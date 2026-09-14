import asyncio

import pytest
import xmltodict
from fastapi import HTTPException

from relation_builder import build_route_master_only_change
from route_masters import RouteMasterChange

MASTER_TAGS = {'type': 'route_master', 'route_master': 'bus', 'ref': '9', 'name': 'Bus 9'}


def _relation(id=100, tags=None, members=(('relation', 1), ('relation', 2))):
    return {
        '@id': str(id),
        '@version': '5',
        '@timestamp': '2026-01-01T00:00:00Z',
        '@user': 'someone',
        '@uid': '1',
        'tag': [{'@k': k, '@v': v} for k, v in (tags if tags is not None else MASTER_TAGS).items()],
        'member': [{'@type': t, '@ref': str(r), '@role': ''} for t, r in members],
    }


class FakeOsm:
    def __init__(self, relations):
        self._relations = {int(r['@id']): r for r in relations}

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        return [self._relations[int(i)] for i in relation_ids]


def _build(change, relations):
    return asyncio.run(build_route_master_only_change(change, False, FakeOsm(relations)))


def _parsed(xml):
    return xmltodict.parse(xml, force_list=('relation', 'tag', 'member'))['osmChange']


def test_it_uploads_only_the_master_and_only_its_tags():
    change = RouteMasterChange(id=100, tags={**MASTER_TAGS, 'operator': 'Alpha'}, tagsOriginal=MASTER_TAGS)

    osm_change = _parsed(_build(change, [_relation()]))

    assert osm_change.get('create') is None or not osm_change['create']
    [master] = osm_change['modify']['relation']
    assert {t['@k']: t['@v'] for t in master['tag']}['operator'] == 'Alpha'


# the variants it holds are not in question here; only its own tags are being edited
def test_the_members_are_left_exactly_as_they_are():
    change = RouteMasterChange(id=100, tags={**MASTER_TAGS, 'operator': 'Alpha'}, tagsOriginal=MASTER_TAGS)

    [master] = _parsed(_build(change, [_relation()]))['modify']['relation']

    assert [(m['@type'], m['@ref']) for m in master['member']] == [('relation', '1'), ('relation', '2')]


def test_a_master_with_no_tag_changes_is_not_uploaded():
    change = RouteMasterChange(id=100, tags=dict(MASTER_TAGS), tagsOriginal=MASTER_TAGS)

    osm_change = _parsed(_build(change, [_relation()]))

    assert not (osm_change.get('modify') or {}).get('relation')


def test_a_tag_someone_else_changed_meanwhile_is_a_conflict():
    change = RouteMasterChange(id=100, tags={**MASTER_TAGS, 'name': 'Mine'}, tagsOriginal=MASTER_TAGS)

    with pytest.raises(HTTPException) as e:
        _build(change, [_relation(tags={**MASTER_TAGS, 'name': 'Theirs'})])

    assert e.value.status_code == 409


def test_a_relation_that_stopped_being_a_master_is_a_conflict():
    change = RouteMasterChange(id=100, tags={**MASTER_TAGS, 'name': 'Mine'}, tagsOriginal=MASTER_TAGS)

    with pytest.raises(HTTPException) as e:
        _build(change, [_relation(tags={'type': 'route', 'route': 'bus'})])

    assert e.value.status_code == 409


def test_the_tags_holding_a_master_together_cannot_be_edited():
    change = RouteMasterChange(id=100, tags={**MASTER_TAGS, 'route_master': 'tram'}, tagsOriginal=MASTER_TAGS)

    with pytest.raises(HTTPException) as e:
        _build(change, [_relation()])

    assert e.value.status_code == 400


def test_editing_metadata_is_not_uploaded_back():
    change = RouteMasterChange(id=100, tags={**MASTER_TAGS, 'operator': 'Alpha'}, tagsOriginal=MASTER_TAGS)

    [master] = _parsed(_build(change, [_relation()]))['modify']['relation']

    assert '@timestamp' not in master
    assert '@user' not in master
    assert master['@version'] == '5'
