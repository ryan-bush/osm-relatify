from fastapi.testclient import TestClient

import main
from user_session import require_user_details

_CLIENT = TestClient(main.app)
main.app.dependency_overrides[require_user_details] = lambda: {'display_name': 'tester'}

MASTER_TAGS = {'type': 'route_master', 'route_master': 'bus', 'ref': '9', 'name': 'Bus 9'}
PTV2 = {'type': 'route', 'route': 'bus', 'public_transport:version': '2'}


class FakeOsm:
    def __init__(self, relations):
        self._relations = {r['id']: r for r in relations}
        self.queried_relation = False

    async def get_relation(self, relation_id, json: bool = True):  # noqa: ARG002
        return self._relations[int(relation_id)]

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        return [self._relations[int(i)] for i in relation_ids]


class FakeOverpass:
    """Nothing should reach Overpass: a master is listed, not downloaded."""

    async def query_relation(self, **kwargs):
        raise AssertionError('a route master must not be downloaded')


def _relation(id, tags, members=()):
    return {
        'type': 'relation',
        'id': id,
        'tags': tags,
        'members': [{'type': t, 'ref': r, 'role': ''} for t, r in members],
    }


def _query(relations, relation_id, monkeypatch):
    monkeypatch.setattr(main, '_OSM', FakeOsm(relations))
    monkeypatch.setattr(main, '_OVERPASS', FakeOverpass())
    return _CLIENT.post('/query', json={'relationId': relation_id})


def test_a_route_master_id_lists_its_variants(monkeypatch):
    relations = [
        _relation(100, MASTER_TAGS, [('relation', 1), ('relation', 2)]),
        _relation(1, {**PTV2, 'ref': '9', 'name': 'Bus 9: A => B', 'from': 'A', 'to': 'B'}),
        _relation(2, {**PTV2, 'ref': '9', 'name': 'Bus 9: B => A'}),
    ]

    body = _query(relations, 100, monkeypatch).json()

    assert body['kind'] == 'route_master'
    assert body['id'] == 100
    assert body['tags']['name'] == 'Bus 9'
    assert [route['id'] for route in body['routes']] == [1, 2]
    assert body['routes'][0]['tags']['from'] == 'A'
    assert all(route['editable'] for route in body['routes'])


# the point of answering here is that it costs one lookup and no download
def test_listing_a_master_downloads_nothing(monkeypatch):
    relations = [_relation(100, MASTER_TAGS, [('relation', 1)]), _relation(1, PTV2)]

    assert _query(relations, 100, monkeypatch).status_code == 200


def test_a_variant_this_application_cannot_open_is_marked(monkeypatch):
    relations = [
        _relation(100, MASTER_TAGS, [('relation', 1)]),
        # no public_transport:version, so it is not one this application reads
        _relation(1, {'type': 'route', 'route': 'bus', 'name': 'Bus 9 (old)'}),
    ]

    [route] = _query(relations, 100, monkeypatch).json()['routes']

    assert route['editable'] is False
    assert route['name'] == 'Bus 9 (old)'


def test_a_master_holding_something_that_is_not_a_route_says_so(monkeypatch):
    relations = [_relation(100, MASTER_TAGS, [('way', 9), ('relation', 1)]), _relation(1, PTV2)]

    body = _query(relations, 100, monkeypatch).json()

    assert body['otherMembers'] == ['way/9']
    assert [route['id'] for route in body['routes']] == [1]


def test_a_relation_that_is_neither_a_route_nor_a_master_is_still_refused(monkeypatch):
    relations = [_relation(100, {'type': 'multipolygon'})]

    r = _query(relations, 100, monkeypatch)

    assert r.status_code == 400
    assert 'PTv2' in r.json()['detail']
