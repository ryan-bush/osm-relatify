"""Pairing each platform with the stop position that serves it."""

from bus_collection_builder import build_bus_stop_collections, stop_position_headings
from models.fetch_relation import FetchRelationBusStop

LAT = 51.5574


def _stop(id, tags, lat=LAT, lon=-1.7792):
    return FetchRelationBusStop.from_data({'id': id, 'type': 'node', 'lat': lat, 'lon': lon, 'tags': tags})


def _platform(id, name='Durham Street', **kwargs):
    return _stop(id, {'name': name, 'public_transport': 'platform', 'highway': 'bus_stop', 'bus': 'yes'}, **kwargs)


def _position(id, name='Durham Street', **kwargs):
    return _stop(id, {'name': name, 'public_transport': 'stop_position', 'bus': 'yes'}, **kwargs)


def _pairs(bus_stops, headings=None):
    return [
        (c.platform.id if c.platform else None, c.stop.id if c.stop else None)
        for c in build_bus_stop_collections(bus_stops, headings)
    ]


def test_each_side_of_the_road_keeps_its_own_stop_position():
    pairs = _pairs([_platform(1, lat=LAT + 0.0001), _platform(2), _position(3, lat=LAT + 0.00011)])

    assert sorted(pairs) == [('1', '3'), ('2', None)]


def test_the_far_platform_is_left_without_one_to_offer_it_its_own():
    """One node cannot be where the buses halt in both directions, so it serves one side."""
    pairs = _pairs([_platform(1), _platform(2, lon=-1.7794), _position(3, lon=-1.77941)])

    assert sorted(pairs) == [('1', None), ('2', '3')]


def test_a_platform_and_its_stop_position_still_pair_up():
    assert _pairs([_platform(1), _position(2)]) == [('1', '2')]


def test_a_bare_named_stop_position_joins_only_the_side_it_stands_on():
    """
    The two sides of a road are told apart by the ref in their names, and the stop
    position between them carries the bare name, matching both of them equally well.
    """
    pairs = _pairs(
        [
            _stop(
                1,
                {'name': 'Durham Street', 'ref': 'swimjdj', 'public_transport': 'platform', 'highway': 'bus_stop'},
                lat=51.5575443,
                lon=-1.7791651,
            ),
            _stop(
                2,
                {'name': 'Durham Street', 'ref': 'swidmat', 'public_transport': 'platform', 'highway': 'bus_stop'},
                lat=51.5574168,
                lon=-1.7792915,
            ),
            _position(3, lat=51.5574325, lon=-1.7792146),
        ]
    )

    assert sorted(pairs) == [('1', None), ('2', '3')]


def test_the_platform_without_a_ref_does_not_take_a_stop_position_off_the_far_side():
    """
    A stop position carries the bare name of its place, so the platform whose name has no
    ref matches it exactly and would take it whichever side of the road it stands on.
    """
    pairs = _pairs(
        [
            _stop(
                1,
                {'name': 'Bladen Close', 'public_transport': 'platform', 'highway': 'bus_stop'},
                lat=51.5274661,
                lon=-1.8007128,
            ),
            _stop(
                2,
                {'name': 'Bladen Close', 'ref': 'swiapwm', 'public_transport': 'platform', 'highway': 'bus_stop'},
                lat=51.5272639,
                lon=-1.8006972,
            ),
            _position(3, 'Bladen Close', lat=51.5272912, lon=-1.8006336),
        ]
    )

    assert sorted(pairs) == [('1', None), ('2', '3')]


_HIGH_STREET = [
    ('1465269924', 51.5230835, -1.7929290, 'platform', 'swiawmp'),
    ('1574764342', 51.5229704, -1.7928543, 'platform', None),
    ('14176056501', 51.5230096, -1.7929133, 'stop_position', None),
    ('14177644556', 51.5230082, -1.7929296, 'stop_position', None),
]


