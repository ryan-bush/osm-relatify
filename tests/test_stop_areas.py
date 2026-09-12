import asyncio

import pytest
import xmltodict
from fastapi import HTTPException

from models.final_route import FinalRoute
from relation_builder import build_osm_change
from stop_areas import (
    StopAreaChange,
    StopAreaMember,
    build_new_stop_area_relations,
    build_stop_area_modifications,
    build_stop_areas_query,
    parse_stop_areas,
)

BUS_TAGS = {'type': 'route', 'route': 'bus', 'public_transport:version': '2', 'name': 'Bus 12'}


def _member(type='node', id=1, role='platform'):
    return StopAreaMember(type=type, id=id, role=role)


def _change(id=None, name='The Station', members=None):
    return StopAreaChange(id=id, name=name, members=members or [_member(id=1), _member(id=2, role='stop')])


class FakeOsm:
    """Stands in for the OSM API, returning the stop area relations being added to."""

    def __init__(self, relations: dict[int, dict]):
        self._relations = relations
        self.requested = None

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        self.requested = tuple(relation_ids)
        return [self._relations[int(i)] for i in relation_ids]


_STOP_AREA_TAGS = {'type': 'public_transport', 'public_transport': 'stop_area', 'name': 'The Station'}


def _relation(id=99, tags=None, members=()):
    return {
        '@id': str(id),
        '@version': '4',
        '@timestamp': '2026-01-01T00:00:00Z',
        '@user': 'someone',
        '@uid': '1',
        'tag': [{'@k': k, '@v': v} for k, v in (tags or _STOP_AREA_TAGS).items()],
        'member': [{'@type': t, '@ref': str(r), '@role': role} for t, r, role in members],
    }


class TestBuildQuery:
    def test_asks_for_the_parents_of_both_kinds_of_stop(self):
        query = build_stop_areas_query([2, 1], [7], 30)

        assert 'node(id:1,2)' in query
        assert 'way(id:7)' in query
        assert '"public_transport"="stop_area"' in query

    def test_leaves_out_a_kind_there_are_none_of(self):
        # an empty id list is a syntax error in Overpass
        assert 'way(id:' not in build_stop_areas_query([1], [], 30)

    def test_no_stops_means_no_query_at_all(self):
        assert build_stop_areas_query([], [], 30) == ''


class TestParse:
    def test_reads_the_relations_and_their_members(self):
        [area] = parse_stop_areas(
            [
                {
                    'type': 'relation',
                    'id': 15683238,
                    'tags': {'type': 'public_transport', 'public_transport': 'stop_area', 'name': 'The Station'},
                    'members': [{'type': 'node', 'ref': 1, 'role': 'platform'}],
                }
            ]
        )

        assert area.id == 15683238
        assert area.name == 'The Station'
        assert area.members == ['node/1']

    def test_ignores_anything_that_is_not_a_stop_area(self):
        elements = [
            {'type': 'relation', 'id': 1, 'tags': {'type': 'route', 'route': 'bus'}, 'members': []},
            {'type': 'node', 'id': 2},
        ]

        assert parse_stop_areas(elements) == []


class TestBuildNewStopAreaRelations:
    def test_creates_the_relation_with_its_tags_and_members(self):
        [relation] = build_new_stop_area_relations([_change()], created_node_ids=set())

        assert relation['@id'] == -2, 'below the route relation, which takes -1'
        assert {t['@k']: t['@v'] for t in relation['tag']} == {
            'type': 'public_transport',
            'public_transport': 'stop_area',
            'name': 'The Station',
        }
        assert [(m['@type'], m['@ref'], m['@role']) for m in relation['member']] == [
            ('node', 1, 'platform'),
            ('node', 2, 'stop'),
        ]

    def test_each_new_area_gets_its_own_placeholder(self):
        relations = build_new_stop_area_relations([_change(), _change(name='Market Square')], set())
        assert [r['@id'] for r in relations] == [-2, -3]

    def test_an_existing_area_is_not_created_again(self):
        assert build_new_stop_area_relations([_change(id=99)], set()) == []

    def test_a_new_area_needs_a_name(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_area_relations([_change(name='  ')], set())

        assert e.value.status_code == 400
        assert 'needs a name' in e.value.detail

    def test_it_may_group_a_stop_this_changeset_creates(self):
        change = _change(members=[_member(id=-3), _member(id=-4, role='stop')])
        [relation] = build_new_stop_area_relations([change], created_node_ids={-3, -4})

        assert [m['@ref'] for m in relation['member']] == [-3, -4]

    def test_a_placeholder_nothing_creates_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_area_relations([_change(members=[_member(id=-9)])], created_node_ids=set())

        assert e.value.status_code == 400
        assert 'not being created' in e.value.detail

    def test_the_same_stop_twice_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_area_relations([_change(members=[_member(id=1), _member(id=1)])], set())

        assert e.value.status_code == 400
        assert 'twice' in e.value.detail


def _route(members, tags=BUS_TAGS):
    return FinalRoute(
        ways=(), latLngs=(), busStops=(), tags=tags, extraWaysToUpdate=(), members=tuple(members), warnings=()
    )


def _build(changes, osm, members=(), new_stops=(), new_stop_positions=()):
    xml = asyncio.run(
        build_osm_change(
            None,
            _route(members),
            include_changeset_id=False,
            overpass=None,
            osm=osm,
            tags_edited=BUS_TAGS,
            new_stops=new_stops,
            new_stop_positions=new_stop_positions,
            stop_areas=changes,
        )
    )
    return xmltodict.parse(xml, force_list=('relation', 'node', 'way', 'member', 'tag'))['osmChange']


def _modifications(changes, osm):
    return asyncio.run(build_stop_area_modifications(changes, set(), osm))


class TestBuildStopAreaModifications:
    def test_adds_only_the_members_it_is_missing(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform')])})
        [relation] = _modifications([_change(id=99)], osm)

        assert [(m['@type'], m['@ref'], m['@role']) for m in relation['member']] == [
            ('node', '1', 'platform'),
            ('node', 2, 'stop'),
        ]

    def test_an_area_that_already_has_them_all_is_left_alone(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform'), ('node', 2, 'stop')])})

        assert _modifications([_change(id=99)], osm) == []

    def test_strips_the_metadata_but_keeps_the_tags(self):
        osm = FakeOsm({99: _relation(members=[])})
        [relation] = _modifications([_change(id=99)], osm)

        assert '@timestamp' not in relation
        assert '@user' not in relation
        assert {t['@k']: t['@v'] for t in relation['tag']}['name'] == 'The Station'

    def test_a_relation_that_is_no_longer_a_stop_area_is_a_conflict(self):
        osm = FakeOsm({99: _relation(tags={'type': 'route', 'route': 'bus'})})

        with pytest.raises(HTTPException) as e:
            _modifications([_change(id=99)], osm)

        assert e.value.status_code == 409

    def test_nothing_is_fetched_when_every_area_is_new(self):
        osm = FakeOsm({})
        assert _modifications([_change()], osm) == []
        assert osm.requested is None


class TestBuildOsmChange:
    def test_a_new_stop_area_rides_along_with_the_route(self):
        change = _change(members=[_member(id=1), _member(id=2, role='stop')])
        created = _build([change], FakeOsm({}))['create']['relation']

        # the route relation and the stop area, each with its own placeholder
        assert sorted(r['@id'] for r in created) == ['-1', '-2']

    def test_an_existing_stop_area_is_modified_in_the_same_changeset(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform')])})
        change = _build([_change(id=99)], osm)

        [modified] = change['modify']['relation']
        assert modified['@id'] == '99'
        assert len(modified['member']) == 2
