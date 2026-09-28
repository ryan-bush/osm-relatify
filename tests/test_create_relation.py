import asyncio

import pytest
import xmltodict

from main import PostDownloadOsmChangeModel, make_new_relation_tags
from models.element_id import ElementId
from models.fetch_relation import (
    FetchRelationBusStop,
    FetchRelationBusStopCollection,
    FetchRelationElement,
    PublicTransport,
    find_start_stop_ways,
)
from models.final_route import FinalRoute, FinalRouteWay
from models.relation_member import RelationMember
from openstreetmap import _parse_created_ids
from relation_builder import NEW_RELATION_PLACEHOLDER_ID, build_osm_change, sort_and_upgrade_members

NEW_TAGS = {
    'type': 'route',
    'route': 'bus',
    'public_transport:version': '2',
    'ref': 'C6',
    'name': 'Bus C6: College => Town',
}


def _way(id: str):
    return FetchRelationElement(
        id=ElementId(id),
        member=True,
        oneway=False,
        roundabout=False,
        nodes=[1, 2],
        latLngs=[(51.0, 0.0), (51.0, 0.001)],
        connectedTo=[],
        turn_in_place_start=False,
        turn_in_place_end=False,
    )


@pytest.fixture
def route():
    return FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=NEW_TAGS,
        extraWaysToUpdate=(),
        members=(
            RelationMember(id=ElementId('101'), type='node', role='stop_entry_only'),
            RelationMember(id=ElementId('201'), type='way', role=''),
        ),
        warnings=(),
    )


def _build(route, relation_id):
    """The change document alone, which is what these read."""
    return asyncio.run(
        build_osm_change(
            relation_id,
            route,
            include_changeset_id=False,
            # neither is touched: there are no split ways, and nothing to fetch
            overpass=None,
            osm=None,
            tags_original=None,
            tags_edited=NEW_TAGS,
        )
    ).xml


def test_new_relation_is_created_not_modified(route):
    osm_change = xmltodict.parse(_build(route, None))['osmChange']

    assert 'relation' not in (osm_change.get('modify') or {})

    created = osm_change['create']['relation']
    assert int(created['@id']) == NEW_RELATION_PLACEHOLDER_ID


def test_new_relation_carries_the_edited_tags(route):
    created = xmltodict.parse(_build(route, None))['osmChange']['create']['relation']
    tags = {t['@k']: t['@v'] for t in created['tag']}

    assert tags == NEW_TAGS


def test_new_relation_carries_the_route_members_in_order(route):
    created = xmltodict.parse(_build(route, None))['osmChange']['create']['relation']

    assert [(m['@type'], m['@ref'], m['@role']) for m in created['member']] == [
        ('node', '101', 'stop_entry_only'),
        ('way', '201', ''),
    ]


def test_creating_needs_no_relation_fetch(route):
    # osm is None, so building at all proves nothing was fetched from the server
    assert _build(route, None)


def test_find_start_stop_ways_returns_nothing_for_an_empty_relation():
    ways = {ElementId('1'): _way('1')}

    assert find_start_stop_ways(ways, {1: [ElementId('1')]}, {'members': []}) == (None, None)


def test_make_new_relation_tags_are_the_ptv2_essentials():
    assert make_new_relation_tags('tram') == {
        'type': 'route',
        'route': 'tram',
        'public_transport:version': '2',
    }


@pytest.mark.parametrize(
    ('relation_id', 'expected'),
    [
        (7, 'Updated route: C6 College bus, #7'),
        (None, 'Created route: C6 College bus'),
    ],
)
def test_comment_says_created_when_there_is_no_relation_yet(relation_id, expected):
    model = PostDownloadOsmChangeModel(
        relationId=relation_id,
        route={},
        tags={'name': 'College bus', 'ref': 'C6'},
    )

    assert model.make_comment() == expected


def test_created_ids_are_read_back_from_the_diff_result():
    diff_result = """<?xml version="1.0"?>
    <diffResult version="0.6">
      <way old_id="-1" new_id="900" new_version="1"/>
      <relation old_id="-1" new_id="12345" new_version="1"/>
    </diffResult>"""

    assert _parse_created_ids(diff_result) == {'way': {-1: 900}, 'relation': {-1: 12345}}


def test_every_created_relation_is_named_by_the_placeholder_it_went_up_with():
    """The stop areas of a change are written before the route, so order says nothing."""
    diff_result = """<?xml version="1.0"?>
    <diffResult version="0.6">
      <node old_id="-1" new_id="800" new_version="1"/>
      <relation old_id="-2" new_id="500" new_version="1"/>
      <relation old_id="-1" new_id="12345" new_version="1"/>
    </diffResult>"""

    assert _parse_created_ids(diff_result) == {
        'node': {-1: 800},
        'relation': {-2: 500, -1: 12345},
    }


