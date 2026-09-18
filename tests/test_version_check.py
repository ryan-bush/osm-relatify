"""The release comparison behind the navbar's update notice."""

import pytest
from fastapi.testclient import TestClient

import version_check
from config import APP_VERSION, CREATED_BY, USER_AGENT, make_created_by, make_user_agent
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


# a changeset names the release it came from, which is a thing a reader can look up,
# and the build that wrote it
def test_changesets_are_stamped_with_the_release_and_the_build():
    assert make_created_by('1.1.0', 'ff422gu') == 'Relatify 1.1.0 #ff422gu'


# neither half is invented when it cannot be read
def test_a_changeset_stamp_leaves_out_what_it_does_not_know():
    assert make_created_by('1.1.0', '') == 'Relatify 1.1.0'
    assert make_created_by('', 'ff422gu') == 'Relatify #ff422gu'
    assert make_created_by('', '') == 'Relatify'


# a server log and a changeset name the same release and the same build
def test_the_user_agent_carries_the_release_and_the_build():
    assert (
        make_user_agent('1.1.0', 'ff422gu', 'https://example.test')
        == 'Relatify/1.1.0 #ff422gu (+https://example.test)'
    )


def test_a_user_agent_leaves_out_what_it_does_not_know():
    assert make_user_agent('1.1.0', '', 'https://example.test') == 'Relatify/1.1.0 (+https://example.test)'
    assert make_user_agent('', 'ff422gu', 'https://example.test') == 'Relatify #ff422gu (+https://example.test)'


# OSM asks for a contact address in the user agent, whatever else is known
def test_the_running_user_agent_points_somewhere():
    assert USER_AGENT.startswith('Relatify')
    assert '(+http' in USER_AGENT


def test_the_running_stamp_names_the_running_version():
    assert CREATED_BY.startswith(f'Relatify {APP_VERSION}' if APP_VERSION else 'Relatify')


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
