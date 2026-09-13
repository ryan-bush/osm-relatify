import asyncio

import pytest
import xmltodict
from fastapi import HTTPException
from pydantic import ValidationError

from bus_stop_creation import NewBusStop
from main import PostDownloadOsmChangeModel
from models.final_route import FinalRoute
from models.relation_member import RelationMember
from relation_builder import build_osm_change

BUS_TAGS = {'type': 'route', 'route': 'bus', 'public_transport:version': '2', 'name': 'Bus 12'}


def _route(members, tags=BUS_TAGS):
    return FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=tags,
        extraWaysToUpdate=(),
        members=tuple(members),
        warnings=(),
    )


def _stop(id=-1, tags=None, **kwargs):
    return NewBusStop(id=id, lat=51.5, lon=-0.12, tags=tags if tags is not None else {'name': 'High Street'}, **kwargs)


def _build_xml(members, new_stops, tags=BUS_TAGS) -> str:
    return asyncio.run(
        build_osm_change(
            None,
            _route(members, tags),
            include_changeset_id=False,
            # neither is touched: nothing is split, and a new relation needs no fetch
            overpass=None,
            osm=None,
            tags_edited=tags,
            new_stops=new_stops,
        )
    )


def _created(members, new_stops, tags=BUS_TAGS) -> dict:
    return xmltodict.parse(_build_xml(members, new_stops, tags), force_list=('node', 'member', 'tag'))['osmChange'][
        'create'
    ]


def _tags(element: dict) -> dict[str, str]:
    return {t['@k']: t['@v'] for t in element['tag']}


PLATFORM = RelationMember(id='-1', type='node', role='platform')
WAY = RelationMember(id='201', type='way', role='')


def test_new_stop_is_created_as_a_bus_platform():
    (node,) = _created([PLATFORM, WAY], [_stop(tags={'name': 'High Street', 'shelter': 'yes'})])['node']

    assert node['@id'] == '-1'
    assert _tags(node) == {
        'name': 'High Street',
        'shelter': 'yes',
        'highway': 'bus_stop',
        'public_transport': 'platform',
        'bus': 'yes',
    }


def test_coordinates_are_written_to_osm_precision():
    (node,) = _created([PLATFORM], [NewBusStop(id=-1, lat=51.123456789, lon=-0.1, tags={'name': 'A'})])['node']

    assert (node['@lat'], node['@lon']) == ('51.1234568', '-0.1000000')


def test_relation_refers_to_the_placeholder_id():
    relation = _created([PLATFORM, WAY], [_stop()])['relation']

    assert relation['member'][0] == {'@type': 'node', '@ref': '-1', '@role': 'platform'}


def test_nodes_are_created_before_the_relation_that_refers_to_them():
    xml = _build_xml([PLATFORM, WAY], [_stop()])

    assert xml.index('<node') < xml.index('<relation')


def test_trolleybus_stop_is_served_by_trolleybuses():
    tags = {**BUS_TAGS, 'route': 'trolleybus'}
    (node,) = _created([PLATFORM], [_stop()], tags)['node']

    assert _tags(node)['trolleybus'] == 'yes'
    assert 'bus' not in _tags(node)


def test_user_tags_cannot_override_what_makes_it_a_stop():
    (node,) = _created([PLATFORM], [_stop(tags={'name': 'A', 'highway': 'street_lamp', 'bus': 'no'})])['node']

    assert _tags(node)['highway'] == 'bus_stop'
    assert _tags(node)['bus'] == 'yes'


def test_blank_values_are_dropped():
    (node,) = _created([PLATFORM], [_stop(tags={'name': ' A ', 'local_ref': '  '})])['node']

    assert _tags(node)['name'] == 'A'
    assert 'local_ref' not in _tags(node)


@pytest.mark.parametrize('tags', [{}, {'name': '   '}, {'local_ref': 'B'}])
def test_a_stop_without_a_name_is_rejected(tags):
    with pytest.raises(HTTPException) as e:
        _build_xml([PLATFORM], [_stop(tags=tags)])

    assert e.value.status_code == 400


def test_tram_routes_cannot_add_stops():
    with pytest.raises(HTTPException):
        _build_xml([PLATFORM], [_stop()], {**BUS_TAGS, 'route': 'tram'})


def test_a_member_placeholder_without_its_stop_is_rejected():
    with pytest.raises(HTTPException) as e:
        _build_xml([PLATFORM, WAY], [])

    assert '-1' in e.value.detail


def test_duplicate_ids_are_rejected():
    with pytest.raises(HTTPException):
        _build_xml([PLATFORM], [_stop(), _stop()])


def test_control_characters_in_tags_are_rejected():
    with pytest.raises(HTTPException):
        _build_xml([PLATFORM], [_stop(tags={'name': 'A\x07'})])


def test_no_new_stops_creates_no_nodes():
    assert '<node' not in _build_xml([WAY], [])


@pytest.mark.parametrize('field', [{'id': 1}, {'id': 0}, {'lat': 91}, {'lon': -181}])
def test_invalid_stops_fail_validation(field):
    with pytest.raises(ValidationError):
        NewBusStop(**{'id': -1, 'lat': 0, 'lon': 0, 'tags': {}, **field})


@pytest.mark.parametrize(
    ('count', 'expected'),
    [
        (0, 'Updated route: Bus 12, #7'),
        (1, 'Updated route: Bus 12, #7; added 1 bus stop'),
        (2, 'Updated route: Bus 12, #7; added 2 bus stops'),
    ],
)
def test_comment_counts_added_stops(count, expected):
    model = PostDownloadOsmChangeModel(
        relationId=7,
        route={},
        tags={'name': 'Bus 12'},
        newStops=[_stop(id=-(i + 1)) for i in range(count)],
    )

    assert model.make_comment() == expected


def test_custom_comment_is_left_alone_when_stops_are_added():
    model = PostDownloadOsmChangeModel(relationId=7, route={}, tags={}, comment='Survey', newStops=[_stop()])

    assert model.make_comment() == 'Survey'
