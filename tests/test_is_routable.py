from overpass import is_routable


def test_bus_can_use_parking_aisle():
    assert is_routable({'highway': 'service', 'service': 'parking_aisle'}, 'bus')


def test_bus_still_avoids_driveway():
    assert not is_routable({'highway': 'service', 'service': 'driveway'}, 'bus')
