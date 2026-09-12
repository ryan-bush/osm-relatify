import asyncio

import pytest
import xmltodict
from fastapi import HTTPException

from bus_stop_creation import (
    NewBusStop,
    NewStopPosition,
    build_new_stop_nodes,
    insert_into_way_nodes,
    make_stop_position_tags,
)
from models.final_route import FinalRoute
from models.relation_member import RelationMember
from relation_builder import build_osm_change

BUS_TAGS = {'type': 'route', 'route': 'bus', 'public_transport:version': '2', 'name': 'Bus 12'}


class FakeOsm:
    """Stands in for the OSM API, returning the ways a stop position is inserted into."""

    def __init__(self, ways: dict[int, list[int]]):
        self._ways = ways
        self.requested: tuple[str, ...] | None = None

    async def get_ways(self, way_ids, json: bool = True):  # noqa: ARG002
        self.requested = tuple(way_ids)
        return [
            {
                '@id': str(way_id),
                '@version': '3',
                '@timestamp': '2026-01-01T00:00:00Z',
                '@user': 'someone',
                '@uid': '1',
                'nd': [{'@ref': str(ref)} for ref in self._ways[int(way_id)]],
                'tag': [{'@k': 'highway', '@v': 'residential'}],
            }
            for way_id in way_ids
        ]


def _position(id=-2, way_id=201, after=11, before=12, **kwargs):
    return NewStopPosition(id=id, lat=51.5001, lon=-0.1201, wayId=way_id, afterNode=after, beforeNode=before, **kwargs)


def _stop(id=-1, tags=None, position=_position):
    return NewBusStop(
        id=id,
        lat=51.5,
        lon=-0.12,
        tags=tags if tags is not None else {'name': 'High Street'},
        stopPosition=position() if callable(position) else position,
    )


def _route(members, tags=BUS_TAGS):
    return FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=tags,
        extraWaysToUpdate=(),
        members=tuple(members),
        warnings=(),
    )


def _change(members, new_stops, osm, tags=BUS_TAGS) -> dict:
    xml = asyncio.run(
        build_osm_change(
            None,
            _route(members, tags),
            include_changeset_id=False,
            overpass=None,
            osm=osm,
            tags_edited=tags,
            new_stops=new_stops,
        )
    )
    return xmltodict.parse(xml, force_list=('node', 'way', 'member', 'tag', 'nd'))['osmChange']


def _tags(element: dict) -> dict[str, str]:
    return {t['@k']: t['@v'] for t in element['tag']}


PLATFORM = RelationMember(id='-1', type='node', role='platform')
STOP = RelationMember(id='-2', type='node', role='stop')
WAY = RelationMember(id='201', type='way', role='')


class TestInsertIntoWayNodes:
    def test_inserts_between_the_pair(self):
        assert insert_into_way_nodes([10, 11, 12, 13], [(11, 12, -2)], 201) == [10, 11, -2, 12, 13]

    def test_inserts_at_the_start_and_the_end(self):
        assert insert_into_way_nodes([10, 11], [(10, 11, -2)], 201) == [10, -2, 11]

    def test_several_stops_on_one_way_do_not_shift_each_other(self):
        # both pairs are resolved against the way as fetched, not as already rewritten
        refs = [10, 11, 12, 13]
        assert insert_into_way_nodes(refs, [(10, 11, -2), (12, 13, -4)], 201) == [10, -2, 11, 12, -4, 13]

    def test_two_stops_in_one_segment_keep_their_order(self):
        assert insert_into_way_nodes([10, 11], [(10, 11, -2), (10, 11, -4)], 201) == [10, -2, -4, 11]

    def test_pair_no_longer_adjacent_is_a_conflict(self):
        # someone else inserted a node between them since the client loaded the way
        with pytest.raises(HTTPException) as e:
            insert_into_way_nodes([10, 11, 99, 12], [(11, 12, -2)], 201)

        assert e.value.status_code == 409
        assert 'way 201' in e.value.detail

    def test_pair_in_the_wrong_direction_is_a_conflict(self):
        with pytest.raises(HTTPException) as e:
            insert_into_way_nodes([10, 11, 12], [(12, 11, -2)], 201)

        assert e.value.status_code == 409

    def test_leaves_a_way_alone_when_nothing_is_inserted(self):
        assert insert_into_way_nodes([10, 11, 12], [], 201) == [10, 11, 12]


