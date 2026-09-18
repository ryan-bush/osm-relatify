"""
Where the stop areas a download reports come from.

They used to be a query of their own, asked after every download: one more chance for a
busy Overpass instance to refuse, and it grew with the route, since every stop downloaded
so far was named in it. The download already asks for every stop_area relation in the
area, to name the stops that take their name from one, so the answer was there all along.
"""

from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection
from overpass import existing_stop_areas

AREA_TAGS = {'type': 'public_transport', 'public_transport': 'stop_area', 'name': 'The Square'}


def _area(id=99, tags=None, members=(('node', 1, 'platform'),)):
    return {
        'type': 'relation',
        'id': id,
        'tags': dict(AREA_TAGS if tags is None else tags),
        'members': [{'type': t, 'ref': r, 'role': role} for t, r, role in members],
    }


def _stop(id=1, type='node'):
    return FetchRelationBusStop.from_data({
        'id': id,
        'type': type,
        'lat': 53.3,
        'lon': -4.6,
        'tags': {'name': 'The Square', 'public_transport': 'platform', 'highway': 'bus_stop'},
    })


def _collection(*stops):
    return FetchRelationBusStopCollection(platform=stops[0], stop=stops[1] if len(stops) > 1 else None)


def test_an_area_a_downloaded_stop_is_in_is_reported():
    [area] = existing_stop_areas([_area()], [_collection(_stop(1))])

    assert area.id == 99
    assert area.name == 'The Square'
    assert area.members == ['node/1']


def test_an_area_none_of_these_stops_is_in_is_left_out():
    """It was downloaded because it is nearby, not because it groups anything here."""
    assert existing_stop_areas([_area(members=(('node', 7, 'platform'),))], [_collection(_stop(1))]) == []


def test_a_stop_position_counts_as_being_in_it():
    areas = existing_stop_areas(
        [_area(members=(('node', 2, 'stop'),))],
        [_collection(_stop(1), _stop(2))],
    )

    assert [area.id for area in areas] == [99]


def test_a_stop_mapped_as_a_way_is_matched_too():
    areas = existing_stop_areas([_area(members=(('way', 5, 'platform'),))], [_collection(_stop(5, type='way'))])

    assert [area.id for area in areas] == [99]


def test_an_area_reaching_into_two_downloaded_cells_is_reported_once():
    # each cell's query returns it, and the elements of every cell are merged
    areas = existing_stop_areas([_area(), _area()], [_collection(_stop(1))])

    assert len(areas) == 1


def test_members_outside_the_download_are_kept():
    """The client needs them to tell which of a group's stops the area is missing."""
    [area] = existing_stop_areas(
        [_area(members=(('node', 1, 'platform'), ('node', 8, 'platform')))],
        [_collection(_stop(1))],
    )

    assert area.members == ['node/1', 'node/8']


def test_a_relation_that_is_not_a_stop_area_is_not_one():
    not_an_area = _area(tags={'type': 'route', 'route': 'bus'}, members=(('node', 1, ''),))

    assert existing_stop_areas([not_an_area], [_collection(_stop(1))]) == []


def test_no_stops_means_no_areas():
    assert existing_stop_areas([_area()], []) == []
