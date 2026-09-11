import csv
import os
import time

import pytest

from bus_stop_creation import NewBusStop
from main import PostDownloadOsmChangeModel
from models.bounding_box import BoundingBox
from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection, PublicTransport
from models.naptan_stop import NaptanStop
from naptan import NaptanStore, build_database, find_unmapped_stops, parse_row

COLUMNS = (
    'ATCOCode',
    'NaptanCode',
    'CommonName',
    'Street',
    'Indicator',
    'Bearing',
    'Longitude',
    'Latitude',
    'StopType',
    'BusStopType',
    'Status',
)


def _row(**overrides) -> dict[str, str]:
    return {
        'ATCOCode': '639000011',
        'NaptanCode': '23234375',
        'CommonName': 'Union Grove',
        'Street': 'Union Grove',
        'Indicator': 'o/s 103',
        'Bearing': 'SW',
        'Longitude': '-2.117499',
        'Latitude': '57.141117',
        'StopType': 'BCT',
        'BusStopType': 'MKD',
        'Status': 'active',
        **overrides,
    }


def test_marked_stop_becomes_platform_tags():
    stop = parse_row(_row())

    assert stop.atcoCode == '639000011'
    assert stop.latLng == (57.141117, -2.117499)
    assert stop.tags == {
        'name': 'Union Grove',
        'naptan:AtcoCode': '639000011',
        'naptan:NaptanCode': '23234375',
        'naptan:CommonName': 'Union Grove',
        'naptan:Indicator': 'o/s 103',
        'naptan:Street': 'Union Grove',
        'naptan:Bearing': 'SW',
    }


@pytest.mark.parametrize(
    ('indicator', 'local_ref'),
    [('Stop A', 'A'), ('Stop P1', 'P1'), ('Stance 1', '1'), ('bay 12', '12'), ('Stand c', 'C')],
)
def test_stop_letters_become_local_ref(indicator, local_ref):
    assert parse_row(_row(Indicator=indicator)).tags['local_ref'] == local_ref


@pytest.mark.parametrize('indicator', ['opp', 'o/s 103', '->N', 'N-bound', 'Stop outside the shop', ''])
def test_descriptive_indicators_are_not_local_ref(indicator):
    assert 'local_ref' not in parse_row(_row(Indicator=indicator)).tags


def test_empty_fields_are_left_out():
    tags = parse_row(_row(NaptanCode='', Street=' ', Bearing='')).tags

    assert 'naptan:NaptanCode' not in tags
    assert 'naptan:Street' not in tags
    assert 'naptan:Bearing' not in tags


@pytest.mark.parametrize(
    'overrides',
    [
        {'Status': 'inactive'},
        {'StopType': 'RSE'},
        {'BusStopType': 'HAR'},
        {'BusStopType': 'FLX'},
        {'BusStopType': 'CUS'},
        {'CommonName': ' '},
        {'Latitude': ''},
    ],
)
def test_stops_without_a_mappable_pole_are_skipped(overrides):
    assert parse_row(_row(**overrides)) is None


@pytest.mark.parametrize('stop_type', ['BCS', 'BCQ'])
def test_bus_station_bays_are_kept(stop_type):
    assert parse_row(_row(StopType=stop_type, BusStopType='')) is not None


def _write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_database_serves_stops_within_a_box(tmp_path):
    _write_csv(
        tmp_path / 'naptan.csv',
        [
            _row(),
            _row(ATCOCode='639000099', Latitude='51.5', Longitude='-0.1'),
            _row(ATCOCode='639000050', Status='inactive'),
        ],
    )
    build_database(tmp_path / 'naptan.csv', tmp_path / 'naptan.sqlite')

    stops = NaptanStore(tmp_path).stops_within([BoundingBox(57.14, -2.12, 57.15, -2.11)])

    assert [s.atcoCode for s in stops] == ['639000011']
    assert stops[0].indicator == 'o/s 103'
    assert stops[0].tags['naptan:Bearing'] == 'SW'


def test_download_without_bus_stops_keeps_the_previous_database(tmp_path):
    _write_csv(tmp_path / 'good.csv', [_row()])
    build_database(tmp_path / 'good.csv', tmp_path / 'naptan.sqlite')

    _write_csv(tmp_path / 'bad.csv', [])
    with pytest.raises(ValueError, match='no bus stops'):
        build_database(tmp_path / 'bad.csv', tmp_path / 'naptan.sqlite')

    assert NaptanStore(tmp_path).stops_within([BoundingBox(57, -3, 58, -2)])
    assert not list(tmp_path.glob('*.tmp'))


def test_missing_database_serves_nothing(tmp_path):
    assert NaptanStore(tmp_path).stops_within([BoundingBox(57, -3, 58, -2)]) == []


def test_staleness_follows_the_database_age(tmp_path):
    store = NaptanStore(tmp_path)
    assert store.is_stale()

    _write_csv(tmp_path / 'naptan.csv', [_row()])
    build_database(tmp_path / 'naptan.csv', tmp_path / 'naptan.sqlite')
    assert not store.is_stale()

    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(tmp_path / 'naptan.sqlite', (two_days_ago, two_days_ago))
    assert store.is_stale()


