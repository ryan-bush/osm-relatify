from collections.abc import Iterable, Sequence

from models.bounding_box import BoundingBox
from models.route_master import RouteMaster, RouteMasterRoute

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


def build_route_master_candidates_query(
    ref: str,
    route_value: str,
    bounds: BoundingBox,
    timeout: int,
) -> str:
    """
    Overpass query for the route masters that routes sharing this ref already belong to.

    Route masters have no geometry of their own, so they are reached through their
    members: the routes with the same ref in the downloaded area, and from those the
    relations holding them.
    """
    ref = ref.strip()
    if not ref or not route_value:
        return ''

    filters = f'["type"="route"]["route"="{escape_overpass_value(route_value)}"]["ref"="{escape_overpass_value(ref)}"]'

    return (
        f'[out:json][timeout:{timeout}];'
        f'rel{filters}({bounds})->.r;'
        f'rel(br.r)["type"="{ROUTE_MASTER_TYPE}"];'
        f'out meta;'
    )


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
        )

    return result


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
