from collections.abc import Iterable, Sequence

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from models.bounding_box import BoundingBox
from models.route_master import RouteMaster, RouteMasterRoute, RouteMasterView
from placeholder_ids import RelationPlaceholders
from route_types import get_route_type
from tag_editing import apply_tag_changes, normalize_tags, validate_tag
from utils import ensure_list, overpass_settings

# a route master collects every variant of one line; anything else is not one
ROUTE_MASTER_TYPE = 'route_master'

# how many member routes are worth naming; a master this large is a tagging accident, and
# fetching all of them would turn one batched request into an unbounded one
MAX_DESCRIBED_ROUTES = 200


def is_route_master(tags: dict[str, str]) -> bool:
    return tags.get('type') == ROUTE_MASTER_TYPE


def escape_overpass_value(value: str) -> str:
    """A tag value as an Overpass string literal, so a quote in a ref cannot end it."""
    return value.replace('\\', '\\\\').replace('"', '\\"')


def build_route_master_candidates_statements(ref: str, route_value: str, bounds: BoundingBox) -> str:
    """
    Overpass statements for the route masters that routes sharing this ref already belong to.

    Route masters have no geometry of their own, so they are reached through their
    members: the routes with the same ref in the downloaded area, and from those the
    relations holding them. Empty when there is no ref to go on, which is nothing to ask.
    """
    ref = ref.strip()
    if not ref or not route_value:
        return ''

    filters = f'["type"="route"]["route"="{escape_overpass_value(route_value)}"]["ref"="{escape_overpass_value(ref)}"]'

    return (
        f'rel{filters}({bounds})->.rm;'
        f'rel(br.rm)["type"="{ROUTE_MASTER_TYPE}"];'
        f'out meta;'
    )


def build_route_master_candidates_query(
    ref: str,
    route_value: str,
    bounds: BoundingBox,
    timeout: int,
) -> str:
    """The candidates asked for on their own, for a ref that has just been typed."""
    statements = build_route_master_candidates_statements(ref, route_value, bounds)
    return (overpass_settings(timeout, 128) + statements) if statements else ''


def parse_route_masters(elements: Iterable[dict]) -> list[RouteMaster]:
    """
    The route master relations an Overpass or OSM API reply describes.

    Both speak the same JSON shape here, so parent relations from the API and candidates
    from Overpass are read the same way.
    """
    result = []

    for element in elements:
        if element.get('type') != 'relation':
            continue

        tags = element.get('tags') or {}
        # the query filtered on this, but a reply is data rather than a promise
        if not is_route_master(tags):
            continue

        result.append(
            RouteMaster(
                id=element['id'],
                tags=tags,
                members=[f'{member["type"]}/{member["ref"]}' for member in element.get('members') or ()],
            )
        )

    return result


def member_route_ids(masters: Iterable[RouteMaster]) -> list[int]:
    """The relation members of these masters, de-duplicated and in the order first seen."""
    result: dict[int, None] = {}

    for master in masters:
        for member in master.members:
            type, _, id = member.partition('/')
            if type == 'relation':
                result.setdefault(int(id), None)

    return list(result)


def parse_routes(elements: Iterable[dict]) -> dict[int, RouteMasterRoute]:
    """The member routes, by id, as the client needs them to list a master's variants."""
    result = {}

    for element in elements:
        if element.get('type') != 'relation':
            continue

        tags = element.get('tags') or {}
        result[element['id']] = RouteMasterRoute(
            id=element['id'],
            ref=tags.get('ref', '').strip(),
            name=tags.get('name', '').strip(),
            tags=tags,
            editable=get_route_type(tags) is not None,
            described=True,
        )

    return result


def build_route_master_view(master: RouteMaster, routes: dict[int, RouteMasterRoute]) -> RouteMasterView:
    """
    A master and its variants, in the order the master holds them.

    A member that could not be looked up, or that is not a relation at all, is still
    counted rather than quietly dropped: a master holding something odd is worth seeing.
    """
    listed = []
    others = []

    for member in master.members:
        type, _, id = member.partition('/')

        if type != 'relation':
            others.append(member)
            continue

        route = routes.get(int(id))
        # in the master, but OSM did not describe it; named by its id so it is not lost,
        # and marked as undescribed so it is not read as a route that cannot be opened
        listed.append(route if route is not None else RouteMasterRoute(id=int(id), ref='', name=''))

    return RouteMasterView(id=master.id, tags=master.tags, routes=listed, otherMembers=others)


def describe_members(
    masters: Sequence[RouteMaster],
    routes: dict[int, RouteMasterRoute],
) -> list[RouteMaster]:
    """Each master with its member routes filled in, for those that were looked up."""
    result = []

    for master in masters:
        described = []

        for member in master.members:
            type, _, id = member.partition('/')
            if type != 'relation':
                continue
            route = routes.get(int(id))
            if route is not None:
                described.append(route)

        result.append(
            RouteMaster(id=master.id, tags=master.tags, members=master.members, routes=described)
        )

    return result


# what makes a relation a route master, and what the application itself reads it back by
PROTECTED_ROUTE_MASTER_KEYS = frozenset({'type', 'route_master'})


