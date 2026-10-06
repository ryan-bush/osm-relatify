from overpass import is_routable


def test_bus_can_use_parking_aisle():
    assert is_routable({'highway': 'service', 'service': 'parking_aisle'}, 'bus')


def test_bus_still_avoids_driveway():
    assert not is_routable({'highway': 'service', 'service': 'driveway'}, 'bus')


def test_bus_can_use_private_road():
    assert is_routable({'highway': 'service', 'access': 'private'}, 'bus')
    assert is_routable({'highway': 'unclassified', 'motor_vehicle': 'private'}, 'bus')


def test_bus_still_avoids_access_no():
    assert not is_routable({'highway': 'residential', 'access': 'no'}, 'bus')
