import asyncio

import pytest
import xmltodict
from fastapi import HTTPException

from placeholder_ids import RelationPlaceholders
from models.final_route import FinalRoute
from relation_builder import build_osm_change
from stop_areas import (
    StopAreaChange,
    StopAreaMember,
    build_new_stop_area_relations,
    build_stop_area_modifications,
    check_new_stop_areas,
    parse_stop_areas,
)

BUS_TAGS = {'type': 'route', 'route': 'bus', 'public_transport:version': '2', 'name': 'Bus 12'}


def _member(type='node', id=1, role='platform'):
    return StopAreaMember(type=type, id=id, role=role)


def _change(id=None, name='The Station', members=None):
    return StopAreaChange(id=id, name=name, members=members or [_member(id=1), _member(id=2, role='stop')])


class FakeOsm:
    """Stands in for the OSM API, returning the stop area relations being added to."""

    def __init__(self, relations: dict[int, dict], parents: dict[tuple[str, int], list[dict]] | None = None):
        self._relations = relations
        # what each element is already a member of, for the duplicate check
        self._parents = parents or {}
        self.requested = None

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        self.requested = tuple(relation_ids)
        return [self._relations[int(i)] for i in relation_ids]

    async def get_parent_relations(self, element_type: str, element_id: int):
        return self._parents.get((element_type, element_id), [])


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
        [plan] = build_new_stop_area_relations([_change()], set(), RelationPlaceholders())
        relation = plan.element

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

    def test_the_plan_says_what_the_relation_will_be_once_uploaded(self):
        """The upload reads the real id back by the placeholder, which is only known here."""
        [plan] = build_new_stop_area_relations([_change()], set(), RelationPlaceholders())

        assert plan.placeholder_id == -2
        assert plan.name == 'The Station'
        assert [m.key for m in plan.members] == ['node/1', 'node/2']

    def test_each_new_area_gets_its_own_placeholder(self):
        plans = build_new_stop_area_relations([_change(), _change(name='Market Square')], set(), RelationPlaceholders())
        assert [p.placeholder_id for p in plans] == [-2, -3]
        assert [p.element['@id'] for p in plans] == [-2, -3]

    def test_an_existing_area_is_not_created_again(self):
        assert build_new_stop_area_relations([_change(id=99)], set(), RelationPlaceholders()) == []

    def test_a_new_area_needs_a_name(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_area_relations([_change(name='  ')], set(), RelationPlaceholders())

        assert e.value.status_code == 400
        assert 'needs a name' in e.value.detail

    def test_it_may_group_a_stop_this_changeset_creates(self):
        change = _change(members=[_member(id=-3), _member(id=-4, role='stop')])
        [plan] = build_new_stop_area_relations([change], {-3, -4}, RelationPlaceholders())

        assert [m['@ref'] for m in plan.element['member']] == [-3, -4]

    def test_a_placeholder_nothing_creates_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_area_relations([_change(members=[_member(id=-9)])], set(), RelationPlaceholders())

        assert e.value.status_code == 400
        assert 'not being created' in e.value.detail

    def test_the_same_stop_twice_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_area_relations([_change(members=[_member(id=1), _member(id=1)])], set(), RelationPlaceholders())

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
    ).xml
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

    def test_an_older_stop_position_role_is_put_right_when_the_area_changes(self):
        osm = FakeOsm({99: _relation(members=[('node', 5, 'stop_position'), ('node', 1, 'platform')])})
        [relation] = _modifications([_change(id=99)], osm)

        assert [(m['@ref'], m['@role']) for m in relation['member']] == [('5', 'stop'), ('1', 'platform'), (2, 'stop')]

    def test_an_older_role_alone_is_not_a_reason_to_change_the_area(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform'), ('node', 2, 'stop_position')])})

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


def _parent_area(id=21385736, name='Berkeley Road'):
    return {
        'type': 'relation',
        'id': id,
        'tags': {'type': 'public_transport', 'public_transport': 'stop_area', 'name': name},
    }


class TestCheckNewStopAreas:
    """
    A download from an Overpass instance that has fallen behind does not know about a
    stop area created since its snapshot, so it offers to create one that already exists.
    OSM itself is asked again before anything is uploaded.
    """

    def _check(self, changes, parents=None):
        return asyncio.run(check_new_stop_areas(changes, FakeOsm({}, parents)))

    def test_a_stop_already_in_one_is_refused(self):
        with pytest.raises(HTTPException) as e:
            self._check([_change()], {('node', 1): [_parent_area()]})

        assert e.value.status_code == 409
        assert 'node/1' in e.value.detail
        assert '21385736' in e.value.detail
        assert 'Berkeley Road' in e.value.detail

    def test_a_stop_in_nothing_is_let_through(self):
        assert self._check([_change()]) is None

    def test_some_other_relation_the_stop_is_in_does_not_count(self):
        route = {'type': 'relation', 'id': 5, 'tags': {'type': 'route', 'route': 'bus'}}

        assert self._check([_change()], {('node', 1): [route]}) is None

    def test_completing_an_existing_area_is_not_a_duplicate(self):
        # the whole point of that change is to add to the relation it is already in
        assert self._check([_change(id=99)], {('node', 1): [_parent_area()]}) is None

    def test_a_stop_this_changeset_creates_is_not_looked_up(self):
        change = _change(members=[_member(id=-3), _member(id=-4, role='stop')])

        assert self._check([change]) is None

    def test_the_upload_is_stopped_by_it(self):
        osm = FakeOsm({}, {('node', 1): [_parent_area()]})

        with pytest.raises(HTTPException) as e:
            _build([_change()], osm)

        assert e.value.status_code == 409


class TestRenamingAnExistingArea:
    """
    A stop area is named after the place, so renaming the stop should carry it along
    rather than leave the relation saying what the stop used to be called.
    """

    def _change(self, name='Market Square', expected='The Station'):
        return StopAreaChange(
            id=99, name=name, expectedName=expected, members=[_member(id=1), _member(id=2, role='stop')]
        )

    def test_the_relation_takes_the_new_name(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform'), ('node', 2, 'stop')])})
        [relation] = _modifications([self._change()], osm)

        assert {t['@k']: t['@v'] for t in relation['tag']}['name'] == 'Market Square'

    def test_a_rename_and_a_missing_member_are_one_change(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform')])})
        [relation] = _modifications([self._change()], osm)

        assert {t['@k']: t['@v'] for t in relation['tag']}['name'] == 'Market Square'
        assert [m['@ref'] for m in relation['member']] == ['1', 2]

    def test_nothing_is_sent_when_it_already_says_that(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform'), ('node', 2, 'stop')])})

        assert _modifications([self._change(name='The Station', expected='The Station')], osm) == []

    def test_a_name_changed_since_is_a_conflict(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform'), ('node', 2, 'stop')])})

        with pytest.raises(HTTPException) as e:
            _modifications([self._change(expected='Something else')], osm)

        assert e.value.status_code == 409
        assert 'The Station' in e.value.detail

    def test_it_cannot_be_renamed_to_nothing(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform')])})

        with pytest.raises(HTTPException) as e:
            _modifications([self._change(name='   ')], osm)

        assert e.value.status_code == 400

    def test_an_area_not_being_renamed_keeps_the_name_it_has(self):
        osm = FakeOsm({99: _relation(members=[('node', 1, 'platform')])})
        # no expectedName: the mapper only asked for the missing member
        [relation] = _modifications([_change(id=99, name='Market Square')], osm)

        assert {t['@k']: t['@v'] for t in relation['tag']}['name'] == 'The Station'
