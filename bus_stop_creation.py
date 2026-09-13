from collections import defaultdict
from collections.abc import Sequence
from itertools import pairwise
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from models.element_id import element_id, split_element_id
from models.relation_member import RelationMember
from tag_editing import normalize_tags, validate_tag
from utils import ensure_list


class NewStopPosition(BaseModel):
    """
    The point on the road where the bus halts, created as a node of the way itself.

    Belongs to a platform, which may be one being created by this changeset or one that
    has been in OSM all along, so it is sent on its own rather than under a new stop.
    """

    # the placeholder the route refers to the stop position by, as for a new platform
    id: int = Field(lt=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    # the way the node is inserted into, and the two of its nodes it goes between.
    # Sending the neighbours rather than an index means a way that has changed since the
    # client loaded it is noticed instead of being given a node in the wrong place.
    wayId: int = Field(gt=0)
    afterNode: int
    beforeNode: int
    # the name of the stop it serves, which it carries too; empty for an unnamed one
    name: str = ''
    # which way along the road the buses calling here travel, as the wiki gives it for a
    # stop position: relative to the way's own direction. Absent when it is not known.
    direction: Literal['forward', 'backward', 'both'] | None = None


class NewBusStop(BaseModel):
    """A bus stop the user placed on the map, created by the same changeset as the route."""

    # the placeholder the route refers to the stop by until OSM assigns a real id
    id: int = Field(lt=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    # only what the user entered; the tags that make the node a stop are added on top
    tags: dict[str, str]


def make_new_stop_tags(route_type: str | None, tags: dict[str, str]) -> dict[str, str]:
    # tram stops are tagged differently and belong on the track, so they are not offered
    if route_type not in {'bus', 'trolleybus'}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'New stops can only be added to bus and trolleybus routes')

    tags = normalize_tags(tags)

    # Overpass only returns named platforms, so an unnamed stop would vanish on reload
    if not tags.get('name'):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Every new bus stop needs a name')

    # applied last, so nothing the user typed can turn the node into something else
    tags.update({'highway': 'bus_stop', 'public_transport': 'platform', route_type: 'yes'})

    for key, value in tags.items():
        validate_tag(key, value)

    return tags


def make_stop_position_tags(route_type: str | None, name: str, direction: str | None = None) -> dict[str, str]:
    """The stop position carries the stop's name, and nothing else the platform owns."""
    if route_type not in {'bus', 'trolleybus'}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, 'Stop positions can only be added to bus and trolleybus routes'
        )

    tags = {'public_transport': 'stop_position', route_type: 'yes'}

    if name := name.strip():
        tags['name'] = name

    # tells the two sides of a road apart where both their stop positions sit on the one
    # way, which is the case the wiki asks for it in
    if direction is not None:
        tags['direction'] = direction

    for key, value in tags.items():
        validate_tag(key, value)

    return tags


def build_new_stop_nodes(
    new_stops: Sequence[NewBusStop],
    new_stop_positions: Sequence[NewStopPosition],
    route_type: str | None,
    members: Sequence[RelationMember],
) -> list[dict]:
    ids = [stop.id for stop in new_stops]
    ids += [position.id for position in new_stop_positions]
    if len(ids) != len(set(ids)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'New bus stops must have distinct ids')

    # a member pointing at a placeholder that nothing creates is rejected by OSM
    sent = {element_id(stop_id) for stop_id in ids}
    missing = sorted(
        {member.id for member in members if member.type == 'node' and split_element_id(member.id).id < 0} - sent
    )
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'The route refers to new bus stops that were not sent: {", ".join(missing)}',
        )

    nodes = [_node(stop.id, stop.lat, stop.lon, make_new_stop_tags(route_type, stop.tags)) for stop in new_stops]

    nodes += [
        _node(
            position.id,
            position.lat,
            position.lon,
            make_stop_position_tags(route_type, position.name, position.direction),
        )
        for position in new_stop_positions
    ]

    return nodes


def _node(node_id: int, lat: float, lon: float, tags: dict[str, str]) -> dict:
    return {
        '@id': node_id,
        # the OSM API stores coordinates to 7 decimal places
        '@lat': f'{lat:.7f}',
        '@lon': f'{lon:.7f}',
        'tag': [{'@k': k, '@v': v} for k, v in tags.items()],
    }


def insert_into_way_nodes(refs: list[int], insertions: Sequence[tuple[int, int, int]], way_id: int) -> list[int]:
    """
    Put each new node into a way's node list, between the pair of nodes it belongs to.

    `insertions` is (after_node, before_node, new_id). Positions are all resolved against
    the way as fetched, so several stops on one way do not shift each other's pair out
    from under them. A pair that is no longer next to each other means the way changed.
    """
    after_index: dict[int, list[int]] = defaultdict(list)

    for after_node, before_node, new_id in insertions:
        for i, (a, b) in enumerate(pairwise(refs)):
            if a == after_node and b == before_node:
                after_index[i].append(new_id)
                break
        else:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f'Conflict: way {way_id} no longer runs from node {after_node} to {before_node}. '
                'Go back and click the relation reload button.',
            )

    result: list[int] = []

    for i, ref in enumerate(refs):
        result.append(ref)
        result.extend(after_index.get(i, ()))

    return result


async def build_stop_position_way_elements(
    new_stop_positions: Sequence[NewStopPosition],
    split_ways: frozenset[int],
    osm,
) -> list[dict]:
    """The road ways to modify, each with its new stop position nodes inserted."""
    by_way: dict[int, list[tuple[int, int, int]]] = defaultdict(list)

    for position in new_stop_positions:
        # the split rewrite below builds its own node lists, which this would be lost in
        if position.wayId in split_ways:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f'A stop position cannot go on way {position.wayId}, which the route splits',
            )

        by_way[position.wayId].append((position.afterNode, position.beforeNode, position.id))

    if not by_way:
        return []

    result = []

    # fetched again rather than trusting what was loaded, so a way someone else changed
    # in the meantime is noticed
    for way in await osm.get_ways(tuple(map(str, by_way)), json=False):
        way_id = int(way['@id'])
        refs = [int(nd['@ref']) for nd in ensure_list(way.get('nd') or [])]

        way.pop('@timestamp', None)
        way.pop('@user', None)
        way.pop('@uid', None)
        way['nd'] = [{'@ref': ref} for ref in insert_into_way_nodes(refs, by_way[way_id], way_id)]
        result.append(way)

    return result
