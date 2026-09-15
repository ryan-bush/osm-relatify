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
        # as the API does: an id it knows nothing about is left out of the reply rather
        # than making the whole request fail
        return [self._relations[int(i)] for i in relation_ids if int(i) in self._relations]


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
    # OSM said what it is; it is simply not one this application opens
    assert route['described'] is True
    assert route['name'] == 'Bus 9 (old)'


def test_a_member_osm_did_not_describe_is_not_marked_as_one_it_cannot_open(monkeypatch):
    """A member deleted since the master last mentioned it is not a route with bad tags."""
    relations = [_relation(100, MASTER_TAGS, [('relation', 1), ('relation', 2)]), _relation(1, PTV2)]

    routes = _query(relations, 100, monkeypatch).json()['routes']

    assert [route['id'] for route in routes] == [1, 2], 'listed rather than quietly dropped'
    assert routes[0]['described'] is True
    assert routes[1]['described'] is False
    assert routes[1]['editable'] is False


def test_a_lookup_that_failed_leaves_every_variant_undescribed(monkeypatch):
    """
    The variants are worth showing without their names, but not with an explanation the
    application invented: a failed lookup is a thing to reload, not a master full of
    routes it cannot open.
    """

    class FailingOsm(FakeOsm):
        async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
            raise RuntimeError('OSM is having a moment')

    monkeypatch.setattr(main, '_OSM', FailingOsm([_relation(100, MASTER_TAGS, [('relation', 1), ('relation', 2)])]))
    monkeypatch.setattr(main, '_OVERPASS', FakeOverpass())

    body = _CLIENT.post('/query', json={'relationId': 100}).json()

    assert body['kind'] == 'route_master'
    assert [route['id'] for route in body['routes']] == [1, 2]
    assert not any(route['described'] for route in body['routes'])


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
