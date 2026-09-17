"""
Getting a query past a busy Overpass instance.

The public instances refuse work in two ways, and both were met repeatedly while adding
one route. A 429 says every query slot this address is given is busy; the status endpoint
says when the next frees up, which is what a retry has to wait for. A 504 is the
dispatcher turning a query away before running it, because it cannot promise the time and
memory the query declares — so each query declares what it actually needs.
"""

import asyncio

import httpx
import pytest

from driving_side import build_driving_side_query
from models.bounding_box import BoundingBox
from overpass import (
    _MAX_SLOT_WAIT,
    build_bb_query,
    build_parents_query,
    build_query,
    download_maxsize_mib,
    slot_wait,
)
from route_masters import build_route_master_candidates_query

# what overpass-api.de answers on /api/status
BUSY_STATUS = """Connected as: 527431106
Current time: 2026-09-17T17:37:55Z
Rate limit: 2
Slot available after: 2026-09-17T17:38:07Z, in 12 seconds.
Currently running queries (pid, space limit, time limit, start time):
"""

FREE_STATUS = """Connected as: 527431106
Current time: 2026-09-17T17:37:55Z
Rate limit: 2
2 slots available now.
"""

URL = 'https://overpass-api.de/api/interpreter'


def _status(monkeypatch, handler):
    async def fake_get(url, **kwargs):  # noqa: ARG001
        return handler(url)

    monkeypatch.setattr('overpass.HTTP.get', fake_get)


def _ok(body: str):
    return lambda url: httpx.Response(200, text=body, request=httpx.Request('GET', url))


class TestSlotWait:
    def test_asks_the_status_endpoint_of_the_instance_that_refused(self, monkeypatch):
        asked = []

        def handler(url):
            asked.append(url)
            return httpx.Response(200, text=FREE_STATUS, request=httpx.Request('GET', url))

        _status(monkeypatch, handler)
        asyncio.run(slot_wait(URL))

        assert asked == ['https://overpass-api.de/api/status']

    def test_reads_how_long_until_a_slot_frees_up(self, monkeypatch):
        _status(monkeypatch, _ok(BUSY_STATUS))

        assert asyncio.run(slot_wait(URL)) == 12

    def test_a_slot_that_is_free_again_is_no_wait_at_all(self, monkeypatch):
        _status(monkeypatch, _ok(FREE_STATUS))

        assert asyncio.run(slot_wait(URL)) == 0

    def test_the_free_line_is_read_rather_than_just_matched(self, monkeypatch):
        """Both lines appear together, and none free is a wait, not a slot."""
        _status(monkeypatch, _ok('0 slots available now.\nSlot available after: now, in 9 seconds.'))

        assert asyncio.run(slot_wait(URL)) == 9

    def test_an_instance_that_does_not_say_is_not_waited_for(self, monkeypatch):
        _status(monkeypatch, _ok('nothing useful here'))

        assert asyncio.run(slot_wait(URL)) is None

    def test_a_status_endpoint_that_is_down_is_not_waited_for(self, monkeypatch):
        def handler(url):
            raise httpx.ConnectError('refused', request=httpx.Request('GET', url))

        _status(monkeypatch, handler)

        assert asyncio.run(slot_wait(URL)) is None


class TestWaitingForASlot:
    """A 429 is retried when the slot comes back soon, and handed on when it does not."""

    def _post(self, monkeypatch, replies, slot):
        sent = []
        slept = []

        async def fake_post(url, **kwargs):  # noqa: ARG001
            sent.append(url)
            code = replies.pop(0) if replies else 200
            body = '{"elements":[]}' if code == 200 else 'rate limited'
            return httpx.Response(code, text=body, request=httpx.Request('POST', url))

        async def fake_sleep(delay):
            slept.append(delay)

        async def fake_slot_wait(url):  # noqa: ARG001
            return slot

        monkeypatch.setattr('overpass.HTTP.post', fake_post)
        monkeypatch.setattr('overpass.asyncio.sleep', fake_sleep)
        monkeypatch.setattr('overpass.slot_wait', fake_slot_wait)

        return sent, slept

    def test_waits_out_a_slot_that_comes_back_soon(self, monkeypatch):
        from overpass import overpass_post

        sent, slept = self._post(monkeypatch, [429], slot=5.0)
        asyncio.run(overpass_post('[out:json];out count;', 30))

        assert sent == [URL, URL], 'the same instance, once its slot is back'
        assert slept == [6.0], 'the wait it asked for, plus a second not to race it'

    def test_hands_a_long_wait_to_the_next_instance(self, monkeypatch):
        from overpass import overpass_post

        sent, _ = self._post(monkeypatch, [429], slot=_MAX_SLOT_WAIT + 30)
        asyncio.run(overpass_post('[out:json];out count;', 30))

        assert len(sent) == 2
        assert sent[1] != sent[0], 'a long wait for a slot is slower than asking elsewhere'

    def test_an_instance_that_will_not_say_is_not_retried(self, monkeypatch):
        from overpass import overpass_post

        sent, slept = self._post(monkeypatch, [429], slot=None)
        asyncio.run(overpass_post('[out:json];out count;', 30))

        assert sent[1] != sent[0]
        assert slept == [], 'nothing to wait for'


class TestDeclaredResources:
    """Every query says what it needs, rather than taking the 512 MiB default."""

    def _bb(self):
        return BoundingBox(minlat=53.3, minlon=-4.7, maxlat=53.4, maxlon=-4.6)

    @pytest.mark.parametrize(
        'query',
        [
            build_bb_query(1, 60),
            build_parents_query([1, 2], 60),
            build_driving_side_query(53.3, -4.7, 30),
            build_route_master_candidates_query(
                '4', 'bus', BoundingBox(minlat=53.3, minlon=-4.7, maxlat=53.4, maxlon=-4.6), 30
            ),
        ],
    )
    def test_each_query_declares_a_size(self, query):
        assert '[maxsize:' in query
        assert '[maxsize:536870912]' not in query, 'that is the default it is meant to come in under'

    def test_the_download_declares_a_size_too(self):
        query = build_query([self._bb()], [self._bb()], 60, 'bus')

        assert f'[maxsize:{128 * 1024 * 1024}]' in query

    def test_a_bigger_area_is_allowed_more(self):
        assert download_maxsize_mib(16) > download_maxsize_mib(1)

    def test_a_small_area_still_gets_room_to_work(self):
        assert download_maxsize_mib(1) == 128

    def test_no_area_is_allowed_past_the_default(self):
        # asking for more than Overpass gives by default would be refused more often, not less
        assert download_maxsize_mib(1000) == 512
