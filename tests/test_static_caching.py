from fastapi.testclient import TestClient

from main import app

# TestClient is used without its context manager on purpose: that skips the lifespan, and
# with it the NaPTAN download, which has nothing to do with serving a file.
_CLIENT = TestClient(app)


def test_a_static_file_is_served_with_revalidation():
    r = _CLIENT.get('/static/js/menu.js')

    assert r.status_code == 200
    assert r.headers['cache-control'] == 'no-cache'
    assert r.headers['etag']


# Revalidate, not do-not-store: the browser keeps the file and is told it is still good.
def test_an_unchanged_file_is_answered_without_its_body():
    etag = _CLIENT.get('/static/js/menu.js').headers['etag']

    r = _CLIENT.get('/static/js/menu.js', headers={'if-none-match': etag})

    assert r.status_code == 304
    assert not r.content
    assert r.headers['cache-control'] == 'no-cache'
