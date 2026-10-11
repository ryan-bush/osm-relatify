"""
Starting a new route as a copy of an existing one.

A line can have a dozen variants that share most of their ways and stops, so a new one is
quicker to make from one of the others than from nothing. The copy is downloaded as the
route it comes from, and is from then on a relation being created like any other.
"""

import pytest
from fastapi.testclient import TestClient

import main
from models.bounding_box import BoundingBox
from models.download_history import DownloadHistory
from overpass import QueryRelationResult
from user_session import require_user_details

_CLIENT = TestClient(main.app)
main.app.dependency_overrides[require_user_details] = lambda: {'display_name': 'tester'}

PTV2 = {'type': 'route', 'route': 'bus', 'public_transport:version': '2'}
SOURCE_TAGS = {**PTV2, 'ref': '99', 'name': 'Bus 99: Swindon => Chippenham', 'from': 'Swindon', 'to': 'Chippenham'}


def _relation(id, tags, members=()):
    return {
        'type': 'relation',
        'id': id,
        'tags': tags,
        'members': [{'type': t, 'ref': r, 'role': ''} for t, r in members],
    }


class FakeOsm:
    def __init__(self, relations):
        self._relations = {r['id']: r for r in relations}
        self.parents_asked = []

    async def get_relation(self, relation_id, json: bool = True):  # noqa: ARG002
        return self._relations[int(relation_id)]

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        return [self._relations[int(i)] for i in relation_ids if int(i) in self._relations]

    async def get_parent_relations(self, type, id):
        self.parents_asked.append((type, id))
        return []


class FakeOverpass:
    def __init__(self):
        self.asked = []

    async def query_relation(self, relation_id, download_hist, download_targets, route_type, ref, route_value, member_way_ids):  # noqa: ARG002
        self.asked.append({'relation_id': relation_id, 'ref': ref, 'member_way_ids': member_way_ids})

        return QueryRelationResult(
            bounds=BoundingBox(minlat=51.5, minlon=-1.9, maxlat=51.6, maxlon=-1.8),
            download_hist=DownloadHistory(session='s', history=((),)),
            download_triggers={},
            ways={},
            id_map={},
            bus_stop_collections=(),
            stop_areas=[],
            driving_side='left',
            route_master_candidates=[],
        )


@pytest.fixture
def services(monkeypatch):
    osm = FakeOsm([
        _relation(1, SOURCE_TAGS, [('way', 10), ('way', 11)]),
        _relation(100, {'type': 'route_master', 'route_master': 'bus', 'ref': '99'}, [('relation', 1)]),
    ])
    overpass = FakeOverpass()
    monkeypatch.setattr(main, '_OSM', osm)
    monkeypatch.setattr(main, '_OVERPASS', overpass)
    monkeypatch.setattr(main, 'NAPTAN_ENABLED', False)
    # the endpoints are found among the ways downloaded, and this download has none
    monkeypatch.setattr(main, 'find_start_stop_ways', lambda *_: (None, None))
    return osm, overpass


def _duplicate(source_id):
    return _CLIENT.post('/query', json={'relationId': None, 'duplicateFrom': source_id})


def test_the_copy_is_downloaded_as_the_route_it_comes_from(services):
    _, overpass = services

    assert _duplicate(1).status_code == 200
    assert overpass.asked == [{'relation_id': 1, 'ref': '99', 'member_way_ids': (10, 11)}]


def test_the_copy_starts_with_the_tags_of_the_route_it_comes_from(services):  # noqa: ARG001
    assert _duplicate(1).json()['tags'] == SOURCE_TAGS


def test_the_copy_is_in_no_route_master_yet(services):
    osm, _ = services

    # it is a new relation, so the source's own masters are not its own
    assert _duplicate(1).json()['routeMasters'] == []
    assert osm.parents_asked == []


def test_a_route_master_is_not_something_to_duplicate(services):
    _, overpass = services

    response = _duplicate(100)

    assert response.status_code == 400
    assert overpass.asked == []
