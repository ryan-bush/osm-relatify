"""
What a further download of the same route asks Overpass.

Panning far enough downloads the area panned into, and that download is one query. What
went around it was not: the driving side was asked again for every area, from a cache
keyed on the middle of the map, which moves as the map does. It is a property of the
country, the country does not change as the mapper pans, and the client keeps what the
first answer said - so a merge does not ask again.
"""

import pytest
from fastapi.testclient import TestClient

import main
from models.bounding_box import BoundingBox
from models.download_history import DownloadHistory
from user_session import require_user_details

_CLIENT = TestClient(main.app)
main.app.dependency_overrides[require_user_details] = lambda: {'display_name': 'tester'}


class FakeOverpass:
    """A download of nothing, which is all the queries around it need."""

    def __init__(self):
        self.driving_side_queries = 0

    async def query_relation(self, relation_id, download_hist, download_targets, route_type):  # noqa: ARG002
        if download_hist is None:
            download_hist = DownloadHistory(session='s', history=(tuple(download_targets),))
        else:
            download_hist = DownloadHistory(
                session=download_hist.session,
                history=(*download_hist.history, tuple(download_targets)),
            )

        bounds = BoundingBox(minlat=53.3, minlon=-4.7, maxlat=53.4, maxlon=-4.6)
        return bounds, download_hist, {}, {}, {}, (), []

    async def query_driving_side(self, lat, lon):  # noqa: ARG002
        self.driving_side_queries += 1
        return 'left'

    async def query_route_master_candidates(self, ref, route_value, bounds):  # noqa: ARG002
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


def test_the_first_download_looks_up_the_driving_side(overpass):
    assert _new_route()['drivingSide'] == 'left'
    assert overpass.driving_side_queries == 1


def test_panning_further_does_not_ask_again(overpass):
    first = _new_route()

    for x in range(1, 4):
        _pan_into(first, x)

    assert overpass.driving_side_queries == 1, 'the country does not change as the map moves'


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
