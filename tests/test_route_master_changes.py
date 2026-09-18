import asyncio

import pytest
from fastapi import HTTPException

from placeholder_ids import RelationPlaceholders
from route_masters import (
    RouteMasterChange,
    build_new_route_master,
    build_route_master_modifications,
    check_new_route_master,
)

ROUTE_TAGS = {'type': 'route', 'route': 'bus', 'public_transport:version': '2', 'ref': '71'}
MASTER_TAGS = {'type': 'route_master', 'route_master': 'bus', 'ref': '71', 'name': 'Bus 71'}


def _relation(id=100, tags=None, members=(('relation', 5),)):
    return {
        '@id': str(id),
        '@version': '3',
        '@timestamp': '2026-01-01T00:00:00Z',
        '@user': 'someone',
        '@uid': '1',
        'tag': [{'@k': k, '@v': v} for k, v in (tags if tags is not None else MASTER_TAGS).items()],
        'member': [{'@type': t, '@ref': str(r), '@role': ''} for t, r in members],
    }


class FakeOsm:
    def __init__(self, relations=(), parents=()):
        self._relations = {int(r['@id']): r for r in relations}
        self._parents = list(parents)
        self.requested = None

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        self.requested = tuple(relation_ids)
        return [self._relations[int(i)] for i in relation_ids]

    async def get_parent_relations(self, element_type, element_id):  # noqa: ARG002
        return self._parents


def _tags(relation):
    return {tag['@k']: tag['@v'] for tag in relation['tag']}


def _members(relation):
    return [(m['@type'], str(m['@ref'])) for m in relation['member']]


class TestBuildNewRouteMaster:
    def _build(self, change, route_tags=None, route_ref=5):
        return build_new_route_master(change, route_tags or ROUTE_TAGS, route_ref, RelationPlaceholders())

    def test_it_creates_a_master_holding_the_route(self):
        master = self._build(RouteMasterChange(tags={'ref': '71', 'name': 'Bus 71'}))

        assert master['@id'] == -2, 'below the route relation, which takes -1'
        assert _tags(master) == {'ref': '71', 'name': 'Bus 71', 'type': 'route_master', 'route_master': 'bus'}
        assert _members(master) == [('relation', '5')]

    def test_a_route_being_created_is_held_by_its_placeholder(self):
        master = self._build(RouteMasterChange(tags={'name': 'Bus 71'}), route_ref=RelationPlaceholders.ROUTE)

        assert _members(master) == [('relation', '-1')]

    # the tags that make it a master are what the application reads it back by
    def test_the_tags_that_make_it_a_master_are_not_the_mapper_s_to_set(self):
        master = self._build(RouteMasterChange(tags={'type': 'route', 'route_master': 'tram', 'name': 'Bus 71'}))

        assert _tags(master)['type'] == 'route_master'
        assert _tags(master)['route_master'] == 'bus'

    def test_a_trolleybus_route_gets_a_trolleybus_master(self):
        master = self._build(RouteMasterChange(tags={'name': 'Bus 71'}), {'type': 'route', 'route': 'trolleybus'})

        assert _tags(master)['route_master'] == 'trolleybus'

    def test_a_route_of_no_stated_kind_cannot_have_a_master(self):
        with pytest.raises(HTTPException) as e:
            self._build(RouteMasterChange(tags={'name': 'Bus 71'}), {'type': 'route'})

        assert e.value.status_code == 400

    def test_an_emptied_tag_is_not_written(self):
        assert 'name' not in _tags(self._build(RouteMasterChange(tags={'name': '  '})))

    def test_a_tag_too_long_is_refused(self):
        with pytest.raises(HTTPException) as e:
            self._build(RouteMasterChange(tags={'name': 'x' * 256}))

        assert e.value.status_code == 400

    def test_nothing_is_created_for_a_master_already_in_osm(self):
        assert self._build(RouteMasterChange(id=100, tags={'name': 'Bus 71'})) is None

    def test_nothing_is_created_when_no_master_was_asked_for(self):
        assert self._build(None) is None


class TestCheckNewRouteMaster:
    """
    A download is a moment in the past: a master created since leaves this route looking
    unlinked, and it would be given a second one of its own. OSM is asked again.
    """

    def _check(self, change, relation_id=5, parents=()):
        return asyncio.run(check_new_route_master(change, relation_id, FakeOsm(parents=parents)))

    def test_a_route_already_in_a_master_is_refused_a_new_one(self):
        parent = {'type': 'relation', 'id': 100, 'tags': MASTER_TAGS}

        with pytest.raises(HTTPException) as e:
            self._check(RouteMasterChange(tags={'name': 'Bus 71'}), parents=[parent])

        assert e.value.status_code == 409
        assert '100' in e.value.detail
        assert 'Bus 71' in e.value.detail

    def test_a_parent_that_is_not_a_master_is_no_obstacle(self):
        parent = {'type': 'relation', 'id': 200, 'tags': {'type': 'network'}}

        self._check(RouteMasterChange(tags={'name': 'Bus 71'}), parents=[parent])

    def test_joining_an_existing_master_is_not_checked_this_way(self):
        parent = {'type': 'relation', 'id': 100, 'tags': MASTER_TAGS}

        self._check(RouteMasterChange(id=100), parents=[parent])

    def test_a_route_being_created_is_in_nothing_to_begin_with(self):
        self._check(RouteMasterChange(tags={'name': 'Bus 71'}), relation_id=None, parents=[])

    def test_nothing_to_check_without_a_master(self):
        self._check(None)


