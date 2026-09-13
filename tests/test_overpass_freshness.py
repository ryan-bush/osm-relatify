"""Refusing an Overpass instance that has fallen behind live OSM."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from overpass import data_age


def _response(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, request=httpx.Request('POST', 'https://example.test/'))


def _stamp(behind: timedelta) -> str:
    return (datetime.now(UTC) - behind).strftime('%Y-%m-%dT%H:%M:%SZ')


def test_reads_the_timestamp_a_json_reply_carries():
    stamp = _stamp(timedelta(minutes=5))
    body = f'{{"version":0.6,"generator":"Overpass API","osm3s":{{"timestamp_osm_base":"{stamp}"}},"elements":[]}}'

    assert data_age(_response(body)) == pytest.approx(300, abs=30)


def test_reads_the_timestamp_an_xml_reply_carries():
    body = f'<?xml version="1.0"?><osm><meta osm_base="{_stamp(timedelta(hours=2))}"/></osm>'

    assert data_age(_response(body)) == pytest.approx(7200, abs=60)


def test_the_six_week_old_instance_that_started_all_this():
    body = '{"osm3s":{"timestamp_osm_base":"2026-07-28T02:16:18Z"},"elements":[]}'
    age = data_age(_response(body))

    assert age is not None
    assert age > 30 * 86_400


def test_a_reply_that_does_not_say_is_not_judged():
    assert data_age(_response('{"elements":[]}')) is None


def test_an_unreadable_timestamp_is_not_judged():
    assert data_age(_response('{"osm3s":{"timestamp_osm_base":"whenever"}}')) is None