def _terminus():
    result = []
    for id, lat, lon, kind, ref in _HIGH_STREET:
        tags = {'name': 'High Street', 'public_transport': kind}
        if kind == 'platform':
            tags['highway'] = 'bus_stop'
        if ref:
            tags['ref'] = ref
        result.append(_stop(int(id), tags, lat=lat, lon=lon))
    return result


def test_a_spare_stop_position_is_not_dropped():
    """
    One name group held both stop positions and only one platform, and the one left over
    fell out of the collections: gone from the map, gone from the route on upload, and
    invisible to the platform that would then be offered a second one on top of it.
    """
    collections = build_bus_stop_collections(_terminus())

    kept = {c.stop.id for c in collections if c.stop is not None}
    assert kept == {'14176056501', '14177644556'}


def test_both_ends_of_a_terminus_keep_their_own_stop_position():
    pairs = _pairs(_terminus())

    assert sorted(pairs) == [('1465269924', '14177644556'), ('1574764342', '14176056501')]


# Capel Sardis in Anglesey: the stop position is for south-westbound buses, and stands
# nearer the north-eastbound platform than its own
SARDIS_SW = {'lat': 53.27326, 'lon': -4.5830832}
SARDIS_NE = {'lat': 53.2733668, 'lon': -4.5831179}
SARDIS_STOP = {'lat': 53.2733206, 'lon': -4.5831284}


def _sardis_platform(id, where, bearing):
    tags = {
        'name': 'Capel Sardis',
        'public_transport': 'platform',
        'highway': 'bus_stop',
        'bus': 'yes',
        'naptan:Bearing': bearing,
    }
    return _stop(id, tags, **where)


def _sardis_stop(direction='backward'):
    tags = {'name': 'Capel Sardis', 'public_transport': 'stop_position', 'bus': 'yes', 'direction': direction}
    return _stop(3, tags, **SARDIS_STOP)


def test_a_stop_position_goes_to_the_platform_its_buses_call_at():
    stops = [_sardis_platform(1, SARDIS_SW, 'SW'), _sardis_platform(2, SARDIS_NE, 'NE'), _sardis_stop()]

    assert sorted(_pairs(stops, {'3': 246.0})) == [('1', '3'), ('2', None)]


def test_without_a_direction_the_nearest_platform_takes_it():
    stops = [_sardis_platform(1, SARDIS_SW, 'SW'), _sardis_platform(2, SARDIS_NE, 'NE'), _sardis_stop()]

    assert sorted(_pairs(stops)) == [('1', None), ('2', '3')]


def test_a_lone_platform_is_not_given_a_stop_position_for_the_other_direction():
    stops = [_sardis_platform(2, SARDIS_NE, 'NE'), _sardis_stop()]

    assert sorted(_pairs(stops, {'3': 246.0}), key=str) == [('2', None), (None, '3')]


def _road(nodes):
    return {'id': 9, 'nodes': nodes, 'tags': {}}


COORDINATES = {1: (53.0, -4.001), 2: (53.0, -4.0), 3: (53.0, -3.999)}


def test_headings_follow_the_way_direction():
    stop = _stop(2, {'name': 'X', 'public_transport': 'stop_position', 'direction': 'forward'})
    back = _stop(2, {'name': 'X', 'public_transport': 'stop_position', 'direction': 'backward'})

    assert round(stop_position_headings([stop], [_road([1, 2, 3])], COORDINATES)['2']) == 90
    assert round(stop_position_headings([back], [_road([1, 2, 3])], COORDINATES)['2']) == 270


def test_a_stop_position_without_a_one_way_direction_has_no_heading():
    both = _stop(2, {'name': 'X', 'public_transport': 'stop_position', 'direction': 'both'})
    untagged = _stop(2, {'name': 'X', 'public_transport': 'stop_position'})

    assert stop_position_headings([both, untagged], [_road([1, 2, 3])], COORDINATES) == {}


def test_a_stop_position_where_ways_meet_has_no_heading():
    stop = _stop(2, {'name': 'X', 'public_transport': 'stop_position', 'direction': 'forward'})

    assert stop_position_headings([stop], [_road([1, 2]), _road([2, 3])], COORDINATES) == {}
