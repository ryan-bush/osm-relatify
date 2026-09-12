"""Which stops a stop_area relation would bring together."""

from bus_collection_builder import assign_stop_area_groups
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection

# a degree of longitude is about 69 km at this latitude, so 0.001 is roughly 69 m
LAT = 51.5


def _stop(name, id=1, lon=0.0, local_ref=None, lat=LAT):
    tags = {'name': name, 'public_transport': 'platform', 'highway': 'bus_stop'}
    if local_ref:
        tags['local_ref'] = local_ref
    return FetchRelationBusStop.from_data({'id': id, 'type': 'node', 'lat': lat, 'lon': lon, 'tags': tags})


def _collection(name, id=1, lon=0.0, local_ref=None, lat=LAT):
    return FetchRelationBusStopCollection(platform=_stop(name, id, lon, local_ref, lat), stop=None)


def _groups(collections, **kwargs):
    return [c.groupId for c in assign_stop_area_groups(collections, **kwargs)]


def test_two_sides_of_a_road_are_one_group():
    groups = _groups([_collection('The Orchards', 1), _collection('The Orchards', 2, lon=0.0003)])

    assert groups[0] == groups[1] != -1


def test_stop_letters_do_not_keep_a_pair_apart():
    """The display name has the letter appended, so grouping by it would never pair them."""
    groups = _groups(
        [
            _collection('The Orchards', 1, local_ref='A'),
            _collection('The Orchards', 2, lon=0.0003, local_ref='B'),
        ]
    )

    assert groups[0] == groups[1] != -1


def test_stops_further_apart_than_the_collection_radius_still_group():
    # about 140 m, well beyond BUS_COLLECTION_SEARCH_AREA
    groups = _groups([_collection('The Orchards', 1), _collection('The Orchards', 2, lon=0.002)])

    assert groups[0] == groups[1] != -1


def test_stops_beyond_the_search_area_are_left_apart():
    # about 350 m
    groups = _groups([_collection('The Orchards', 1), _collection('The Orchards', 2, lon=0.005)])

    assert groups[0] != groups[1]


def test_the_search_area_is_adjustable():
    far = [_collection('The Orchards', 1), _collection('The Orchards', 2, lon=0.005)]

    assert _groups(far, search_area=500)[0] == _groups(far, search_area=500)[1]


def test_different_names_never_group():
    groups = _groups([_collection('The Orchards', 1), _collection('Mill Close', 2, lon=0.0003)])

    assert groups[0] != groups[1]


def test_the_name_is_compared_loosely_enough_for_punctuation_and_case():
    groups = _groups([_collection("St. Mary's Church", 1), _collection('St Marys Church', 2, lon=0.0003)])

    assert groups[0] == groups[1] != -1


def test_an_unnamed_stop_belongs_to_nothing():
    groups = _groups([_collection('', 1), _collection('The Orchards', 2, lon=0.0003)])

    assert groups[0] == -1


def test_a_lone_stop_still_gets_a_group():
    # its platform and stop position are a stop area in their own right
    assert _groups([_collection('The Orchards', 1)]) == [0]


def test_three_stops_at_one_place_are_one_group():
    groups = _groups(
        [
            _collection('Bus Station', 1, local_ref='1'),
            _collection('Bus Station', 2, lon=0.0006, local_ref='2'),
            _collection('Bus Station', 3, lon=0.0012, local_ref='3'),
        ]
    )

    assert groups[0] == groups[1] == groups[2] != -1


def test_two_places_sharing_a_name_stay_separate():
    # the same name at opposite ends of town, about 700 m apart
    groups = _groups([_collection('High Street', 1), _collection('High Street', 2, lon=0.01)])

    assert groups[0] != groups[1]


def test_nothing_to_group():
    assert assign_stop_area_groups([]) == []


def test_the_collections_themselves_are_untouched():
    original = [_collection('The Orchards', 1)]
    result = assign_stop_area_groups(original)

    assert result[0].platform is original[0].platform
    assert original[0].groupId == -1, 'the originals are left alone'
