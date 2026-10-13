import asyncio
from concurrent.futures import ProcessPoolExecutor

from cython_lib import route as route_module
from cython_lib.route import build_graph, calc_bus_route, follow_stop_plan, route_stop_visits
from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection
from models.final_route import FinalRoute
from models.relation_member import RelationMember
from models.stop_plan import StopPlanEntry
from relation_builder import SortedBusEntry, sort_and_upgrade_members
from route_warnings import _check_for_bus_stop_not_reached
from tests.test_route_search import _assert_continuous, _junction_loops


def _collection(stop_id, lat_lng=(51.0, 0.0)):
    platform = FetchRelationBusStop(
        id=ElementId(stop_id),
        type='node',
        member=True,
        latLng=lat_lng,
        tags={'name': f'Stop {stop_id}', 'highway': 'bus_stop', 'public_transport': 'platform'},
        name=f'Stop {stop_id}',
        groupName=f'stop {stop_id}',
        highway='bus_stop',
        public_transport='platform',
    )
    return FetchRelationBusStopCollection(platform=platform, stop=None)


# the loop a-b-c serves B, the loop d-e serves E; both leave the junction and come back to it
STOP_B = _collection('B', (51.0005, 0.00105))
STOP_E = _collection('E', (50.9994, -0.0007))
STOP_IN = _collection('I', (50.9995, 0.00005))


def _bus_map(*placements):
    return {
        ElementId(way_id): [
            SortedBusEntry(
                bus_stop_collection=collection,
                sort_index=0,
                neighbor_id=ElementId(way_id),
                distance_from_neighbor=5,
                kerb_side_forward=None,
            )
        ]
        for way_id, collection in placements
    }


def _path(way_ids):
    return tuple(route_module.GraphKey(ElementId(way_id), route_module.BOOL_START) for way_id in way_ids)


def _follow(way_ids, plan, bus_map):
    ways = _junction_loops()
    best_path = route_module.BestPath.zero()._replace(path=_path(way_ids))

    result = follow_stop_plan(best_path, build_graph(ways), ways, bus_map, [ElementId(p) for p in plan])

    _assert_continuous(result.path, ways)
    return [str(key.way_id) for key in result.path]


def test_a_loop_is_driven_on_the_pass_that_serves_the_stops_in_the_mapper_order():
    """Through Worton, the lane to Marston can be served on the way in or on the way out."""
    bus_map = _bus_map(('b', STOP_B), ('e', STOP_E))

    assert _follow(['in', 'a', 'b', 'c', 'd', 'e', 'out'], ['E', 'B'], bus_map) == [
        'in',
        'd',
        'e',
        'a',
        'b',
        'c',
        'out',
    ]


def test_a_route_already_in_the_mapper_order_is_left_alone():
    bus_map = _bus_map(('b', STOP_B), ('e', STOP_E))

    assert _follow(['in', 'a', 'b', 'c', 'd', 'e', 'out'], ['B', 'E'], bus_map) == [
        'in',
        'a',
        'b',
        'c',
        'd',
        'e',
        'out',
    ]


def test_an_order_the_ways_cannot_drive_keeps_the_route():
    # I is on the way in, so nothing can be moved in front of it
    bus_map = _bus_map(('in', STOP_IN), ('b', STOP_B))

    assert _follow(['in', 'a', 'b', 'c', 'out'], ['B', 'I'], bus_map) == ['in', 'a', 'b', 'c', 'out']


def test_a_stop_served_on_every_lap_is_listed_once_unless_the_mapper_asks_for_more():
    bus_map = _bus_map(('b', STOP_B))
    path = _path(['in', 'a', 'b', 'c', 'a', 'b', 'c', 'out'])

    assert route_stop_visits(path, bus_map, {}) == [ElementId('B')]
    assert route_stop_visits(path, bus_map, {ElementId('B'): 2}) == [ElementId('B'), ElementId('B')]
    # never more often than the route goes past it
    assert route_stop_visits(path, bus_map, {ElementId('B'): 3}) == [ElementId('B'), ElementId('B')]


def _route(stops, tags=None):
    return FinalRoute(ways=(), latLngs=(), busStops=tuple(stops), tags=tags or {})


def _roles(route):
    return [member.role for member in route.members]


def test_ends_are_entry_and_exit_only_unless_the_mapper_says_otherwise():
    stops = [_collection('1'), _collection('2'), _collection('3')]

    automatic = sort_and_upgrade_members(_route(stops), [])
    assert _roles(automatic) == ['platform_entry_only', 'platform', 'platform_exit_only']
    assert automatic.busStopRoles == ('entry_only', 'both', 'exit_only')

    plan = [
        StopPlanEntry(id=ElementId('1'), role='both'),
        StopPlanEntry(id=ElementId('2'), role='exit_only'),
        StopPlanEntry(id=ElementId('3')),
    ]
    arranged = sort_and_upgrade_members(_route(stops), [], plan)
    assert _roles(arranged) == ['platform', 'platform_exit_only', 'platform_exit_only']
    assert arranged.busStopRoles == ('both', 'exit_only', 'exit_only')


def test_a_role_already_in_the_relation_is_kept_and_shown():
    stops = [_collection('1'), _collection('2'), _collection('3')]
    relation_members = [RelationMember(id=ElementId('2'), type='node', role='platform_entry_only')]

    route = sort_and_upgrade_members(_route(stops), relation_members)

    assert route.busStopRoles == ('entry_only', 'entry_only', 'exit_only')


def test_each_visit_to_a_stop_served_twice_has_its_own_role():
    stops = [_collection('1'), _collection('2'), _collection('1')]
    plan = [
        StopPlanEntry(id=ElementId('1'), role='entry_only'),
        StopPlanEntry(id=ElementId('2')),
        StopPlanEntry(id=ElementId('1'), role='exit_only'),
    ]

    route = sort_and_upgrade_members(_route(stops), [], plan)

    assert _roles(route) == ['platform_entry_only', 'platform', 'platform_exit_only']


def test_a_roundtrip_serving_its_terminus_again_does_not_list_it_a_third_time():
    stops = [_collection('1'), _collection('2'), _collection('1')]
    route = FinalRoute(ways=(), latLngs=((51.0, 0.0),), busStops=tuple(stops), tags={'roundtrip': 'yes'})

    route = sort_and_upgrade_members(route, [])

    assert [str(member.id) for member in route.members] == ['1', '2', '1']


def test_a_stop_served_twice_does_not_hide_one_never_reached():
    collections = [_collection('1'), _collection('2'), _collection('3')]
    route = _route([collections[0], collections[1], collections[0]])

    warning = _check_for_bus_stop_not_reached(route, collections)

    assert warning is not None
    assert warning.extra == (ElementId('3'),)


def test_the_route_follows_the_mapper_order_end_to_end():
    ways = _junction_loops()

    def run(stop_plan):
        async def calculate():
            with ProcessPoolExecutor(2) as executor:
                return await calc_bus_route(
                    ways,
                    ElementId('in'),
                    ElementId('out'),
                    [STOP_B, STOP_E],
                    {'type': 'route', 'route': 'bus'},
                    executor,
                    n_processes=2,
                    stop_plan=stop_plan,
                )

        route = asyncio.run(calculate())
        return [str(collection.best.id) for collection in route.busStops]

    automatic = run(())
    reversed_plan = [StopPlanEntry(id=ElementId(stop_id)) for stop_id in reversed(automatic)]

    assert sorted(automatic) == ['B', 'E']
    assert run(reversed_plan) == automatic[::-1]
