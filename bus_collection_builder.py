from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from itertools import combinations
from math import radians
from operator import itemgetter

import networkx as nx
import numpy as np
from rapidfuzz.fuzz import token_ratio
from rapidfuzz.process import extract
from scipy.optimize import linear_sum_assignment
from sentry_sdk import trace
from sklearn.neighbors import BallTree

from config import BUS_COLLECTION_SEARCH_AREA, STOP_AREA_SEARCH_AREA
from cython_lib.geoutils import haversine_distance, radians_tuple
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection, PublicTransport
from utils import extract_numbers, normalize_name


@trace
def build_bus_stop_collections(bus_stops: Sequence[FetchRelationBusStop]) -> list[FetchRelationBusStopCollection]:
    # 1. group by area
    # 2. group by name in area
    # 3. discard unnamed if in area with named
    # 4. for each named group, pick best platform and best stop
    if not bus_stops:
        return []

    search_latLng = BUS_COLLECTION_SEARCH_AREA / 111_111
    search_latLng_rad = radians(search_latLng)

    bus_stops_coordinates = tuple(radians_tuple(bus_stop.latLng) for bus_stop in bus_stops)
    bus_stops_tree = BallTree(bus_stops_coordinates, metric='haversine')

    G = nx.Graph()  # noqa: N806

    query_indices, _ = bus_stops_tree.query_radius(
        bus_stops_coordinates,
        r=search_latLng_rad,
        return_distance=True,
        sort_results=True,
    )

    # group by area
    for i in range(len(bus_stops)):
        G.add_edge(i, i)
    for query_group in query_indices:
        for i, j in combinations(query_group, 2):
            G.add_edge(i, j)

    collections: list[FetchRelationBusStopCollection] = []

    for component in nx.connected_components(G):
        # make area group from member indices
        area_group = tuple(bus_stops[member_index] for member_index in component)

        # group by name in area
        name_groups: dict[str, list[FetchRelationBusStop]] = defaultdict(list)

        for bus_stop in area_group:
            name_groups[bus_stop.groupName].append(bus_stop)

        # discard unnamed if in area with named
        if len(name_groups) > 1 and (unnamed := name_groups.get('')):
            unnamed = [s for s in unnamed if s.public_transport == PublicTransport.PLATFORM]  # never discard platforms
            if unnamed:
                name_groups[''] = unnamed
            else:
                name_groups.pop('')

        # expand short-name groups to long-name groups if possible
        if len(name_groups) > 1:
            expand_data = {
                expand_key: extract(expand_key, name_groups.keys(), scorer=token_ratio, score_cutoff=89)
                for expand_key in name_groups
            }

            expand_data = sorted(
                expand_data.items(),
                key=lambda t: (sum(map(itemgetter(1), t[1])), -len(t[0])),
                reverse=True,
            )
            # pprint(expand_data)

            for expand_key, target_data in expand_data:
                expand_key_n = extract_numbers(expand_key)
                expand_group = name_groups[expand_key]
                expand_group_public_transports = {bus_stop.public_transport for bus_stop in expand_group}
                targets = []

                for target_key, name_score, _ in target_data:
                    if target_key == expand_key:
                        continue

                    # expand non-numeric to numeric
                    #  or
                    # expand numeric to numeric when equal
                    if not expand_key_n.issubset(extract_numbers(target_key)):
                        continue

                    target_group = name_groups.get(target_key)

                    # skip if target_group was expanded/popped
                    if not target_group:
                        continue

                    target_group_public_transports = (bus_stop.public_transport for bus_stop in target_group)

                    # expand only if target doesn't share any public_transport types
                    if expand_group_public_transports.intersection(target_group_public_transports):
                        continue

                    # where the group stands now, so a stop moved into it does not decide
                    # where the next one goes
                    targets.append((target_key, name_score, target_group, tuple(s.latLng for s in target_group)))

                if not targets:
                    continue

                # Several groups can match the same short name: the two sides of a road
                # are each named for the stop standing on them, and the stop position
                # between them carries the bare name. Putting the whole group into every
                # one of them would tell both sides the bus halts in the same spot, and
                # would leave the far side looking as though it already had a stop
                # position of its own, so each stop goes to the nearest group instead.
                for bus_stop in expand_group:
                    target_key, name_score, target_group, _ = min(
                        targets,
                        key=lambda t, bus_stop=bus_stop: min(
                            haversine_distance(bus_stop.latLng, latLng) for latLng in t[3]
                        ),
                    )

                    print(
                        f'[COLL] [{name_score:5.1f}] Expanded {expand_key!r} to {target_key!r}, '
                        f'ID={bus_stop.nice_id!r}'
                    )
                    target_group.append(bus_stop)

                name_groups.pop(expand_key)

        # for each named group, pick best platform and best stop
        for name_key, name_group in name_groups.items():
            platforms: list[FetchRelationBusStop] = []
            stops: list[FetchRelationBusStop] = []

            for bus_stop in name_group:
                if bus_stop.public_transport == PublicTransport.PLATFORM:
                    platforms.append(bus_stop)
                elif bus_stop.public_transport == PublicTransport.STOP_POSITION:
                    stops.append(bus_stop)
                else:
                    raise NotImplementedError(f'Unknown public transport type: {bus_stop.public_transport}')

            # for deterministic results
            platforms.sort(key=lambda p: p.id)
            stops.sort(key=lambda s: s.id)

            platforms_explicit, platforms_implicit = _pick_best(platforms)
            stops_explicit, stops_implicit = _pick_best(stops)

            if platforms_explicit and stops_explicit:
                collection_name = next(s.name for s in name_group if s.groupName == name_key)
                print(
                    f'🚧 Warning: Invalid explicit platforms and stops for {collection_name!r}, '
                    f'ID={stops_explicit[0].nice_id!r}'
                )

            if platforms_explicit:
                for platform, stop in zip(platforms_explicit, _assign(platforms_explicit, stops), strict=True):
                    collections.append(FetchRelationBusStopCollection(platform=platform, stop=stop))
                continue

            if stops_explicit:
                for stop, platform in zip(stops_explicit, _assign(stops_explicit, platforms), strict=True):
                    collections.append(FetchRelationBusStopCollection(platform=platform, stop=stop))
                continue

            if platforms_implicit and stops_implicit:
                for platform, stop in zip(platforms_implicit, _assign(platforms_implicit, stops), strict=True):
                    collections.append(FetchRelationBusStopCollection(platform=platform, stop=stop))
                continue

            if platforms_implicit:  # and not stops_implicit
                collections.extend(
                    FetchRelationBusStopCollection(platform=platform, stop=None)
                    for platform in platforms_implicit
                )
                continue

            if stops_implicit:  # and not platforms_implicit
                collections.extend(
                    FetchRelationBusStopCollection(platform=None, stop=stop) for stop in stops_implicit
                )
                continue

    return _pair_by_distance_within_places(assign_stop_area_groups(collections))


