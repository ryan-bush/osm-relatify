"""
What the upload endpoint tells the client about the ids OSM handed out.

A changeset can create several relations at once, and the diffResult names them only by
the placeholder each went up with. These cover the wiring: which placeholder the endpoint
asks for the route by, and that the stop areas it created reach the client at all.
"""

from typing import ClassVar

from fastapi.testclient import TestClient

import main
from models.osm_change import OsmChange
from models.stop_area import StopArea
from openstreetmap import UploadResult
from placeholder_ids import RelationPlaceholders
from stop_areas import NewStopAreaPlan, StopAreaMember
from user_session import require_user_access_token

_CLIENT = TestClient(main.app)
main.app.dependency_overrides[require_user_access_token] = lambda: 'token'

# enough of a route for the endpoint to parse; nothing here builds a change out of it
_ROUTE = {
    'ways': [],
    'latLngs': [],
    'busStops': [],
    'tags': {'type': 'route', 'route': 'bus', 'public_transport:version': '2'},
    'extraWaysToUpdate': [],
    'members': [],
    'warnings': [],
}

_PLAN = NewStopAreaPlan(
    placeholder_id=-2,
    name='The Station',
    members=(StopAreaMember(type='node', id=1, role='platform'),),
    element={},
)


class FakeOsm:
    """Stands in for the authorized client the endpoint opens to upload with."""

    calls: ClassVar[list[dict]] = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get_authorized_user(self):
        return {'changesets': {'count': 7}}

    async def upload_osm_change(self, osm_change, tags, **kwargs):  # noqa: ARG002
        FakeOsm.calls.append(kwargs)

        return UploadResult(
            ok=True,
            error_code=None,
            error_message=None,
            changeset_id=999,
            relation_id=12345 if kwargs.get('relation_placeholder') is not None else None,
            new_stop_areas=[StopArea(id=501, name='The Station', members=['node/1'])],
        )


def _upload(monkeypatch, relation_id):
    FakeOsm.calls = []
    monkeypatch.setattr(main, 'OpenStreetMap', FakeOsm)

    async def fake_build(*args, **kwargs):  # noqa: ARG001
        return OsmChange(xml='<osmChange/>', new_stop_areas=(_PLAN,))

    monkeypatch.setattr(main, 'build_osm_change', fake_build)

    r = _CLIENT.post(
        '/upload_osm',
        json={
            'relationId': relation_id,
            'route': _ROUTE,
            'tags': {},
            'comment': 'testing',
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_a_route_being_created_is_asked_for_by_its_own_placeholder(monkeypatch):
    data = _upload(monkeypatch, None)

    [call] = FakeOsm.calls
    assert call['relation_placeholder'] == RelationPlaceholders.ROUTE
    assert call['new_stop_areas'] == (_PLAN,)
    assert data['relation_id'] == 12345


def test_editing_a_route_asks_for_no_created_relation_at_all(monkeypatch):
    """There is no route to create, so a stop area or master created alongside is not one."""
    data = _upload(monkeypatch, 18333921)

    [call] = FakeOsm.calls
    assert call['relation_placeholder'] is None
    assert data['relation_id'] is None


def test_the_stop_areas_the_change_created_reach_the_client(monkeypatch):
    data = _upload(monkeypatch, 18333921)

    assert data['new_stop_areas'] == [{'id': 501, 'name': 'The Station', 'members': ['node/1']}]
