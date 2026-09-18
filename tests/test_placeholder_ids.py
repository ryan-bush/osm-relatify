import asyncio

import pytest
import xmltodict

from bus_stop_creation import NewBusStop
from models.element_id import ElementId
from models.final_route import FinalRoute
from models.relation_member import RelationMember
from openstreetmap import _parse_created_ids, _resolve_new_stop_areas
from placeholder_ids import RelationPlaceholders
from relation_builder import build_osm_change
from route_masters import RouteMasterChange
from stop_areas import NewStopAreaPlan, StopAreaChange, StopAreaMember

NEW_TAGS = {
    'type': 'route',
    'route': 'bus',
    'public_transport:version': '2',
    'ref': 'C6',
    'name': 'Bus C6: College => Town',
}


def test_ids_are_handed_out_below_the_route_and_never_twice():
    placeholders = RelationPlaceholders()

    assert RelationPlaceholders.ROUTE == -1
    taken = [placeholders.take() for _ in range(3)]

    assert taken == [-2, -3, -4]
    assert RelationPlaceholders.ROUTE not in taken


def test_each_allocator_starts_afresh():
    assert RelationPlaceholders().take() == RelationPlaceholders().take()


@pytest.fixture
def route():
    return FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=NEW_TAGS,
        extraWaysToUpdate=(),
        members=(RelationMember(id=ElementId('201'), type='way', role=''),),
        warnings=(),
    )


class FakeOsm:
    """Nothing here is in OSM yet, so nothing is ever fetched back."""

    async def get_parent_relations(self, element_type, element_id):  # noqa: ARG002
        return []


def _build_everything_at_once(route):
    """A changeset that creates a route, the stops it serves, their stop areas and its master."""
    return _everything_at_once(route).xml


def _everything_at_once(route):
    return asyncio.run(
        build_osm_change(
            None,
            route,
            include_changeset_id=False,
            overpass=None,
            osm=FakeOsm(),
            tags_original=None,
            tags_edited=NEW_TAGS,
            new_stops=[
                NewBusStop(id=-1, lat=51.0, lon=0.0, tags={'name': 'College'}),
                NewBusStop(id=-2, lat=51.1, lon=0.1, tags={'name': 'Town'}),
            ],
            stop_areas=[
                StopAreaChange(name='College', members=[StopAreaMember(type='node', id=-1, role='platform')]),
                StopAreaChange(name='Town', members=[StopAreaMember(type='node', id=-2, role='platform')]),
            ],
            route_master=RouteMasterChange(tags={'ref': 'C6', 'name': 'Bus C6'}),
        )
    )


def _created(osm_change, element_type):
    created = osm_change['create'].get(element_type) or []
    return created if isinstance(created, list) else [created]


# A member pointing at a placeholder nothing creates is refused by the API outright, and a
# placeholder handed out twice is not refused at all: the change simply lands wrong.
def test_everything_created_at_once_gets_an_id_of_its_own(route):
    osm_change = xmltodict.parse(_build_everything_at_once(route))['osmChange']

    relation_ids = [int(r['@id']) for r in _created(osm_change, 'relation')]

    assert len(relation_ids) == 4, 'the route, two stop areas and the route master'
    assert len(set(relation_ids)) == len(relation_ids)
    assert RelationPlaceholders.ROUTE in relation_ids


def test_every_reference_to_a_created_relation_resolves(route):
    osm_change = xmltodict.parse(_build_everything_at_once(route))['osmChange']

    created_relations = {int(r['@id']) for r in _created(osm_change, 'relation')}
    created_nodes = {int(n['@id']) for n in _created(osm_change, 'node')}

    for relation in _created(osm_change, 'relation'):
        members = relation.get('member') or []
        for member in members if isinstance(members, list) else [members]:
            ref = int(member['@ref'])
            if ref >= 0:
                continue

            created = created_relations if member['@type'] == 'relation' else created_nodes
            assert ref in created, f'relation {relation["@id"]} refers to {member["@type"]} {ref}, which nothing creates'


def test_the_master_holds_the_route_being_created(route):
    osm_change = xmltodict.parse(_build_everything_at_once(route))['osmChange']

    master = next(
        r for r in _created(osm_change, 'relation')
        if any(tag['@k'] == 'type' and tag['@v'] == 'route_master' for tag in r['tag'])
    )

    assert [(m['@type'], int(m['@ref'])) for m in [master['member']]] == [('relation', RelationPlaceholders.ROUTE)]