def test_modified_relation_is_not_mistaken_for_a_created_one():
    diff_result = """<?xml version="1.0"?>
    <diffResult version="0.6">
      <relation old_id="18333921" new_id="18333921" new_version="8"/>
    </diffResult>"""

    assert _parse_created_ids(diff_result) == {}


def test_unparseable_diff_result_is_survivable():
    assert _parse_created_ids('not xml at all') == {}


def _stop(id: str):
    return FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=False,
        latLng=(51.0, 0.0),
        tags={},
        name='Stop',
        groupName='Stop',
        highway='bus_stop',
        public_transport=PublicTransport.PLATFORM,
    )


def test_roles_are_assigned_from_scratch_without_an_existing_relation():
    """The empty member list of a new relation still yields correct PTv2 roles."""
    route = FinalRoute(
        ways=(FinalRouteWay(way=_way('201'), reversed_latLngs=False),),
        latLngs=(),
        busStops=(
            FetchRelationBusStopCollection(platform=_stop('101'), stop=None),
            FetchRelationBusStopCollection(platform=_stop('102'), stop=None),
            FetchRelationBusStopCollection(platform=_stop('103'), stop=None),
        ),
        tags=NEW_TAGS,
        extraWaysToUpdate=(),
        members=(),
        warnings=(),
    )

    # no relation exists, so there are no members to carry roles over from
    members = sort_and_upgrade_members(route, []).members

    assert [(str(m.id), m.role) for m in members] == [
        ('101', 'platform_entry_only'),
        ('102', 'platform'),
        ('103', 'platform_exit_only'),
        ('201', ''),
    ]


ROUNDTRIP_TAGS = NEW_TAGS | {
    'name': 'Bus 60: Amlwch → Carreglefn → Amlwch',
    'roundtrip': 'yes',
}


def _stop_position(id: str, latLng: tuple[float, float]):  # noqa: N803
    return FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=False,
        latLng=latLng,
        tags={},
        name='Stop',
        groupName='Stop',
        highway=None,
        public_transport=PublicTransport.STOP_POSITION,
    )


def _platform(id: str, latLng: tuple[float, float]):  # noqa: N803
    return FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=False,
        latLng=latLng,
        tags={},
        name='Stop',
        groupName='Stop',
        highway='bus_stop',
        public_transport=PublicTransport.PLATFORM,
    )


def _roundtrip_route(latLngs: tuple[tuple[float, float], ...]):  # noqa: N803
    """A three-stop loop whose first stop sits at (51.0, 0.0)."""
    return FinalRoute(
        ways=(FinalRouteWay(way=_way('201'), reversed_latLngs=False),),
        latLngs=latLngs,
        busStops=(
            FetchRelationBusStopCollection(
                platform=_platform('101', (51.0, 0.0)), stop=_stop_position('111', (51.0, 0.0))
            ),
            FetchRelationBusStopCollection(
                platform=_platform('102', (51.0, 0.001)), stop=_stop_position('112', (51.0, 0.001))
            ),
            FetchRelationBusStopCollection(
                platform=_platform('103', (51.0, 0.002)), stop=_stop_position('113', (51.0, 0.002))
            ),
        ),
        tags=ROUNDTRIP_TAGS,
        extraWaysToUpdate=(),
        members=(),
        warnings=(),
    )


def test_circular_route_repeats_the_stop_it_returns_to():
    """A loop back to its first stop lists that stop again at the end, as PTv2 asks."""
    route = _roundtrip_route(((51.0, 0.0), (51.0, 0.002), (51.0, 0.0)))

    members = sort_and_upgrade_members(route, []).members

    assert [(str(m.id), m.role) for m in members] == [
        ('111', 'stop'),
        ('101', 'platform'),
        ('112', 'stop'),
        ('102', 'platform'),
        ('113', 'stop'),
        ('103', 'platform'),
        ('111', 'stop'),
        ('101', 'platform'),
        ('201', ''),
    ]


def test_loop_ending_short_of_the_first_stop_repeats_nothing():
    """The ways close up before reaching the first stop, so every stop is served once.

    Repeating one there would put a stop position out of order, which OSM Inspector
    reports as "stop position in wrong order".
    """
    route = _roundtrip_route(((51.0, 0.0), (51.0, 0.002), (51.0, 0.004)))

    members = sort_and_upgrade_members(route, []).members

    assert [(str(m.id), m.role) for m in members] == [
        ('111', 'stop'),
        ('101', 'platform'),
        ('112', 'stop'),
        ('102', 'platform'),
        ('113', 'stop'),
        ('103', 'platform'),
        ('201', ''),
    ]