def _pair_by_distance_within_places(
    collections: list[FetchRelationBusStopCollection],
) -> list[FetchRelationBusStopCollection]:
    """
    Within one place, give each stop position to the platform it stands beside.

    Collections are put together by name, and a stop position carries the bare name of
    its place while a platform often carries a ref alongside it. The one whose name has
    no ref therefore matches the stop position exactly and takes it, whichever side of
    the road each of them is actually on: at Bladen Close that paired the stop position
    with a platform 20 m away over the one 5 m away, and left the near side looking as
    though it had no stop position to add.

    The stops of one place are already worked out for stop areas, which goes by the name
    on the sign and so reaches across those groups. Settling the pairs again over that
    wider group is what puts each one back on its own side.
    """
    by_group: dict[int, list[int]] = defaultdict(list)

    for i, collection in enumerate(collections):
        # a stop position of its own is nobody's to move; only platforms take one
        if collection.groupId >= 0 and collection.platform is not None:
            by_group[collection.groupId].append(i)

    result = list(collections)

    for indices in by_group.values():
        if len(indices) < 2:
            continue

        stops = [result[i].stop for i in indices if result[i].stop is not None]
        if not stops:
            continue

        platforms = [result[i].platform for i in indices]

        distance_matrix = np.zeros((len(platforms), len(stops)))
        for i, platform in enumerate(platforms):
            for j, stop in enumerate(stops):
                distance_matrix[i, j] = haversine_distance(platform.latLng, stop.latLng)

        # never more stops than platforms, one each at most, so every stop keeps a place
        row_ind, col_ind = linear_sum_assignment(distance_matrix)
        assigned = {indices[i]: stops[j] for i, j in zip(row_ind, col_ind, strict=False)}

        for i in indices:
            result[i] = replace(result[i], stop=assigned.get(i))

    return result


