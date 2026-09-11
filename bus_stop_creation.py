from collections.abc import Sequence

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from models.element_id import element_id, split_element_id
from models.relation_member import RelationMember
from tag_editing import normalize_tags, validate_tag


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


def build_new_stop_nodes(
    new_stops: Sequence[NewBusStop],
    route_type: str | None,
    members: Sequence[RelationMember],
) -> list[dict]:
    ids = [stop.id for stop in new_stops]
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

    return [
        {
            '@id': stop.id,
            # the OSM API stores coordinates to 7 decimal places
            '@lat': f'{stop.lat:.7f}',
            '@lon': f'{stop.lon:.7f}',
            'tag': [{'@k': k, '@v': v} for k, v in make_new_stop_tags(route_type, stop.tags).items()],
        }
        for stop in new_stops
    ]
