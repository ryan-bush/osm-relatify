"""Which way round a route goes, given the side of the road traffic keeps to."""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import pytest

from cython_lib.route import calc_bus_route
from driving_side import build_driving_side_statements, parse_driving_side
from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection, FetchRelationElement
from models.final_route import FinalRoute, FinalRouteWay
from relation_builder import sort_bus_on_path
from route_warnings import check_for_issues

# A road in from the south meets a square loop at B, and a road out leaves west from B.
# Going B, C, D, F is anticlockwise, which keeps the inside of the loop on the left.
A = (50.998, 0.0)
B = (51.0, 0.0)
C = (51.0, 0.002)
D = (51.002, 0.002)
F = (51.002, 0.0)
E = (51.0, -0.002)

CONNECTIONS = {
    'in': ['bc', 'fb', 'out'],
    'out': ['in', 'bc', 'fb'],
    'bc': ['in', 'out', 'fb', 'cd'],
    'cd': ['bc', 'df'],
    'df': ['cd', 'fb'],
    'fb': ['df', 'bc', 'in', 'out'],
}
POINTS = {'in': (A, B), 'out': (B, E), 'bc': (B, C), 'cd': (C, D), 'df': (D, F), 'fb': (F, B)}

ANTICLOCKWISE = ['in', 'bc', 'cd', 'df', 'fb', 'out']
CLOCKWISE = ['in', 'fb', 'df', 'cd', 'bc', 'out']


def _ways(**travel):
    return {
        ElementId(way_id): FetchRelationElement(
            id=ElementId(way_id),
            member=True,
            oneway=False,
            roundabout=False,
            nodes=[0, 1],
            latLngs=list(POINTS[way_id]),
            connectedTo=[ElementId(other) for other in CONNECTIONS[way_id]],
            turn_in_place_start=False,
            turn_in_place_end=False,
            travel=travel.get(way_id),
        )
        for way_id in CONNECTIONS
    }


def _platform(id, lat_lng):
    stop = FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=True,
        latLng=lat_lng,
        tags={'name': f'Stop {id}', 'highway': 'bus_stop', 'public_transport': 'platform'},
        name=f'Stop {id}',
        groupName=f'stop {id}',
        highway='bus_stop',
        public_transport='platform',
    )
    return FetchRelationBusStopCollection(platform=stop, stop=None)


# both inside the loop, 20 m in from the road
INSIDE = [_platform('101', (51.00018, 0.001)), _platform('102', (51.001, 0.0017))]


def _route(driving_side, ways=None):
    ways = ways or _ways()

    async def run():
        with ProcessPoolExecutor(2) as executor:
            return await calc_bus_route(
                ways,
                ElementId('in'),
                ElementId('out'),
                INSIDE,
                {'type': 'route', 'route': 'bus'},
                executor,
                n_processes=2,
                driving_side=driving_side,
            )

    return asyncio.run(run())


def _order(route):
    return [route_way.way.id for route_way in route.ways]


def test_traffic_keeping_left_goes_round_with_the_stops_on_its_left():
    assert _order(_route('left')) == ANTICLOCKWISE


def test_traffic_keeping_right_goes_round_with_the_stops_on_its_right():
    assert _order(_route('right')) == CLOCKWISE


def test_a_direction_set_on_one_way_of_the_loop_decides_the_rest():
    assert _order(_route('left', _ways(cd='backward'))) == CLOCKWISE


def test_the_kerb_side_follows_the_driving_side():
    ways = list(_ways().values())
    [left] = [e for e in sort_bus_on_path(INSIDE[:1], ways, 'left')]
    [right] = [e for e in sort_bus_on_path(INSIDE[:1], ways, 'right')]

    assert left.kerb_side_forward is not None
    assert left.kerb_side_forward != right.kerb_side_forward


def _final(order, ways):
    route_ways = []
    lat_lngs = []
    for way_id in order:
        way = ways[ElementId(way_id)]
        route_ways.append(FinalRouteWay(way=way, reversed_latLngs=False))
        lat_lngs.extend(way.latLngs if not lat_lngs else way.latLngs[1:])
    return FinalRoute(ways=tuple(route_ways), latLngs=tuple(lat_lngs), busStops=tuple(INSIDE), tags={}, members=())


def _far_kerb_warning(route, driving_side, ways):
    checked = check_for_issues(
        route, ways, ElementId('in'), ElementId('out'), list(INSIDE), [], driving_side=driving_side
    )
    return [w for w in checked.warnings if 'far side' in w.message]


def test_a_loop_driven_the_wrong_way_round_is_flagged():
    ways = _ways()
    [warning] = _far_kerb_warning(_final(ANTICLOCKWISE, ways), 'right', ways)

    assert set(warning.extra) == {'101', '102'}
    assert 'keeping right' in warning.message


def test_a_loop_driven_the_right_way_round_is_not_flagged():
    ways = _ways()

    assert _far_kerb_warning(_final(ANTICLOCKWISE, ways), 'left', ways) == []


def test_stops_on_a_one_way_road_are_not_judged():
    ways = _ways()
    ways = {way_id: way if way_id != 'bc' else _one_way(way) for way_id, way in ways.items()}
    [warning] = _far_kerb_warning(_final(ANTICLOCKWISE, ways), 'right', ways)

    assert set(warning.extra) == {'102'}


def _one_way(way):
    return replace(way, oneway=True)


@pytest.mark.parametrize(
    ('tags', 'side'),
    [
        ({'ISO3166-1': 'GB', 'driving_side': 'left'}, 'left'),
        ({'ISO3166-1': 'GB'}, 'left'),
        ({'ISO3166-1:alpha2': 'ie'}, 'left'),
        ({'ISO3166-1': 'FR'}, 'right'),
        ({'ISO3166-1': 'XX', 'driving_side': 'left'}, 'left'),
        ({'name': 'Somewhere'}, None),
    ],
)
def test_the_driving_side_is_read_from_the_country(tags, side):
    assert parse_driving_side([{'tags': tags}]) == side


def test_no_country_says_nothing():
    assert parse_driving_side([]) is None


def test_the_query_asks_for_the_country_around_the_point():
    statements = build_driving_side_statements(53.2, -4.1)

    assert 'is_in(53.2,-4.1)' in statements
    assert 'admin_level=2' in statements