def _pick_best(
    elements: list[FetchRelationBusStop],
) -> tuple[tuple[FetchRelationBusStop, ...], tuple[FetchRelationBusStop, ...]]:
    if not elements:
        return (), ()
    elements_explicit = tuple(e for e in elements if e.highway == 'bus_stop')
    elements_implicit = tuple(e for e in elements if e.highway != 'bus_stop')
    return elements_explicit, elements_implicit


@trace
def _assign(
    primary: Sequence[FetchRelationBusStop],
    elements: Sequence[FetchRelationBusStop],
) -> list[FetchRelationBusStop | None]:
    """
    Pair each of `primary` with the element that goes with it, closest pairs first.

    No element is ever given to two of them. A stop position sits on one side of a road
    and serves the platform on that side, so handing the one node to both platforms of a
    place would say the buses in each direction halt in the same spot - and would leave
    the platform that has no stop position of its own looking as though it already had
    one. Whichever of `primary` the assignment cannot pair off gets None instead, which
    is what offers the mapper a stop position to add.
    """
    if not elements:
        return [None] * len(primary)

    distance_matrix = np.zeros((len(primary), len(elements)))
    for i, p in enumerate(primary):
        for j, e in enumerate(elements):
            distance_matrix[i, j] = haversine_distance(p.latLng, e.latLng)

    # the Hungarian algorithm, which pairs off as many as it can for the least total
    # distance; on a lopsided matrix it simply leaves the extras unpaired
    row_ind, col_ind = linear_sum_assignment(distance_matrix)

    result: list[FetchRelationBusStop | None] = [None] * len(primary)
    for i, j in zip(row_ind, col_ind, strict=False):
        result[i] = elements[j]

    return result


def _stop_area_key(collection: FetchRelationBusStopCollection) -> str:
    """
    What makes two stops part of one place: the name on the sign.

    Deliberately the `name` tag rather than the collection's display name, which has the
    stop letter appended — "The Orchards A" and "The Orchards B" are the two sides of one
    road, and grouping by display name would never put them together.
    """
    for stop in (collection.platform, collection.stop):
        if stop is not None and (name := stop.tags.get('name', '').strip()):
            return normalize_name(name, lower=True, special=True, whitespace=True)

    return ''


def assign_stop_area_groups(
    collections: list[FetchRelationBusStopCollection],
    search_area: float = STOP_AREA_SEARCH_AREA,
) -> list[FetchRelationBusStopCollection]:
    """
    Mark which collections a stop_area relation would bring together.

    Not the same grouping as the collections themselves, which pair a platform with the
    stop position serving it and so keep to a tight radius. The stops of one place can be
    much further apart, so this reaches further and goes by name alone. Stops with no name
    are left ungrouped, as there is nothing to say they belong together.
    """
    named = [(i, collection) for i, collection in enumerate(collections) if _stop_area_key(collection)]
    if not named:
        return collections

    coordinates = tuple(radians_tuple(collection.best.latLng) for _, collection in named)
    tree = BallTree(coordinates, metric='haversine')
    nearby = tree.query_radius(coordinates, r=radians(search_area / 111_111))

    G = nx.Graph()  # noqa: N806

    for position, (index, collection) in enumerate(named):
        G.add_node(index)

        for other in nearby[position]:
            if other == position:
                continue

            other_index, other_collection = named[other]
            if _stop_area_key(collection) == _stop_area_key(other_collection):
                G.add_edge(index, other_index)

    group_of: dict[int, int] = {}

    for group_id, component in enumerate(nx.connected_components(G)):
        for index in component:
            group_of[index] = group_id

    return [replace(collection, groupId=group_of.get(i, -1)) for i, collection in enumerate(collections)]
