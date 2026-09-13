"""Pairing each platform with the stop position that serves it."""

from bus_collection_builder import build_bus_stop_collections
from models.fetch_relation import FetchRelationBusStop

LAT = 51.5574


def _stop(id, tags, lat=LAT, lon=-1.7792):
    return FetchRelationBusStop.from_data({'id': id, 'type': 'node', 'lat': lat, 'lon': lon, 'tags': tags})


def _platform(id, name='Durham Street', **kwargs):
    return _stop(id, {'name': name, 'public_transport': 'platform', 'highway': 'bus_stop', 'bus': 'yes'}, **kwargs)


def _position(id, name='Durham Street', **kwargs):
    return _stop(id, {'name': name, 'public_transport': 'stop_position', 'bus': 'yes'}, **kwargs)


def _pairs(bus_stops):
    return [
        (c.platform.id if c.platform else None, c.stop.id if c.stop else None)
        for c in build_bus_stop_collections(bus_stops)
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