class TestStopPositionTags:
    def test_carries_the_name_and_the_route_type(self):
        tags = make_stop_position_tags('bus', {'name': 'High Street', 'local_ref': 'B'})
        assert tags == {'public_transport': 'stop_position', 'bus': 'yes', 'name': 'High Street'}

    def test_leaves_out_the_platform_only_tags(self):
        tags = make_stop_position_tags('bus', {'name': 'High Street', 'naptan:AtcoCode': '3900VA1', 'shelter': 'yes'})
        assert 'naptan:AtcoCode' not in tags
        assert 'shelter' not in tags

    def test_trolleybus_route(self):
        assert make_stop_position_tags('trolleybus', {'name': 'X'})['trolleybus'] == 'yes'


class TestBuildNewStopNodes:
    def test_creates_the_platform_and_the_stop_position(self):
        nodes = build_new_stop_nodes([_stop()], 'bus', [PLATFORM, STOP, WAY])
        assert [node['@id'] for node in nodes] == [-1, -2]
        assert _tags(nodes[0])['public_transport'] == 'platform'
        assert _tags(nodes[1])['public_transport'] == 'stop_position'

    def test_creates_only_the_platform_without_a_stop_position(self):
        nodes = build_new_stop_nodes([_stop(position=None)], 'bus', [PLATFORM, WAY])
        assert [node['@id'] for node in nodes] == [-1]

    def test_a_stop_position_id_clashing_with_a_platform_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            build_new_stop_nodes([_stop(id=-1, position=lambda: _position(id=-1))], 'bus', [PLATFORM, WAY])

        assert e.value.status_code == 400
        assert 'distinct ids' in e.value.detail

    def test_the_route_may_refer_to_the_stop_position(self):
        # it is sent, so it is not reported as a member nothing creates
        build_new_stop_nodes([_stop()], 'bus', [PLATFORM, STOP, WAY])


class TestBuildOsmChange:
    def test_modifies_the_way_and_creates_both_nodes(self):
        osm = FakeOsm({201: [10, 11, 12, 13]})
        change = _change([PLATFORM, STOP, WAY], [_stop()], osm)

        created = [node['@id'] for node in change['create']['node']]
        assert created == ['-1', '-2']

        way = change['modify']['way'][0]
        assert [nd['@ref'] for nd in way['nd']] == ['10', '11', '-2', '12', '13']

    def test_strips_the_metadata_from_the_fetched_way(self):
        osm = FakeOsm({201: [10, 11, 12]})
        way = _change([PLATFORM, STOP, WAY], [_stop()], osm)['modify']['way'][0]

        assert '@timestamp' not in way
        assert '@user' not in way
        assert '@uid' not in way
        # the way's own tags are left exactly as they were
        assert _tags(way) == {'highway': 'residential'}

    def test_the_stop_position_is_a_relation_member(self):
        osm = FakeOsm({201: [10, 11, 12]})
        relation = _change([PLATFORM, STOP, WAY], [_stop()], osm)['create']['relation']

        refs = [(m['@type'], m['@ref'], m['@role']) for m in relation['member']]
        assert ('node', '-2', 'stop') in refs

    def test_no_way_is_fetched_without_a_stop_position(self):
        osm = FakeOsm({})
        change = _change([PLATFORM, WAY], [_stop(position=None)], osm)

        assert osm.requested is None
        # nothing was modified at all, so the whole block is empty
        assert not change['modify']

    def test_two_stops_on_the_same_way_share_one_modify(self):
        osm = FakeOsm({201: [10, 11, 12, 13]})
        stops = [
            _stop(id=-1, position=lambda: _position(id=-2, after=10, before=11)),
            _stop(id=-3, tags={'name': 'Low Street'}, position=lambda: _position(id=-4, after=12, before=13)),
        ]
        members = [PLATFORM, STOP, RelationMember(id='-3', type='node', role='platform'),
                   RelationMember(id='-4', type='node', role='stop'), WAY]
        change = _change(members, stops, osm)

        ways = change['modify']['way']
        assert len(ways) == 1
        assert [nd['@ref'] for nd in ways[0]['nd']] == ['10', '-2', '11', '12', '-4', '13']