class TestBuildRouteMasterModifications:
    def _build(self, change=None, detach=(), route_ref=5, relations=()):
        return asyncio.run(
            build_route_master_modifications(change, detach, route_ref, FakeOsm(relations=relations))
        )

    def test_the_route_is_added_to_the_master_it_joins(self):
        [master] = self._build(RouteMasterChange(id=100), relations=[_relation(members=(('relation', 7),))])

        assert _members(master) == [('relation', '7'), ('relation', '5')]

    # the download is a moment in the past, so what is there now is what is added to
    def test_a_route_the_master_already_holds_is_not_added_twice(self):
        assert self._build(RouteMasterChange(id=100), relations=[_relation()]) == []

    def test_the_route_is_removed_from_a_master_it_leaves(self):
        [master] = self._build(detach=[100], relations=[_relation(members=(('relation', 5), ('relation', 7)))])

        assert _members(master) == [('relation', '7')]

    def test_leaving_a_master_that_no_longer_holds_the_route_changes_nothing(self):
        assert self._build(detach=[100], relations=[_relation(members=(('relation', 7),))]) == []

    def test_joining_one_master_and_leaving_another_at_once(self):
        masters = self._build(
            RouteMasterChange(id=100),
            detach=[101],
            relations=[_relation(id=100, members=()), _relation(id=101, members=(('relation', 5),))],
        )

        assert {m['@id']: _members(m) for m in masters} == {
            '100': [('relation', '5')],
            '101': [],
        }

    def test_a_master_cannot_be_joined_and_left_at_once(self):
        with pytest.raises(HTTPException) as e:
            self._build(RouteMasterChange(id=100), detach=[100], relations=[_relation()])

        assert e.value.status_code == 400

    def test_a_route_being_created_has_no_master_to_leave(self):
        with pytest.raises(HTTPException) as e:
            self._build(detach=[100], route_ref=RelationPlaceholders.ROUTE, relations=[_relation()])

        assert e.value.status_code == 400

    # the modify block is read after the create block, so the route exists by then
    def test_a_master_already_in_osm_can_take_on_a_route_being_created(self):
        [master] = self._build(
            RouteMasterChange(id=100),
            route_ref=RelationPlaceholders.ROUTE,
            relations=[_relation(members=(('relation', 7),))],
        )

        assert _members(master) == [('relation', '7'), ('relation', '-1')]

    def test_a_relation_that_stopped_being_a_master_is_a_conflict(self):
        with pytest.raises(HTTPException) as e:
            self._build(RouteMasterChange(id=100), relations=[_relation(tags={'type': 'route', 'route': 'bus'})])

        assert e.value.status_code == 409
        assert 'no longer a route master' in e.value.detail

    def test_tag_edits_are_merged_onto_what_is_on_the_server(self):
        [master] = self._build(
            RouteMasterChange(id=100, tags={**MASTER_TAGS, 'operator': 'Stagecoach'}, tagsOriginal=MASTER_TAGS),
            relations=[_relation(tags={**MASTER_TAGS, 'colour': 'red'})],
        )

        assert _tags(master)['operator'] == 'Stagecoach'
        assert _tags(master)['colour'] == 'red', 'a tag nobody edited keeps what the server has'

    def test_a_tag_edit_alone_is_enough_to_upload_the_master(self):
        # the route is already a member, so nothing about the membership changes
        [master] = self._build(
            RouteMasterChange(id=100, tags={**MASTER_TAGS, 'name': 'Bus 71 (Oxford)'}, tagsOriginal=MASTER_TAGS),
            relations=[_relation()],
        )

        assert _tags(master)['name'] == 'Bus 71 (Oxford)'

    def test_the_tags_holding_a_master_together_cannot_be_edited(self):
        with pytest.raises(HTTPException) as e:
            self._build(
                RouteMasterChange(id=100, tags={**MASTER_TAGS, 'route_master': 'tram'}, tagsOriginal=MASTER_TAGS),
                relations=[_relation()],
            )

        assert e.value.status_code == 400
        assert 'route_master' in e.value.detail

    def test_a_tag_changed_by_someone_else_meanwhile_is_a_conflict(self):
        with pytest.raises(HTTPException) as e:
            self._build(
                RouteMasterChange(id=100, tags={**MASTER_TAGS, 'name': 'Mine'}, tagsOriginal=MASTER_TAGS),
                relations=[_relation(tags={**MASTER_TAGS, 'name': 'Theirs'})],
            )

        assert e.value.status_code == 409

    def test_a_master_left_alone_says_nothing_about_its_tags(self):
        # tagsOriginal absent means the mapper only linked the route, not edited the master
        [master] = self._build(RouteMasterChange(id=100, tags={}), relations=[_relation(members=())])

        assert _tags(master) == MASTER_TAGS

    def test_editing_metadata_is_not_uploaded_back(self):
        [master] = self._build(RouteMasterChange(id=100), relations=[_relation(members=())])

        assert '@timestamp' not in master
        assert '@user' not in master
        assert '@uid' not in master
        assert master['@version'] == '3'

    def test_nothing_is_fetched_when_there_is_nothing_to_change(self):
        assert self._build() == []
