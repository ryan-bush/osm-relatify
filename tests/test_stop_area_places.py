"""What a stop takes from the stop area it is in."""

from bus_collection_builder import build_bus_stop_collections
from models.fetch_relation import FetchRelationBusStop, PublicTransport
from overpass import stop_area_places, stop_elements


def _node(id, tags, lon=0.0):
    return {'type': 'node', 'id': id, 'lat': 53.2, 'lon': lon, 'tags': tags}


def _area(id, members, **tags):
    return {
        'type': 'relation',
        'id': id,
        'tags': {'type': 'public_transport', 'public_transport': 'stop_area', 'bus': 'yes', **tags},
        'members': [{'type': 'node', 'ref': ref, 'role': role} for ref, role in members],
    }


def _stops(relations, platforms, stop_positions):
    places = stop_area_places(relations, platforms, stop_positions)
    return [
        FetchRelationBusStop.from_data(e, places.get((e['type'], e['id']))) for e in (*platforms, *stop_positions)
    ]


def test_the_area_name_does_not_become_the_stop_name_tag():
    """Ffordd y Parc in Bangor: unnamed, in an area called Ffordd Penlan."""
    [platform] = _stops(
        [_area(1, [(10, 'platform')], name='Ffordd Penlan')],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform'})],
        [],
    )

    assert 'name' not in platform.tags
    assert platform.placeName == 'Ffordd Penlan'
    assert platform.name == 'Ffordd Penlan'


def test_the_area_tags_do_not_become_the_stop_tags():
    [platform] = _stops(
        [_area(1, [(10, 'platform')], name='X', ref='1234')],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform'})],
        [],
    )

    assert platform.tags == {'highway': 'bus_stop', 'public_transport': 'platform'}


def test_a_stop_keeps_its_own_name():
    [platform] = _stops(
        [_area(1, [(10, 'platform')], name='Ffordd Penlan')],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform', 'name': 'Ffordd y Parc'})],
        [],
    )

    assert platform.placeName == 'Ffordd y Parc'


def test_an_unnamed_area_is_named_by_its_platform():
    """Coleg Normal in Bangor: neither the area nor its stop positions have a name."""
    _, stop = _stops(
        [_area(1, [(20, 'stop'), (10, 'platform')])],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform', 'name': 'Coleg Normal'})],
        [_node(20, {'public_transport': 'stop_position', 'bus': 'yes'}, lon=0.00005)],
    )

    assert stop.placeName == 'Coleg Normal'


def test_the_older_stop_position_role_counts_as_a_stop():
    """Ffordd-y-Llyn in Bangor: an untagged-by-role stop position in an older area."""
    _, stop = _stops(
        [_area(1, [(20, 'stop_position'), (10, 'platform')])],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform', 'name': 'Ffordd-y-Llyn'})],
        [_node(20, {'bus': 'yes'}, lon=0.00005)],
    )

    assert stop.public_transport == PublicTransport.STOP_POSITION
    assert stop.placeName == 'Ffordd-y-Llyn'


def test_an_unnamed_stop_position_pairs_with_its_platform():
    stops = _stops(
        [_area(1, [(20, 'stop'), (10, 'platform')])],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform', 'name': 'Coleg Normal'})],
        [_node(20, {'public_transport': 'stop_position', 'bus': 'yes'}, lon=0.00005)],
    )

    [collection] = build_bus_stop_collections(stops)

    assert (collection.platform.id, collection.stop.id) == ('10', '20')


def test_a_stop_in_two_areas_takes_the_first():
    [platform] = _stops(
        [_area(2, [(10, 'platform')], name='Second'), _area(1, [(10, 'platform')], name='First')],
        [_node(10, {'highway': 'bus_stop', 'public_transport': 'platform'})],
        [],
    )

    assert platform.placeName == 'First'


def test_a_bare_node_in_a_stop_area_is_read_by_its_role():
    """An X43 stop area held a stop position with no tags at all, which failed the load."""
    platform = _node(10, {'highway': 'bus_stop', 'public_transport': 'platform', 'name': 'Coleg Normal'})
    bare = {'type': 'node', 'id': 20, 'lat': 53.2, 'lon': 0.00005}
    relations = [_area(1, [(20, 'stop_position'), (10, 'platform')])]
    places = stop_area_places(relations, [platform], [bare])

    elements = stop_elements([platform, bare], places)
    stop = FetchRelationBusStop.from_data(elements[1], places[('node', 20)])

    assert stop.tags == {}
    assert stop.public_transport == PublicTransport.STOP_POSITION
    assert stop.placeName == 'Coleg Normal'


def test_an_untagged_node_in_no_stop_area_is_left_out():
    bare = {'type': 'node', 'id': 20, 'lat': 53.2, 'lon': 0.0}

    assert stop_elements([bare], {}) == ()


def test_a_bus_stop_without_public_transport_is_a_platform():
    """A highway=bus_stop mapped before PTv2 was left off the map, so it could not be tagged."""
    stop = _node(10, {'highway': 'bus_stop', 'name': 'Deiniol Road'})

    [element] = stop_elements([stop], {})
    platform = FetchRelationBusStop.from_data(element)

    assert platform.public_transport == PublicTransport.PLATFORM
    assert platform.tags == {'highway': 'bus_stop', 'name': 'Deiniol Road'}
    assert build_bus_stop_collections([platform])[0].platform == platform
