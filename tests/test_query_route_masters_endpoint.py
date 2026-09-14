from fastapi.testclient import TestClient

import main
from models.bounding_box import BoundingBox
from models.route_master import RouteMaster
from user_session import require_user_details

_CLIENT = TestClient(main.app)
main.app.dependency_overrides[require_user_details] = lambda: {'display_name': 'tester'}

BOUNDS = [51.0, -1.5, 51.5, -1.0]


def _post(body, monkeypatch, answer=([], [])):
    seen = {}

    async def fake(relation_id, tags, bounds):
        seen['args'] = (relation_id, tags, bounds)
        return answer

    monkeypatch.setattr(main, '_query_route_masters', fake)
    return _CLIENT.post('/query_route_masters', json=body), seen


def test_it_asks_with_the_tags_the_mapper_has_now(monkeypatch):
    tags = {'type': 'route', 'route': 'bus', 'ref': '9'}

    r, seen = _post({'relationId': None, 'tags': tags, 'bounds': BOUNDS}, monkeypatch)

    assert r.status_code == 200
    assert seen['args'] == (None, tags, BoundingBox(*BOUNDS))


def test_it_answers_with_both_lists(monkeypatch):
    master = RouteMaster(id=100, tags={'type': 'route_master', 'ref': '9'}, members=['relation/5'])

    r, _ = _post({'tags': {}, 'bounds': BOUNDS}, monkeypatch, answer=([], [master]))

    assert r.json() == {
        'routeMasters': [],
        'routeMasterCandidates': [
            {'id': 100, 'tags': {'type': 'route_master', 'ref': '9'}, 'members': ['relation/5'], 'routes': []}
        ],
    }


# null is not an empty list: it is what stops the client offering to create a second
# master beside one it simply could not see
def test_a_lookup_that_could_not_answer_stays_null(monkeypatch):
    r, _ = _post({'tags': {}, 'bounds': BOUNDS}, monkeypatch, answer=(None, None))

    assert r.json() == {'routeMasters': None, 'routeMasterCandidates': None}


def test_bounds_are_required():
    assert _CLIENT.post('/query_route_masters', json={'tags': {}}).status_code == 422
