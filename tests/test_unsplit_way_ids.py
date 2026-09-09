from models.element_id import element_id
from relation_builder import _unsplit_way_ids


def _ids(*values):
    return [element_id(v) if isinstance(v, int) else element_id(v[0], extra_num=v[1], max_num=v[2]) for v in values]


def test_whole_way_driven_once_is_rejoined():
    assert _unsplit_way_ids(_ids((5, 1, 2), (5, 2, 2))) == _ids(5)


def test_whole_way_driven_backwards_is_rejoined():
    assert _unsplit_way_ids(_ids((5, 2, 2), (5, 1, 2))) == _ids(5)


def test_way_driven_twice_stays_two_members():
    # in and back out of a road the bus turns around on
    assert _unsplit_way_ids(_ids((5, 1, 2), (5, 2, 2), (5, 2, 2), (5, 1, 2))) == _ids(5, 5)


def test_turning_around_mid_way_keeps_the_segments():
    # only the first half is driven, twice - rejoining would claim the second half
    # was driven too, and drop one of the two passes
    assert _unsplit_way_ids(_ids((5, 1, 2), (5, 1, 2))) == _ids((5, 1, 2), (5, 1, 2))


def test_segments_out_of_order_are_left_alone():
    assert _unsplit_way_ids(_ids((5, 1, 3), (5, 3, 3), (5, 2, 3))) == _ids((5, 1, 3), (5, 3, 3), (5, 2, 3))