def _naptan(code, lat_lng, name='Union Grove', local_ref=None):
    tags = {'name': name, **({'local_ref': local_ref} if local_ref else {})}
    return NaptanStop(atcoCode=code, name=name, indicator='', latLng=lat_lng, tags=tags)


def _osm(id, lat_lng, tags):
    platform = FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=False,
        latLng=lat_lng,
        tags=tags,
        name=tags.get('name', ''),
        groupName='',
        highway='bus_stop',
        public_transport=PublicTransport.PLATFORM,
    )
    return FetchRelationBusStopCollection(platform=platform, stop=None)


# about 25 m apart, either side of a road
NORTH_SIDE = (57.14120, -2.11750)
SOUTH_SIDE = (57.14098, -2.11750)


def _codes(stops):
    return [s.atcoCode for s in stops]


def test_osm_stop_with_the_code_is_mapped():
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE)],
        [_osm('1', (57.2, -2.2), {'name': 'Somewhere Else', 'naptan:AtcoCode': 'A'})],
    )

    assert unmapped == []


def test_one_osm_stop_can_carry_several_codes():
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE), _naptan('B', SOUTH_SIDE)],
        [_osm('1', NORTH_SIDE, {'name': 'Union Grove', 'naptan:AtcoCode': 'A;B'})],
    )

    assert unmapped == []


def test_osm_stop_without_a_code_matches_by_name_and_distance():
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE)], [_osm('1', (57.14130, -2.11750), {'name': 'Union Grove'})]
    )

    assert unmapped == []


def test_mapped_stop_does_not_hide_its_twin_across_the_road():
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE), _naptan('B', SOUTH_SIDE)],
        [_osm('1', (57.14125, -2.11750), {'name': 'Union Grove'})],
    )

    assert _codes(unmapped) == ['B']


def test_a_differently_named_stop_nearby_is_not_a_match():
    unmapped = find_unmapped_stops([_naptan('A', NORTH_SIDE)], [_osm('1', NORTH_SIDE, {'name': 'Holburn Street'})])

    assert _codes(unmapped) == ['A']


def test_a_same_named_stop_far_away_is_not_a_match():
    unmapped = find_unmapped_stops([_naptan('A', NORTH_SIDE)], [_osm('1', (57.1430, -2.1175), {'name': 'Union Grove'})])

    assert _codes(unmapped) == ['A']


def test_an_osm_stop_matched_by_code_does_not_hide_its_twin():
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE), _naptan('B', SOUTH_SIDE)],
        [_osm('1', NORTH_SIDE, {'name': 'Union Grove', 'naptan:AtcoCode': 'A'})],
    )

    assert _codes(unmapped) == ['B']


def test_a_code_naptan_no_longer_lists_still_allows_a_name_match():
    """Adelphi G5 in Aberdeen: OSM kept an old code after NaPTAN renumbered the stop."""
    unmapped = find_unmapped_stops(
        [_naptan('NEW', NORTH_SIDE, 'Adelphi', 'G5')],
        [_osm('1', (57.14130, -2.11750), {'name': 'Adelphi', 'local_ref': 'G5', 'naptan:AtcoCode': 'OLD'})],
    )

    assert unmapped == []


def test_a_second_naptan_record_for_the_same_letter_is_matched_to_the_coded_stop():
    """Music Hall C1 in Aberdeen: NaPTAN lists two active records for the one stop."""
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE, 'Music Hall', 'C1'), _naptan('DUP', (57.14125, -2.11750), 'Music Hall', 'C1')],
        [_osm('1', NORTH_SIDE, {'name': 'Music Hall', 'local_ref': 'C1', 'naptan:AtcoCode': 'A'})],
    )

    assert unmapped == []


def test_stops_with_different_letters_are_not_matched():
    """Guild Street M5 in Aberdeen is missing while M3 beside it is mapped."""
    unmapped = find_unmapped_stops(
        [_naptan('M5', NORTH_SIDE, 'Guild Street', 'M5')],
        [_osm('1', (57.14130, -2.11750), {'name': 'Guild Street', 'local_ref': 'm3'})],
    )

    assert _codes(unmapped) == ['M5']


def test_a_coded_stop_does_not_take_a_neighbour_without_a_letter():
    unmapped = find_unmapped_stops(
        [_naptan('A', NORTH_SIDE, 'Queens Links'), _naptan('B', SOUTH_SIDE, 'Queens Links')],
        [_osm('1', NORTH_SIDE, {'name': 'Queens Links', 'naptan:AtcoCode': 'A'})],
    )

    assert _codes(unmapped) == ['B']


def _changeset_tags(new_stops):
    return PostDownloadOsmChangeModel(
        relationId=7, route={}, tags={'name': 'Bus 12'}, newStops=new_stops
    ).make_changeset_tags()


def test_changeset_credits_naptan_when_a_stop_came_from_it():
    stop = NewBusStop(id=-1, lat=57.1, lon=-2.1, tags={'name': 'Union Grove', 'naptan:AtcoCode': '639000011'})

    assert _changeset_tags([stop])['source'] == 'NaPTAN'


def test_changeset_has_no_source_without_naptan_stops():
    stop = NewBusStop(id=-1, lat=57.1, lon=-2.1, tags={'name': 'Union Grove'})

    assert 'source' not in _changeset_tags([stop])
    assert _changeset_tags([])['comment'] == 'Updated route: Bus 12, #7'
