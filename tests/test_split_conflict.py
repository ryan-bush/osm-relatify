"""The conflict check build_osm_change() makes on a way the route splits."""

import asyncio

import pytest
import xmltodict
from fastapi import HTTPException

from models.fetch_relation import FetchRelationElement
from models.final_route import FinalRoute, FinalRouteWay
from models.relation_member import RelationMember
from overpass import QueryParentsResult
from relation_builder import build_osm_change

RELATION_ID = 7
WAY_ID = 52919101

# A drive that leaves node 1, reaches node 4, loops round and comes back to node 4: the
# way passes through one of its own nodes twice, and is split there.
P_SHAPED_NODES = [1, 2, 3, 4, 5, 6, 4]


def way_xml(nodes: list[int]) -> str:
    nds = ''.join(f'<nd ref="{node}"/>' for node in nodes)
    return f'<osm><way id="{WAY_ID}" version="6" changeset="1">{nds}<tag k="highway" v="service"/></way></osm>'


RELATION_XML = f"""<osm><relation id="{RELATION_ID}" version="3" changeset="99">
  <member type="way" ref="{WAY_ID}" role=""/>
  <tag k="type" v="route"/>
  <tag k="route" v="bus"/>
</relation></osm>"""


class FakeOpenStreetMap:
    def __init__(self, way_nodes: list[int]):
        self._way_nodes = way_nodes

    async def get_relation(self, relation_id, *, json: bool = True) -> dict:
        return xmltodict.parse(RELATION_XML)['osm']['relation']

    async def get_ways(self, way_ids, *, json: bool = True) -> list[dict]:
        return [xmltodict.parse(way_xml(self._way_nodes), force_list=('nd',))['osm']['way']]


class FakeOverpass:
    async def query_parents(self, way_ids) -> QueryParentsResult:
        return QueryParentsResult(id_relations_map={WAY_ID: []}, ways_map={})


def piece(extra_num: int, nodes: list[int]) -> FetchRelationElement:
    return FetchRelationElement(
        id=f'{WAY_ID}_{extra_num}_2',
        member=extra_num == 1,
        oneway=False,
        roundabout=False,
        nodes=nodes,
        latLngs=[(51.0 + node / 1000, 0.0) for node in nodes],
        connectedTo=[],
        turn_in_place_start=False,
        turn_in_place_end=False,
    )


def build(server_nodes: list[int]) -> dict:
    # split at node 4, where the way meets itself
    first, second = piece(1, [1, 2, 3, 4]), piece(2, [4, 5, 6, 4])
    route = FinalRoute(
        ways=(FinalRouteWay(way=first, reversed_latLngs=False),),
        latLngs=(),
        busStops=(),
        tags={'type': 'route', 'route': 'bus'},
        extraWaysToUpdate=(second,),
        members=(RelationMember(id=first.id, type='way', role=''),),
        warnings=(),
    )

    osm_change = asyncio.run(
        build_osm_change(
            RELATION_ID,
            route,
            include_changeset_id=False,
            overpass=FakeOverpass(),
            osm=FakeOpenStreetMap(server_nodes),
        )
    )
    return xmltodict.parse(osm_change.xml, force_list=('nd',))['osmChange']


def test_a_way_through_its_own_node_is_split_without_a_conflict():
    change = build(P_SHAPED_NODES)

    modified = change['modify']['way']
    assert modified['@id'] == str(WAY_ID)
    assert [int(nd['@ref']) for nd in modified['nd']] == [1, 2, 3, 4]

    created = change['create']['way']
    assert [int(nd['@ref']) for nd in created['nd']] == [4, 5, 6, 4]


@pytest.mark.parametrize(
    'server_nodes',
    [
        pytest.param([1, 2, 3, 4], id='already split'),
        pytest.param([1, 2, 3, 4, 5, 7, 6, 4], id='node added'),
        pytest.param([1, 2, 4, 5, 6, 4], id='node removed'),
    ],
)
def test_a_way_changed_since_loading_is_a_conflict(server_nodes):
    with pytest.raises(HTTPException) as exc_info:
        build(server_nodes)

    assert exc_info.value.status_code == 409
    assert f'Way {WAY_ID} was modified' in exc_info.value.detail
