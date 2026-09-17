"""
Saying what went wrong when Overpass cannot be reached at all.

httpx reports every failure to open a connection the same way - "All connection attempts
failed" - whether the name would not resolve, the machine refused, or the network went
away. Which of those it was decides where to look, so the reason underneath is what gets
logged, and a request that reached nothing says so rather than blaming Overpass for being
busy.
"""

import asyncio
import socket

import httpx
import pytest
from fastapi import HTTPException

from overpass import describe_transport_error, overpass_post

URL = 'https://overpass-api.de/api/interpreter'


def _connect_error(cause: BaseException | None) -> httpx.ConnectError:
    """An httpx error wrapped around an operating system one, as httpx raises it."""
    error = httpx.ConnectError('All connection attempts failed')
    if cause is not None:
        error.__cause__ = cause
    return error


class TestDescribeTransportError:
    def test_names_the_machine_that_refused(self):
        refused = ConnectionRefusedError(61, "Connect call failed ('65.109.112.52', 443)")

        assert describe_transport_error(_connect_error(refused)) == (
            "ConnectionRefusedError: [Errno 61] Connect call failed ('65.109.112.52', 443)"
        )

    def test_tells_a_name_that_would_not_resolve_apart_from_a_refusal(self):
        dns = socket.gaierror(8, 'nodename nor servname provided, or not known')

        assert 'nodename nor servname provided' in describe_transport_error(_connect_error(dns))

    def test_digs_through_the_layers_httpx_wraps_it_in(self):
        """The real reason is several __cause__ links down, which is why it is never seen."""
        deep = OSError(65, 'No route to host')
        middle = OSError('All connection attempts failed')
        middle.__cause__ = deep

        assert 'No route to host' in describe_transport_error(_connect_error(middle))

    def test_says_each_reason_when_the_addresses_failed_differently(self):
        # the name has an A and a AAAA record, and they can fail for different reasons
        group = BaseExceptionGroup(
            'multiple',
            [OSError(65, 'No route to host'), ConnectionRefusedError(61, 'Connection refused')],
        )

        described = describe_transport_error(_connect_error(group))

        assert 'No route to host' in described
        assert 'Connection refused' in described

    def test_the_same_reason_twice_is_said_once(self):
        group = BaseExceptionGroup('multiple', [OSError(65, 'No route to host'), OSError(65, 'No route to host')])

        assert describe_transport_error(_connect_error(group)).count('No route to host') == 1

    def test_an_error_with_nothing_underneath_is_still_described(self):
        assert 'All connection attempts failed' in describe_transport_error(_connect_error(None))

    def test_a_timeout_is_described_by_its_kind(self):
        assert 'ConnectTimeout' in describe_transport_error(httpx.ConnectTimeout('timed out'))

    def test_the_cancellation_that_enforces_a_timeout_is_not_the_reason(self):
        """A connect that ran out of time ends in anyio cancelling it, which says nothing."""
        timeout = httpx.ConnectTimeout('timed out')
        timeout.__cause__ = asyncio.CancelledError('Cancelled via cancel scope; reason: deadline exceeded')

        described = describe_transport_error(timeout)

        assert 'cancel scope' not in described
        assert 'ConnectTimeout' in described


class TestNothingAnswered:
    def _all_fail(self, monkeypatch, error):
        async def fake_post(url, **kwargs):  # noqa: ARG001
            raise error

        async def fake_sleep(delay):
            pass

        monkeypatch.setattr('overpass.HTTP.post', fake_post)
        monkeypatch.setattr('overpass.asyncio.sleep', fake_sleep)

    def test_a_request_that_reached_nothing_says_so(self, monkeypatch):
        self._all_fail(monkeypatch, _connect_error(ConnectionRefusedError(61, 'Connection refused')))

        with pytest.raises(HTTPException) as raised:
            asyncio.run(overpass_post('[out:json];out count;', 30))

        assert raised.value.status_code == 503
        assert 'Could not reach any Overpass instance' in raised.value.detail
        assert 'check the network' in raised.value.detail

    def test_an_instance_that_answered_is_not_blamed_on_the_network(self, monkeypatch):
        """A 504 from one and no connection to the other is still Overpass being busy."""
        sent = []

        async def fake_post(url, **kwargs):  # noqa: ARG001
            sent.append(url)
            if len(sent) == 1:
                return httpx.Response(504, text='busy', request=httpx.Request('POST', url))
            raise _connect_error(ConnectionRefusedError(61, 'Connection refused'))

        async def fake_sleep(delay):
            pass

        monkeypatch.setattr('overpass.HTTP.post', fake_post)
        monkeypatch.setattr('overpass.asyncio.sleep', fake_sleep)

        with pytest.raises(HTTPException) as raised:
            asyncio.run(overpass_post('[out:json];out count;', 30))

        assert 'Could not reach' not in raised.value.detail
        assert 'currently unavailable' in raised.value.detail

    def test_the_reason_reaches_the_log(self, monkeypatch, capsys):
        self._all_fail(monkeypatch, _connect_error(socket.gaierror(8, 'nodename nor servname provided')))

        with pytest.raises(HTTPException):
            asyncio.run(overpass_post('[out:json];out count;', 30))

        printed = capsys.readouterr().out
        assert 'nodename nor servname provided' in printed
        assert 'All connection attempts failed' not in printed, 'that is the line that says nothing'
