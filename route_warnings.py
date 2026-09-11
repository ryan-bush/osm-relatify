from collections.abc import Sequence
from dataclasses import replace
from itertools import pairwise, zip_longest
from math import atan2, cos, degrees, hypot, radians

from sentry_sdk import trace

from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStopCollection, FetchRelationElement
from models.final_route import FinalRoute, FinalRouteWarning, WarningSeverity
from models.relation_member import RelationMember
from relation_builder import sort_bus_on_path

# NaPTAN gives the direction buses travel when calling at a stop as a compass point
_COMPASS_DEGREES = {'N': 0, 'NE': 45, 'E': 90, 'SE': 135, 'S': 180, 'SW': 225, 'W': 270, 'NW': 315}
# compass points are 45° apart and roads bend, so only a clearly opposite heading counts
_OPPOSITE_HEADING_ANGLE = 120  # degrees
# a route can pass a stop in both directions; stretches this much further away than the
# nearest still count as passing it
_PASSING_TOLERANCE = 20  # meters
# a stop further than this from the route is reported as far away instead
_PASSING_MAX_DISTANCE = 120  # meters


@trace
def _check_for_unused_ways(route: FinalRoute, ways: dict[ElementId, FetchRelationElement]) -> FinalRouteWarning | None:
    way_ids = set(ways.keys())
    way_ids.difference_update(route_way.way.id for route_way in route.ways)
    if way_ids:
        return FinalRouteWarning(
            severity=WarningSeverity.HIGH,
            message='Some ways are not used',
            extra=tuple(way_ids),
        )


@trace
def _check_for_end_not_reached(route: FinalRoute, end_way: ElementId) -> FinalRouteWarning | None:
    if end_way not in (route_way.way.id for route_way in route.ways):
        return FinalRouteWarning(
            severity=WarningSeverity.HIGH,
            message='The stop point is not reached',
        )


@trace
def _check_for_bus_stop_far_away(
    route: FinalRoute,
    bus_stop_collections: list[FetchRelationBusStopCollection],
) -> FinalRouteWarning | None:
    threshold = 120  # meters
    sorted_ways = tuple(route_way.way for route_way in route.ways)
    sorted_bus_stops = sort_bus_on_path(bus_stop_collections, sorted_ways)
    far_way_bus_stops = tuple(stop for stop in sorted_bus_stops if stop.distance_from_neighbor > threshold)
    if far_way_bus_stops:
        return FinalRouteWarning(
            severity=WarningSeverity.LOW,
            message='Some stops are far away',
            extra=tuple(stop.bus_stop_collection.best.id for stop in far_way_bus_stops),
        )


@trace
def _check_for_bus_stop_not_reached(
    route: FinalRoute,
    bus_stop_collections: list[FetchRelationBusStopCollection],
) -> FinalRouteWarning | None:
    if len(route.busStops) == len(bus_stop_collections):
        return None
    not_reached_bus_stop_ids: set[ElementId] = {collection.best.id for collection in bus_stop_collections}
    not_reached_bus_stop_ids.difference_update(collection.best.id for collection in route.busStops)
    return FinalRouteWarning(
        severity=WarningSeverity.HIGH,
        message='Some stops are not reached',
        extra=tuple(not_reached_bus_stop_ids),
    )


@trace
def _check_for_bus_stop_inactive_in_naptan(
    route: FinalRoute,
    inactive_naptan_codes: frozenset[str],
) -> FinalRouteWarning | None:
    inactive = tuple(
        collection.best.id for collection in route.busStops if collection.atco_codes & inactive_naptan_codes
    )
    if inactive:
        return FinalRouteWarning(
            severity=WarningSeverity.LOW,
            message='Some stops are inactive in NaPTAN',
            extra=inactive,
        )


def _naptan_bearing(collection: FetchRelationBusStopCollection) -> int | None:
    for stop in (collection.platform, collection.stop):
        if stop is not None and (bearing := stop.tags.get('naptan:Bearing', '').strip().upper()) in _COMPASS_DEGREES:
            return _COMPASS_DEGREES[bearing]
    return None


