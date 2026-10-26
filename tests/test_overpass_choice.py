"""The Overpass instance picked in the navbar is the only one a request's queries use."""

import asyncio

import pytest

from config import OVERPASS_INSTANCE_COOKIE, OverpassInstance
from overpass import OVERPASS_CHOICE, overpass_instances
from overpass_choice_middleware import OverpassChoiceMiddleware

FIRST = OverpassInstance('https://first.example/api/interpreter', 'first.example')
SECOND = OverpassInstance('https://second.example/api/interpreter', 'second.example')


@pytest.fixture(autouse=True)
def _instances(monkeypatch):
    monkeypatch.setattr('overpass.OVERPASS_INSTANCES', (FIRST, SECOND))


@pytest.mark.parametrize('choice', [None, '', 'gone.example'])
def test_no_choice_or_an_unknown_one_tries_every_instance(choice):
    token = OVERPASS_CHOICE.set(choice)
    try:
        assert overpass_instances() == (FIRST, SECOND)
    finally:
        OVERPASS_CHOICE.reset(token)


def test_a_chosen_instance_is_used_on_its_own():
    token = OVERPASS_CHOICE.set('second.example')
    try:
        assert overpass_instances() == (SECOND,)
    finally:
        OVERPASS_CHOICE.reset(token)


@pytest.mark.parametrize('scope_type', ['http', 'websocket'])
def test_the_middleware_reads_the_choice_from_the_cookie(scope_type):
    seen = []

    async def app(scope, receive, send):  # noqa: ARG001
        seen.append(overpass_instances())

    scope = {
        'type': scope_type,
        'headers': [(b'cookie', f'access_token=x; {OVERPASS_INSTANCE_COOKIE}=second.example'.encode())],
    }
    asyncio.run(OverpassChoiceMiddleware(app)(scope, None, None))

    assert seen == [(SECOND,)]
    assert OVERPASS_CHOICE.get() is None, 'the choice does not outlive its request'
