import pytest

from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection, PublicTransport
from models.final_route import FinalRoute, WarningSeverity
from route_warnings import _check_for_bus_stop_inactive_in_naptan, _check_for_bus_stop_serving_other_direction

# an east-west road; the route heads east along it
WEST, MIDDLE, EAST = (57.0, -2.010), (57.0, -2.005), (57.0, -2.000)
# about 11 m north of the road, the near kerb for an eastbound bus in the UK
BESIDE_ROAD = (57.0001, -2.005)
# the same distance south of it, the kerb an eastbound bus passes on its right
ACROSS_ROAD = (56.9999, -2.005)
# a stop mapped out on the carriageway, which stands at neither kerb
ON_ROAD = (57.0, -2.005)


def _stop(tags, lat_lng=ACROSS_ROAD, id='1', public_transport=PublicTransport.PLATFORM):
    return FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=True,
        latLng=lat_lng,
        tags=tags,
        name='',
        groupName='',
        highway='bus_stop',
        public_transport=public_transport,
    )


def _route(collections, lat_lngs=(WEST, MIDDLE, EAST)):
    return FinalRoute(ways=(), latLngs=tuple(lat_lngs), busStops=tuple(collections), tags={})


def _platform(tags, **kwargs):
    return FetchRelationBusStopCollection(platform=_stop(tags, **kwargs), stop=None)


@pytest.mark.parametrize('bearing', ['E', 'NE', 'SE', 'N', 'S'])
def test_a_stop_facing_roughly_the_route_direction_is_fine(bearing):
    assert _check_for_bus_stop_serving_other_direction(_route([_platform({'naptan:Bearing': bearing})])) is None


@pytest.mark.parametrize('bearing', ['W', 'NW', 'SW', 'w '])
def test_a_stop_facing_the_opposite_way_across_the_road_is_flagged(bearing):
    warning = _check_for_bus_stop_serving_other_direction(_route([_platform({'naptan:Bearing': bearing})]))

    assert warning.severity == WarningSeverity.LOW
    assert warning.extra == ('1',)


def test_a_stop_at_the_near_kerb_is_kept_even_when_the_bearing_disagrees():
    # the bearing of one stop of a pair gets copied onto the other, as at Otter Way on
    # Stoneover Lane, where the NE-bound platform is tagged SW-bound like its partner
    near_kerb = _platform({'naptan:Bearing': 'W'}, lat_lng=BESIDE_ROAD)

    assert _check_for_bus_stop_serving_other_direction(_route([near_kerb])) is None


def test_a_stop_out_on_the_carriageway_is_left_to_its_bearing():
    on_road = _platform({'naptan:Bearing': 'W'}, lat_lng=ON_ROAD)

    assert _check_for_bus_stop_serving_other_direction(_route([on_road])).extra == ('1',)


def test_a_route_passing_both_ways_serves_either_side():
    out_and_back = (WEST, MIDDLE, EAST, MIDDLE, WEST)

    assert (
        _check_for_bus_stop_serving_other_direction(_route([_platform({'naptan:Bearing': 'W'})], out_and_back)) is None
    )


def test_the_bearing_is_read_from_the_stop_position_too():
    collection = FetchRelationBusStopCollection(
        platform=_stop({'name': 'No bearing here'}),
        stop=_stop({'naptan:Bearing': 'W'}, id='2', public_transport=PublicTransport.STOP_POSITION),
    )

    assert _check_for_bus_stop_serving_other_direction(_route([collection])).extra == ('1',)


@pytest.mark.parametrize('tags', [{}, {'naptan:Bearing': ''}, {'naptan:Bearing': '270'}])
def test_a_stop_without_a_compass_bearing_is_not_judged(tags):
    assert _check_for_bus_stop_serving_other_direction(_route([_platform(tags)])) is None


def test_a_stop_far_from_the_route_is_left_to_the_far_away_warning():
    far = _platform({'naptan:Bearing': 'W'}, lat_lng=(57.01, -2.005))

    assert _check_for_bus_stop_serving_other_direction(_route([far])) is None


def test_inactive_naptan_stops_are_flagged():
    route = _route(
        [
            _platform({'naptan:AtcoCode': 'LIVE'}, id='1'),
            _platform({'naptan:AtcoCode': 'OLD'}, id='2'),
            _platform({'naptan:AtcoCode': 'LIVE2;OLD2'}, id='3'),
            _platform({}, id='4'),
        ]
    )

    warning = _check_for_bus_stop_inactive_in_naptan(route, frozenset({'OLD', 'OLD2'}))

    assert warning.severity == WarningSeverity.LOW
    assert warning.extra == ('2', '3')


def test_no_inactive_warning_without_inactive_codes():
    assert _check_for_bus_stop_inactive_in_naptan(_route([_platform({'naptan:AtcoCode': 'OLD'})]), frozenset()) is None


# Otter Way on Stoneover Lane, Royal Wootton Bassett, where the NE-bound platform
# (node 3202423034) carries its partner's SW bearing. Bus 55 towards Chippenham runs
# north-east past it, and the platform stands at the near kerb.
_STONEOVER_LANE_NE = ((51.5430627, -1.8917), (51.5440143, -1.8907738), (51.5450, -1.8898))


def test_the_real_otter_way_platform_is_not_flagged():
    otter_way = _platform({'naptan:Bearing': 'SW'}, lat_lng=(51.5441087, -1.8907539))

    assert _check_for_bus_stop_serving_other_direction(_route([otter_way], _STONEOVER_LANE_NE)) is None


def test_its_partner_across_the_road_is_flagged():
    partner = _platform({'naptan:Bearing': 'SW'}, lat_lng=(51.5439904, -1.8907182))

    assert _check_for_bus_stop_serving_other_direction(_route([partner], _STONEOVER_LANE_NE)).extra == ('1',)
