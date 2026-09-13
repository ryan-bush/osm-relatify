"""Refusing an Overpass instance that has fallen behind live OSM."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from overpass import data_age, reply_error


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


REAL_ERROR_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
<head><title>OSM3S Response</title></head>
<body>
<p>The data included in this document is from www.openstreetmap.org.</p>
<p><strong style="color:#FF0000">Error</strong>: runtime error: open64: 0 Success
/osm3s_osm_base Dispatcher_Client::request_read_and_idx::timeout. The server is
probably too busy to handle your request. </p>
</body>
</html>"""


def _html(body: str) -> httpx.Response:
    return httpx.Response(
        200, text=body, headers={'content-type': 'text/html; charset=utf-8'},
        request=httpx.Request('POST', 'https://example.test/'),
    )


class TestReplyError:
    """Overpass gives some failures a 200, so the status code alone does not say."""

    def test_the_error_page_a_busy_instance_answers_with(self):
        reported = reply_error(_html(REAL_ERROR_PAGE))

        assert reported is not None
        assert 'too busy' in reported
        assert '<' not in reported, 'the markup is stripped out of the message'

    def test_a_page_with_no_error_paragraph_still_counts(self):
        assert reply_error(_html('<html><body><p>nothing useful</p></body></html>')) is not None

    def test_a_query_that_gave_up_part_way_through(self):
        # valid JSON, some of the data, and a remark saying the rest was never collected
        body = r'{"elements":[],"remark":"runtime error: Query timed out in \"recurse\" at line 1"}'
        reported = reply_error(_response(body))

        assert reported is not None
        assert reported.startswith('runtime error: Query timed out')

    def test_an_xml_reply_that_gave_up_part_way_through(self):
        body = '<?xml version="1.0"?><osm><remark>runtime error: Query timed out</remark></osm>'

        assert reply_error(_response(body)) == 'runtime error: Query timed out'

    def test_data_is_not_an_error(self):
        assert reply_error(_response('{"elements":[{"type":"node","id":1}]}')) is None

    def test_a_remark_that_is_not_an_error_is_left_alone(self):
        assert reply_error(_response('{"remark":"considered 3 areas","elements":[]}')) is None
