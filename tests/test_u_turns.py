"""
Turning the bus around where nothing in OSM says it can be done.

A route that goes out and back along the same way was reloaded as a route that could not
get past the turn, and every way beyond it came back reported as unused. Two real ones
showed the two shapes of it: bus 24 in Holyhead turns at a mini roundabout, which is a
turning circle by another name and can be read off the map; bus C6 in Swindon reverses
into Church Farm Lane, which leaves no tag behind anywhere. What the second one leaves is
the relation, where the way it turned on is listed twice in a row.
"""

from models.bounding_box import BoundingBox
from overpass import build_query
from u_turns import relation_u_turn_nodes

# way 221500763 in Holyhead, off the way before it and back onto the way after, with the
# mini roundabout it turns at as its far end
HOLYHEAD = {
    154804916: [1672779772, 2305277610],
    221500763: [2305277610, 1672779763],
    221500768: [2305277610, 2305277601],
}
HOLYHEAD_MEMBERS = [154804916, 221500763, 221500763, 221500768]

# way 1470398313 in Swindon, in and back out of the one way beside it
SWINDON = {
    1470398314: [246192437, 7018409890],
    1470398313: [246192437, 246192439],
}
SWINDON_MEMBERS = [1470398314, 1470398313, 1470398313, 1470398314]


def test_a_way_listed_twice_in_a_row_turns_at_its_far_end():
    assert relation_u_turn_nodes(HOLYHEAD_MEMBERS, HOLYHEAD) == {1672779763}


def test_the_end_it_came_in_by_is_not_a_turn():
    # the bus is already pointing the right way there; a turn offered would be a second route
    assert 2305277610 not in relation_u_turn_nodes(HOLYHEAD_MEMBERS, HOLYHEAD)


def test_a_bus_reversing_into_a_side_road_turns_at_the_far_end_too():
    assert relation_u_turn_nodes(SWINDON_MEMBERS, SWINDON) == {246192439}


def test_a_route_that_never_doubles_back_turns_nowhere():
    assert relation_u_turn_nodes([154804916, 221500763, 221500768], HOLYHEAD) == set()


def test_the_same_way_driven_again_later_is_not_a_turn():
    """A loop comes back over its own ways; only one listed twice running is a turn."""
    members = [154804916, 221500763, 221500768, 221500763]

    assert relation_u_turn_nodes(members, HOLYHEAD) == set()


def test_an_end_that_cannot_be_told_from_the_other_offers_both():
    # nothing beside it to say which end it came in by, so either may be the one
    assert relation_u_turn_nodes([221500763, 221500763], HOLYHEAD) == {2305277610, 1672779763}


def test_a_way_outside_the_downloaded_area_says_nothing():
    assert relation_u_turn_nodes([999, 999], HOLYHEAD) == set()


def test_a_relation_with_no_members_says_nothing():
    assert relation_u_turn_nodes([], HOLYHEAD) == set()


def test_a_mini_roundabout_is_downloaded_as_a_place_to_turn():
    bb = BoundingBox(minlat=53.3, minlon=-4.7, maxlat=53.4, maxlon=-4.6)
    query = build_query([bb], [bb], 60, 'bus')

    assert 'node[highway=turning_circle]' in query
    assert 'node[highway=mini_roundabout]' in query
