from collections.abc import Container, Iterable, Sequence
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from models.stop_area import StopArea
from tag_editing import validate_tag
from utils import ensure_list

# a stop area groups the stops of one place; anything else is not ours to touch
STOP_AREA_TAGS = {'type': 'public_transport', 'public_transport': 'stop_area'}


def build_stop_areas_query(node_ids: Iterable[int], way_ids: Iterable[int], timeout: int) -> str:
    """Overpass query for the stop_area relations the given stops already belong to."""
    node_ids = tuple(sorted(set(node_ids)))
    way_ids = tuple(sorted(set(way_ids)))

    filters = '["type"="public_transport"]["public_transport"="stop_area"]'
    parts = []

    # an empty id list is a syntax error, so each kind is only asked for when there is one
    if node_ids:
        parts.append(f'node(id:{",".join(map(str, node_ids))})->.n;rel(bn.n){filters};')
    if way_ids:
        parts.append(f'way(id:{",".join(map(str, way_ids))})->.w;rel(bw.w){filters};')

    if not parts:
        return ''

    return f'[out:json][timeout:{timeout}];(' + ''.join(parts) + ');out meta;'


def parse_stop_areas(elements: Iterable[dict]) -> list[StopArea]:
    """The stop_area relations an Overpass reply describes."""
    result = []

    for element in elements:
        if element.get('type') != 'relation':
            continue

        tags = element.get('tags') or {}
        # Overpass filtered on these, but a reply is data rather than a promise
        if any(tags.get(key) != value for key, value in STOP_AREA_TAGS.items()):
            continue

        result.append(
            StopArea(
                id=element['id'],
                name=tags.get('name', '').strip(),
                members=[f'{member["type"]}/{member["ref"]}' for member in element.get('members') or ()],
            )
        )

    return result


class StopAreaMember(BaseModel):
    """A stop that belongs in a stop area, by the role the wiki gives it."""

    type: Literal['node', 'way']
    # negative for a platform or stop position this same changeset is creating
    id: int
    role: Literal['platform', 'stop']

    @property
    def key(self) -> str:
        return f'{self.type}/{self.id}'


class StopAreaChange(BaseModel):
    """A stop area to create, or members to add to one that is already in OSM."""

    # absent for a new one, which this changeset creates
    id: int | None = Field(default=None, gt=0)
    # only used when creating; an existing relation keeps the name it has
    name: str = ''
    members: list[StopAreaMember] = Field(min_length=1)


# the route relation being created takes -1, so stop areas start below it
FIRST_STOP_AREA_PLACEHOLDER_ID = -2


def _check_members(changes: Sequence[StopAreaChange], created_node_ids: Container[int]) -> None:
    for change in changes:
        seen: set[str] = set()

        for member in change.members:
            if member.key in seen:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f'{member.key} was sent twice for one stop area')
            seen.add(member.key)

            # a member pointing at a placeholder that nothing creates is rejected by OSM
            if member.id < 0 and member.id not in created_node_ids:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f'A stop area refers to {member.key}, which is not being created',
                )

        if change.id is None and not change.name.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, 'A new stop area needs a name')


def build_new_stop_area_relations(
    changes: Sequence[StopAreaChange],
    created_node_ids: Container[int],
) -> list[dict]:
    """The stop_area relations to create, each with its own placeholder id."""
    _check_members(changes, created_node_ids)

    result = []
    next_id = FIRST_STOP_AREA_PLACEHOLDER_ID

    for change in changes:
        if change.id is not None:
            continue

        name = change.name.strip()
        tags = {**STOP_AREA_TAGS, 'name': name}

        for key, value in tags.items():
            validate_tag(key, value)

        result.append(
            {
                '@id': next_id,
                'tag': [{'@k': k, '@v': v} for k, v in tags.items()],
                'member': [
                    {'@type': m.type, '@ref': m.id, '@role': m.role} for m in change.members
                ],
            }
        )
        next_id -= 1

    return result


async def build_stop_area_modifications(
    changes: Sequence[StopAreaChange],
    created_node_ids: Container[int],
    osm,
) -> list[dict]:
    """The stop_area relations already in OSM, with the members they were missing added."""
    _check_members(changes, created_node_ids)

    by_id = {change.id: change for change in changes if change.id is not None}
    if not by_id:
        return []

    result = []

    # fetched again rather than trusting what was loaded, so members added in the
    # meantime are not added a second time
    for relation in await osm.get_relations(tuple(map(str, by_id)), json=False):
        relation_id = int(relation['@id'])
        tags = {tag['@k']: tag['@v'] for tag in ensure_list(relation.get('tag') or [])}

        # it has stopped being a stop area since, so it is not ours to add stops to
        if any(tags.get(key) != value for key, value in STOP_AREA_TAGS.items()):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f'Conflict: relation {relation_id} is no longer a stop area. '
                'Go back and click the relation reload button.',
            )

        members = ensure_list(relation.get('member') or [])
        present = {f'{m["@type"]}/{m["@ref"]}' for m in members}
        added = [m for m in by_id[relation_id].members if m.key not in present]

        if not added:
            continue

        relation.pop('@timestamp', None)
        relation.pop('@user', None)
        relation.pop('@uid', None)
        relation['member'] = [
            *members,
            *({'@type': m.type, '@ref': m.id, '@role': m.role} for m in added),
        ]
        result.append(relation)

    return result
