"""
How many queries loading a route costs Overpass.

The public instance gives one address two query slots a minute. Loading a route asked for
four in the same moment - the relation's own bounding box, the area itself, the route
masters its ref could join, and the country the route is in for the driving side - and the
third came back 429. Only the first two need an answer before the next can be asked; the
other two need nothing the area download does not already have, so they ride along with
it and a fresh route load is two queries.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import main
from models.bounding_box import BoundingBox
from models.download_history import DownloadHistory
from overpass import Overpass, QueryRelationResult
from user_session import require_user_details

_CLIENT = TestClient(main.app)
main.app.dependency_overrides[require_user_details] = lambda: {'display_name': 'tester'}

BOUNDS = BoundingBox(minlat=53.3, minlon=-4.7, maxlat=53.4, maxlon=-4.6)


class _Reply:
    def __init__(self, elements):
        self._elements = elements

    def json(self):
        return {'elements': self._elements}


class FakeOverpass:
    """A download of nothing, which is all the queries around it need."""

    def __init__(self):
        self.asked = []
        self.candidate_queries = 0

    async def query_relation(self, relation_id, download_hist, download_targets, route_type, ref, route_value):  # noqa: ARG002
        self.asked.append(ref)

        if download_hist is None:
            download_hist = DownloadHistory(session='s', history=(tuple(download_targets),))
        else:
            download_hist = DownloadHistory(
                session=download_hist.session,
                history=(*download_hist.history, tuple(download_targets)),
            )

        return QueryRelationResult(
            bounds=BOUNDS,
            download_hist=download_hist,
            download_triggers={},
            ways={},
            id_map={},
            bus_stop_collections=(),
            stop_areas=[],
            driving_side='left',
            route_master_candidates=[],
        )

    async def query_route_master_candidates(self, ref, route_value, bounds):  # noqa: ARG002
        self.candidate_queries += 1
        return []


@pytest.fixture
def overpass(monkeypatch):
    fake = FakeOverpass()
    monkeypatch.setattr(main, '_OVERPASS', fake)
    # NaPTAN would answer with the driving side of its own, which is not what is measured
    monkeypatch.setattr(main, 'NAPTAN_ENABLED', False)
    return fake


def _new_route():
    return _CLIENT.post(
        '/query',
        json={'relationId': None, 'routeType': 'bus', 'bounds': [53.3, -4.7, 53.31, -4.69]},
    ).json()


def _pan_into(previous, x=1):
    return _CLIENT.post(
        '/query',
        json={
            'relationId': None,
            'routeType': 'bus',
            'downloadHistory': previous['downloadHistory'],
            'downloadTargets': [{'x': x, 'y': 0}],
        },
    ).json()


def test_the_download_is_the_only_thing_asked_of_overpass(overpass):
    assert _new_route()['drivingSide'] == 'left'
    assert overpass.candidate_queries == 0, 'the candidates came down with the area'


def test_panning_further_asks_nothing_extra_either(overpass):
    first = _new_route()

    for x in range(1, 4):
        _pan_into(first, x)

    assert overpass.candidate_queries == 0


def test_the_download_is_told_the_ref_its_siblings_are_found_by(overpass):
    _new_route()

    # a route being created starts without one; an existing route carries its own
    assert overpass.asked == ['']


def test_a_merge_says_nothing_about_the_driving_side(overpass):  # noqa: ARG001
    """The client keeps what the first answer said; None is what leaves it alone."""
    merged = _pan_into(_new_route())

    assert merged['fetchMerge'] is True
    assert merged['drivingSide'] is None


def test_the_download_is_where_stop_areas_come_from(overpass):  # noqa: ARG001
    # no separate lookup to fail, so the client is never left not knowing
    assert _new_route()['stopAreas'] == []


def test_the_session_is_carried_through_a_merge(overpass):  # noqa: ARG001
    first = _new_route()
    merged = _pan_into(first)

    assert merged['downloadHistory']['session'] == first['downloadHistory']['session']
    assert len(merged['downloadHistory']['history']) == 2


class TestWhatTheDownloadCosts:
    """The real Overpass client, counting what it sends for one fresh route load."""

    def _replies(self, monkeypatch):
        sent = []

        async def fake_post(query, query_timeout):  # noqa: ARG001
            sent.append(query)

            if 'out ids bb qt' in query:  # the relation's own bounding box
                bounds = {'minlat': 53.3, 'minlon': -4.7, 'maxlat': 53.31, 'maxlon': -4.69}
                return _Reply([{'id': 1, 'bounds': bounds}])

            groups = [
                *([{'type': 'count'}] * 7),  # the area itself
                {'type': 'area', 'tags': {'ISO3166-1': 'GB'}},
                {'type': 'count'},
                {'type': 'relation', 'id': 9, 'tags': {'type': 'route_master', 'ref': '4'}},
                {'type': 'count'},
            ]
            return _Reply(groups)

        monkeypatch.setattr('overpass.overpass_post', fake_post)
        return sent

    def _load(self):
        return asyncio.run(
            Overpass().query_relation(
                relation_id=123,
                download_hist=None,
                download_targets=None,
                route_type='bus',
                ref='4',
                route_value='bus',
            )
        )

    def test_a_fresh_route_load_is_two_queries(self, monkeypatch):
        sent = self._replies(monkeypatch)
        self._load()

        assert len(sent) == 2, 'a third would be refused: the instance gives two slots a minute'

    def test_the_driving_side_comes_down_with_the_area(self, monkeypatch):
        self._replies(monkeypatch)

        assert self._load().driving_side == 'left'

    def test_the_route_master_candidates_come_down_with_it_too(self, monkeypatch):
        self._replies(monkeypatch)

        assert [master.id for master in self._load().route_master_candidates] == [9]

    def test_a_route_with_no_ref_has_no_siblings_to_ask_after(self, monkeypatch):
        sent = self._replies(monkeypatch)
        asyncio.run(
            Overpass().query_relation(
                relation_id=124,
                download_hist=None,
                download_targets=None,
                route_type='bus',
            )
        )

        assert '"route_master"' not in sent[1]