class RouteMasterChange(BaseModel):
    """The route master to put the route in: one already in OSM, or one to create."""

    # absent for a new master, which this changeset creates
    id: int | None = Field(default=None, gt=0)
    # the tags to give a new master, or the edited tags of one already in OSM
    tags: dict[str, str] = Field(default_factory=dict)
    # An existing master's tags exactly as the client loaded them, the baseline its edits
    # are diffed against. Absent means its tags are left alone.
    tagsOriginal: dict[str, str] | None = Field(default=None)  # noqa: N815


def _check_tags(tags: dict[str, str]) -> dict[str, str]:
    for key, value in tags.items():
        validate_tag(key, value)
    return tags


def route_master_tags_for(route_tags: dict[str, str]) -> dict[str, str]:
    """The tags a master of this route must carry, whatever else the mapper gives it."""
    route_value = route_tags.get(route_tags.get('type', ''), '')
    return {'type': ROUTE_MASTER_TYPE, 'route_master': route_value}


async def check_new_route_master(
    change: RouteMasterChange | None,
    relation_id: int | None,
    osm,
) -> None:
    """
    Refuse to create a route master for a route that is already in one.

    Which masters exist is answered at download time, and by then it is already a moment
    in the past: a master created since leaves this route looking unlinked, and it would
    be given a second one of its own. The question is put once more here, to OSM, which
    is never behind.
    """
    if change is None or change.id is not None or relation_id is None:
        return

    for relation in await osm.get_parent_relations('relation', relation_id):
        tags = relation.get('tags') or {}
        if not is_route_master(tags):
            continue

        name = tags.get('name', '').strip()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Conflict: this route is already in route master {relation["id"]}'
            f'{f" ({name})" if name else ""}, so a new one would be a duplicate. '
            'Go back and click the relation reload button.',
        )


def build_new_route_master(
    change: RouteMasterChange | None,
    route_tags: dict[str, str],
    route_ref: int,
    placeholders: RelationPlaceholders,
) -> dict | None:
    """
    The route master to create, holding the route this changeset is about.

    `route_ref` is the route's id, which for a route being created by this same changeset
    is its placeholder: the master refers to it exactly as the route relation is written.
    """
    if change is None or change.id is not None:
        return None

    # the tags that make it a master are not the mapper's to get wrong
    tags = {**normalize_tags(change.tags), **route_master_tags_for(route_tags)}

    if not tags['route_master']:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'A route master needs a kind of route to be a master of')

    _check_tags(tags)

    return {
        '@id': placeholders.take(),
        'tag': [{'@k': k, '@v': v} for k, v in tags.items()],
        'member': [{'@type': 'relation', '@ref': route_ref, '@role': ''}],
    }


async def build_route_master_modifications(
    change: RouteMasterChange | None,
    detach: Sequence[int],
    route_ref: int | None,
    osm,
) -> list[dict]:
    """
    The route masters already in OSM that this changeset changes.

    One gains the route and any tag edits the mapper made to it; the ones being detached
    from lose it. A master that comes back unchanged is left out rather than uploaded with
    a new version that says nothing.

    `route_ref` is how the route is referred to: its id, or its placeholder when this same
    changeset is creating it. A master already in OSM can take on a route that does not
    exist yet, the modify block being read after the create block. None means no route is
    involved at all — the master's own tags are being edited and its members left alone.
    """
    wanted: dict[int, RouteMasterChange | None] = {}

    if change is not None and change.id is not None:
        wanted[change.id] = change

    for master_id in detach:
        if master_id in wanted:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f'Route master {master_id} cannot be joined and left by the same changeset',
            )
        wanted[master_id] = None

    if not wanted:
        return []

    # a route being created, or no route at all, is in nothing to be detached from
    if (route_ref is None or route_ref < 0) and any(target is None for target in wanted.values()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'A route being created is not in any route master')

    result = []

    # fetched again rather than trusting what was loaded, so a master the route was added
    # to in the meantime does not get it twice
    for relation in await osm.get_relations(tuple(map(str, wanted)), json=False):
        master_id = int(relation['@id'])
        tags = {tag['@k']: tag['@v'] for tag in ensure_list(relation.get('tag') or [])}

        # it has stopped being a route master since, so it is not ours to put a route in
        if not is_route_master(tags):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f'Conflict: relation {master_id} is no longer a route master. '
                'Go back and click the relation reload button.',
            )

        target = wanted[master_id]
        members = ensure_list(relation.get('member') or [])
        key = f'relation/{route_ref}'
        present = any(f'{m["@type"]}/{m["@ref"]}' == key for m in members)

        if route_ref is None:
            # only the master's own tags are being changed; what it holds is not in question
            updated = members
            changed_members = False
        elif target is None:
            updated = [m for m in members if f'{m["@type"]}/{m["@ref"]}' != key]
            changed_members = len(updated) != len(members)
        elif present:
            updated = members
            changed_members = False
        else:
            updated = [*members, {'@type': 'relation', '@ref': route_ref, '@role': ''}]
            changed_members = True

        relation.pop('@timestamp', None)
        relation.pop('@user', None)
        relation.pop('@uid', None)

        changed_tags = False
        if target is not None and target.tagsOriginal is not None:
            changed_tags = apply_tag_changes(
                relation, target.tagsOriginal, target.tags, PROTECTED_ROUTE_MASTER_KEYS
            )

        if not changed_members and not changed_tags:
            continue

        relation['member'] = updated
        result.append(relation)

    return result
