"""The release comparison behind the navbar's update notice."""

import pytest
from fastapi.testclient import TestClient

import version_check
from config import APP_VERSION
from main import app

# no context manager: the lifespan's NaPTAN download has nothing to do with this
_CLIENT = TestClient(app)


@pytest.mark.parametrize(
    ('latest', 'current', 'expected'),
    [
        ('1.3.0', '1.2.0', True),
        ('1.2.1', '1.2.0', True),
        ('2.0.0', '1.9.9', True),
        ('1.10.0', '1.9.0', True),  # not a string comparison
        ('v1.3.0', '1.2.0', True),  # the tag keeps its v
        ('1.2.0', '1.2.0', False),
        ('1.2', '1.2.0', False),  # the same release written two ways
        ('1.1.0', '1.2.0', False),  # an instance ahead of the last release
        ('1.3.0-beta1', '1.2.0', False),  # pre-releases are not offered
        ('nightly', '1.2.0', False),
        (None, '1.2.0', False),
        ('1.3.0', '0.0.0', True),
    ],
)
def test_is_newer(latest, current, expected):
    assert version_check.is_newer(latest, current) is expected


def test_the_running_version_is_a_release_number():
    assert version_check.parse_version(APP_VERSION) is not None


def test_version_endpoint_reports_an_available_update(monkeypatch):
    async def fake_latest():
        return {'version': '9.9.9', 'url': 'https://example.test/releases/tag/v9.9.9'}

    monkeypatch.setattr(version_check, 'get_latest_release', fake_latest)

    body = _CLIENT.get('/version').json()

    assert body['version'] == APP_VERSION
    assert body['latest'] == '9.9.9'
    assert body['updateAvailable'] is True
    assert body['url'] == 'https://example.test/releases/tag/v9.9.9'


# GitHub being unreachable must not break the page that asks
def test_version_endpoint_still_answers_without_github(monkeypatch):
    async def fake_latest():
        return None

    monkeypatch.setattr(version_check, 'get_latest_release', fake_latest)

    body = _CLIENT.get('/version').json()

    assert body['version'] == APP_VERSION
    assert body['latest'] is None
    assert body['updateAvailable'] is False
    assert body['url'].startswith('https://github.com/')
