import pytest

from models.bounding_box import BoundingBox
from models.route_master import RouteMaster, RouteMasterRoute
from route_masters import (
    build_route_master_candidates_query,
    describe_members,
    escape_overpass_value,
    is_route_master,
    member_route_ids,
    parse_route_masters,
    parse_routes,
)

BOUNDS = BoundingBox(51.0, -1.5, 51.5, -1.0)


def _master(id=100, tags=None, members=(('relation', 1), ('relation', 2))):
    return {
        'type': 'relation',
        'id': id,
        'tags': tags if tags is not None else {'type': 'route_master', 'route_master': 'bus', 'ref': '71'},
        'members': [{'type': t, 'ref': r, 'role': ''} for t, r in members],
    }


def _route(id=1, tags=None):
    if tags is None:
        tags = {'ref': '71', 'name': 'Bus 71: A => B'}
    return {'type': 'relation', 'id': id, 'tags': tags}


def test_is_route_master():
    assert is_route_master({'type': 'route_master'})
    assert not is_route_master({'type': 'route'})
    assert not is_route_master({})


def test_query_names_the_ref_route_kind_and_area():
    query = build_route_master_candidates_query('71', 'bus', BOUNDS, 30)

    assert '["type"="route"]' in query
    assert '["route"="bus"]' in query
    assert '["ref"="71"]' in query
    assert str(BOUNDS) in query
    # masters are reached through the routes they hold, having no geometry of their own
    assert 'rel(br.r)["type"="route_master"]' in query
    assert '[timeout:30]' in query


def test_query_keeps_the_route_kind_as_tagged():
    # a trolleybus route's siblings are trolleybus routes, not bus ones
    assert '["route"="trolleybus"]' in build_route_master_candidates_query('71', 'trolleybus', BOUNDS, 30)


@pytest.mark.parametrize('ref', ['', '   '])
def test_no_query_without_a_ref(ref):
    assert build_route_master_candidates_query(ref, 'bus', BOUNDS, 30) == ''


def test_no_query_without_a_route_kind():
    assert build_route_master_candidates_query('71', '', BOUNDS, 30) == ''


def test_a_quote_in_a_ref_cannot_end_the_literal():
    assert escape_overpass_value('7"1') == '7\\"1'
    assert escape_overpass_value('7\\1') == '7\\\\1'

    query = build_route_master_candidates_query('7"1', 'bus', BOUNDS, 30)
    assert '["ref"="7\\"1"]' in query


def test_parse_reads_masters_with_their_members():
    masters = parse_route_masters([_master()])

    assert len(masters) == 1
    assert masters[0].id == 100
    assert masters[0].tags['ref'] == '71'
    assert masters[0].members == ['relation/1', 'relation/2']


def test_parse_ignores_what_is_not_a_route_master():
    elements = [
        _master(),
        {'type': 'way', 'id': 5, 'tags': {'type': 'route_master'}},
        _master(id=101, tags={'type': 'route', 'route': 'bus'}),
        _master(id=102, tags={}),
    ]

    assert [master.id for master in parse_route_masters(elements)] == [100]


def test_parse_tolerates_a_master_with_no_members():
    element = _master()
    del element['members']

    assert parse_route_masters([element])[0].members == []


def test_member_route_ids_are_unique_and_in_the_order_first_seen():
    masters = parse_route_masters([
        _master(members=(('relation', 2), ('relation', 1))),
        _master(id=101, members=(('relation', 1), ('relation', 3))),
    ])

    assert member_route_ids(masters) == [2, 1, 3]


def test_member_route_ids_skip_what_is_not_a_relation():
    # a master holding a way is mis-tagged, and not something to go asking OSM about
    masters = parse_route_masters([_master(members=(('way', 9), ('relation', 1)))])

    assert member_route_ids(masters) == [1]


def test_describe_members_fills_in_the_variants_in_member_order():
    masters = parse_route_masters([_master(members=(('relation', 2), ('relation', 1)))])
    routes = parse_routes([_route(id=1), _route(id=2, tags={'ref': '71', 'name': 'Bus 71: B => A'})])

    described = describe_members(masters, routes)[0]

    assert [route.id for route in described.routes] == [2, 1]
    assert described.routes[0].name == 'Bus 71: B => A'
    # the full member list is kept, so a master holding something odd still shows it
    assert described.members == ['relation/2', 'relation/1']


def test_describe_members_leaves_out_members_it_could_not_look_up():
    masters = parse_route_masters([_master(members=(('way', 9), ('relation', 1), ('relation', 7)))])

    described = describe_members(masters, parse_routes([_route(id=1)]))[0]

    assert described.routes == [RouteMasterRoute(id=1, ref='71', name='Bus 71: A => B')]


def test_describe_members_without_any_lookups_leaves_the_variants_empty():
    masters = parse_route_masters([_master()])

    assert describe_members(masters, {})[0].routes == []


def test_parse_routes_trims_and_defaults_missing_tags():
    routes = parse_routes([_route(id=1, tags={'name': '  Bus 71  '}), _route(id=2, tags={})])

    assert routes[1] == RouteMasterRoute(id=1, ref='', name='Bus 71')
    assert routes[2] == RouteMasterRoute(id=2, ref='', name='')


def test_parse_routes_ignores_elements_that_are_not_relations():
    assert parse_routes([{'type': 'way', 'id': 1, 'tags': {}}]) == {}


def test_route_master_defaults_are_not_shared():
    first = RouteMaster(id=1, tags={})
    first.members.append('relation/2')

    assert RouteMaster(id=2, tags={}).members == []