def test_the_stop_areas_hold_the_stops_being_created(route):
    osm_change = xmltodict.parse(_build_everything_at_once(route))['osmChange']

    areas = [
        r for r in _created(osm_change, 'relation')
        if any(tag['@k'] == 'public_transport' and tag['@v'] == 'stop_area' for tag in r['tag'])
    ]

    assert [int(area['member']['@ref']) for area in areas] == [-1, -2]


def _created_relation_ids_in_document_order(osm_change_xml: str) -> list[int]:
    """The order the API reads them in, which xmltodict's parsed dict does not preserve."""
    import re

    create = osm_change_xml.split('<create>', 1)[1].split('</create>', 1)[0]
    return [int(m) for m in re.findall(r'<relation id="(-?\d+)"', create)]


# The API resolves placeholders strictly in document order: a relation referring to one
# that has not been created yet is rejected outright, not reordered. So this is about the
# bytes, not about the parsed tree.
def test_a_relation_is_created_before_anything_refers_to_it(route):
    xml = _build_everything_at_once(route)

    order = _created_relation_ids_in_document_order(xml)
    positions = {id: index for index, id in enumerate(order)}

    for relation in _created(xmltodict.parse(xml)['osmChange'], 'relation'):
        members = relation.get('member') or []
        for member in members if isinstance(members, list) else [members]:
            ref = int(member['@ref'])
            if member['@type'] != 'relation' or ref >= 0:
                continue

            assert positions[ref] < positions[int(relation['@id'])], (
                f'relation {relation["@id"]} is written before relation {ref}, which it refers to'
            )


def test_the_master_comes_after_the_route_it_holds(route):
    order = _created_relation_ids_in_document_order(_build_everything_at_once(route))

    assert order[-1] == -4, 'the master, written last of all'
    assert order.index(RelationPlaceholders.ROUTE) < order.index(-4)


def test_nodes_are_created_before_the_relations_that_group_them(route):
    xml = _build_everything_at_once(route)

    assert xml.index('<node') < xml.index('<relation'), 'a stop area refers to the stops it holds'


# Everything above is about the change going up. These are about reading it back: the
# diffResult names what was created by the placeholder it carried, and nothing else.
def test_the_route_is_read_back_by_its_own_placeholder(route):
    """A stop area is written before the route, so the first created relation is not it."""
    plans = _everything_at_once(route).new_stop_areas
    diff_result = (
        '<?xml version="1.0"?><diffResult version="0.6">'
        '<node old_id="-1" new_id="801"/><node old_id="-2" new_id="802"/>'
        f'<relation old_id="{plans[0].placeholder_id}" new_id="501"/>'
        f'<relation old_id="{plans[1].placeholder_id}" new_id="502"/>'
        f'<relation old_id="{RelationPlaceholders.ROUTE}" new_id="12345"/>'
        '<relation old_id="-4" new_id="600"/>'
        '</diffResult>'
    )

    created = _parse_created_ids(diff_result)

    assert created['relation'][RelationPlaceholders.ROUTE] == 12345


def test_new_stop_areas_are_read_back_with_the_stops_they_hold(route):
    change = _everything_at_once(route)
    [college, town] = change.new_stop_areas
    diff_result = (
        '<?xml version="1.0"?><diffResult version="0.6">'
        '<node old_id="-1" new_id="801"/><node old_id="-2" new_id="802"/>'
        f'<relation old_id="{college.placeholder_id}" new_id="501"/>'
        f'<relation old_id="{town.placeholder_id}" new_id="502"/>'
        '</diffResult>'
    )

    areas = _resolve_new_stop_areas(change.new_stop_areas, _parse_created_ids(diff_result))

    assert [(a.id, a.name, a.members) for a in areas] == [
        # the members were placeholders too, and are the real stops now
        (501, 'College', ['node/801']),
        (502, 'Town', ['node/802']),
    ]


def test_a_stop_area_the_upload_said_nothing_about_is_left_out(route):
    change = _everything_at_once(route)

    assert _resolve_new_stop_areas(change.new_stop_areas, {}) == []


def test_a_stop_already_in_osm_keeps_the_id_it_has():
    plan = NewStopAreaPlan(
        placeholder_id=-2,
        name='The Station',
        members=(
            StopAreaMember(type='node', id=1, role='platform'),
            StopAreaMember(type='node', id=-1, role='stop'),
        ),
        element={},
    )

    [area] = _resolve_new_stop_areas([plan], {'relation': {-2: 501}, 'node': {-1: 801}})

    assert area.members == ['node/1', 'node/801']
