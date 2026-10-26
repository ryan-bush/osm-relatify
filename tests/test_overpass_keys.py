"""
Instances that need a key: it reaches them, it stays out of the logs, and one they refuse
hands the query on rather than failing it.
"""

import asyncio
from types import MappingProxyType

import httpx

from config import OverpassInstance
from overpass import overpass_post

KEYED = OverpassInstance(
    'https://overpass.example/my-secret/api/interpreter',
    'overpass.example',
    MappingProxyType({'Authorization': 'Bearer secret'}),
)
PUBLIC = OverpassInstance('https://overpass-api.de/api/interpreter', 'https://overpass-api.de/api/interpreter')


def _post(monkeypatch, replies):
    sent = []

    async def fake_post(url, **kwargs):
        sent.append((url, dict(kwargs.get('headers') or {})))
        code = replies.pop(0) if replies else 200
        body = '{"elements":[]}' if code == 200 else 'nope'
        return httpx.Response(code, text=body, request=httpx.Request('POST', url))

    async def fake_sleep(delay):
        pass

    monkeypatch.setattr('overpass.OVERPASS_INSTANCES', (KEYED, PUBLIC))
    monkeypatch.setattr('overpass.HTTP.post', fake_post)
    monkeypatch.setattr('overpass.asyncio.sleep', fake_sleep)
    return sent


def test_sends_the_instances_own_headers(monkeypatch):
    sent = _post(monkeypatch, [])
    asyncio.run(overpass_post('[out:json];out count;', 30))

    assert sent == [(KEYED.url, {'Authorization': 'Bearer secret'})]


def test_a_refused_key_hands_on_at_once_and_stays_out_of_the_log(monkeypatch, capsys):
    sent = _post(monkeypatch, [401])
    asyncio.run(overpass_post('[out:json];out count;', 30))

    assert [url for url, _ in sent] == [KEYED.url, PUBLIC.url], 'not retried: the key will not start working'
    assert sent[1][1] == {}, "one instance's key is not sent to another"

    out = capsys.readouterr().out
    assert 'overpass.example refused the key' in out
    assert 'secret' not in out