def _headings_passing(lat_lng: tuple[float, float], route_lat_lngs: Sequence[tuple[float, float]]) -> list[float]:
    """The route's headings where it passes a point, in degrees clockwise from north."""
    lat0, lon0 = lat_lng
    # flat enough over the few hundred metres that matter
    x_scale = 111_320 * cos(radians(lat0))
    y_scale = 110_540

    passes: list[tuple[float, float]] = []

    for (lat_a, lon_a), (lat_b, lon_b) in pairwise(route_lat_lngs):
        ax, ay = (lon_a - lon0) * x_scale, (lat_a - lat0) * y_scale
        dx, dy = (lon_b - lon_a) * x_scale, (lat_b - lat_a) * y_scale

        length_sq = dx * dx + dy * dy
        if not length_sq:
            continue

        # the closest point of the segment to the stop, which sits at the origin
        t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
        passes.append((hypot(ax + t * dx, ay + t * dy), degrees(atan2(dx, dy)) % 360))

    if not passes:
        return []

    nearest = min(distance for distance, _ in passes)
    if nearest > _PASSING_MAX_DISTANCE:
        return []

    return [heading for distance, heading in passes if distance <= nearest + _PASSING_TOLERANCE]


def _angle_between(a: float, b: float) -> float:
    difference = abs(a - b) % 360
    return min(difference, 360 - difference)


@trace
def _check_for_bus_stop_serving_other_direction(route: FinalRoute) -> FinalRouteWarning | None:
    """Catches the stop across the road being picked, using the bearing NaPTAN gives it."""
    other_direction = []

    for collection in route.busStops:
        bearing = _naptan_bearing(collection)
        if bearing is None:
            continue

        headings = _headings_passing(collection.best.latLng, route.latLngs)

        if headings and all(_angle_between(bearing, heading) > _OPPOSITE_HEADING_ANGLE for heading in headings):
            other_direction.append(collection.best.id)

    if other_direction:
        return FinalRouteWarning(
            severity=WarningSeverity.LOW,
            message='Some stops serve the other direction',
            extra=tuple(other_direction),
        )


@trace
def _check_for_not_enough_bus_stops(route: FinalRoute) -> FinalRouteWarning | None:
    if len(route.busStops) < 2:
        return FinalRouteWarning(
            severity=WarningSeverity.HIGH,
            message='The route has less than 2 stops',
        )


@trace
def _check_for_roundtrip_not_roundtrip(route: FinalRoute) -> FinalRouteWarning | None:
    if route.roundtrip and route.latLngs and route.latLngs[0] != route.latLngs[-1]:
        return FinalRouteWarning(
            severity=WarningSeverity.LOW,
            message='The route is not a valid roundtrip',
        )


@trace
def _check_for_members_unchanged(route: FinalRoute, relation_members: list[RelationMember]) -> FinalRouteWarning | None:
    for route_member, relation_member in zip_longest(route.members, relation_members):
        if route_member != relation_member:
            return None
    return FinalRouteWarning(
        severity=WarningSeverity.UNCHANGED,
        message='The route is unchanged',
    )


@trace
def check_for_issues(
    route: FinalRoute,
    ways: dict[ElementId, FetchRelationElement],
    start_way: ElementId,  # noqa: ARG001
    end_way: ElementId,
    bus_stop_collections: list[FetchRelationBusStopCollection],
    relation_members: list[RelationMember],
    # empty unless NaPTAN is enabled
    inactive_naptan_codes: frozenset[str] = frozenset(),
) -> FinalRoute:
    warnings = (
        _check_for_unused_ways(route, ways),
        _check_for_end_not_reached(route, end_way),
        _check_for_bus_stop_far_away(route, bus_stop_collections),
        _check_for_bus_stop_not_reached(route, bus_stop_collections),
        _check_for_bus_stop_inactive_in_naptan(route, inactive_naptan_codes),
        _check_for_bus_stop_serving_other_direction(route),
        _check_for_not_enough_bus_stops(route),
        _check_for_roundtrip_not_roundtrip(route),
        _check_for_members_unchanged(route, relation_members),
    )
    sorted_warnings = tuple(sorted(filter(None, warnings), key=lambda warning: warning.severity.value, reverse=True))
    return replace(route, warnings=sorted_warnings)
