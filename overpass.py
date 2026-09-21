import asyncio
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import chain
from typing import NamedTuple

import httpx
import xmltodict
from asyncache import cached
from cachetools import TTLCache
from fastapi import HTTPException
from starlette import status

from bus_collection_builder import build_bus_stop_collections, name_unnamed_stop_positions, stop_position_headings
from config import (
    DOWNLOAD_RELATION_GRID_CELL_EXPAND,
    DOWNLOAD_RELATION_WAY_BB_EXPAND,
    OVERPASS_API_ATTEMPTS,
    OVERPASS_API_INTERPRETERS,
    OVERPASS_MAX_DATA_AGE,
)
from driving_side import DrivingSide, build_driving_side_statements, parse_driving_side
from models.bounding_box import BoundingBox
from models.bounding_box_collection import BoundingBoxCollection
from models.download_history import Cell, DownloadHistory
from models.element_id import ElementId, element_id
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection, FetchRelationElement
from models.route_master import RouteMaster
from models.stop_area import StopArea
from route_masters import (
    build_route_master_candidates_query,
    build_route_master_candidates_statements,
    parse_route_masters,
)
from stop_areas import parse_stop_areas
from u_turns import relation_u_turn_nodes
from utils import HTTP, overpass_settings
from xmltodict_postprocessor import postprocessor

# TODO: right hand side detection by querying roundabouts, and first/last bus stop


# Overpass sometimes refuses new connections or replies with a temporary error
# (rate limit, gateway timeout, server overloaded); retry and fall back to the
# other configured instances instead of failing the whole request.
_RETRY_STATUS_CODES = frozenset((429, 502, 503, 504))


class OverpassReplyError(Exception):
    """Overpass answered with a 200 that carries an error rather than data."""

# Every reply says how far its data has caught up: `timestamp_osm_base` in JSON,
# `osm_base` on the meta element in XML. Both sit in the first few hundred bytes.
_OSM_BASE_RE = re.compile(r'(?:"timestamp_osm_base"\s*:\s*"|osm_base=")([^"]+)"')


def data_age(response: httpx.Response) -> float | None:
    """How many seconds behind live OSM this reply's data is, or None when it does not say."""
    match = _OSM_BASE_RE.search(response.text[:4096])
    if match is None:
        return None

    try:
        stamp = datetime.fromisoformat(match[1])
    except ValueError:
        return None

    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)

    return (datetime.now(UTC) - stamp).total_seconds()


# Overpass answers some failures with 200 and an error page in place of the data, and
# others with a note buried in an otherwise well-formed reply. Neither is data.
_HTML_ERROR_RE = re.compile(r'<strong[^>]*>\s*Error\s*</strong>\s*:?\s*(.*?)</p>', re.DOTALL | re.IGNORECASE)
# a remark carries quotes of its own, escaped, so the value cannot just run to the next one
_JSON_REMARK_RE = re.compile(r'"remark"\s*:\s*"((?:[^"\\]|\\.)*)"')
_XML_REMARK_RE = re.compile(r'<remark>(.*?)</remark>', re.DOTALL)


def reply_error(response: httpx.Response) -> str | None:
    """
    What went wrong in a reply Overpass still gave a 200 to, or None when it is data.

    A query the server could not run comes back as an HTML page saying so, and one it
    gave up part way through comes back as valid JSON with a remark and whatever it had
    managed to collect. Passed on as data, the first fails much later on the parse and
    the second quietly looks like an area with nothing in it.
    """
    if response.headers.get('content-type', '').startswith('text/html'):
        match = _HTML_ERROR_RE.search(response.text)
        return ' '.join(match[1].split()) if match else 'answered with a page instead of data'

    match = _JSON_REMARK_RE.search(response.text) or _XML_REMARK_RE.search(response.text)
    if match is None:
        return None

    # a remark also carries harmless notes, such as how many areas were considered
    remark = ' '.join(match[1].split())
    return remark if 'error' in remark.lower() else None


def _describe_age(age: float) -> str:
    if age >= 172_800:
        return f'{age / 86_400:.0f} days'
    if age >= 7200:
        return f'{age / 3600:.0f} hours'
    return f'{age / 60:.0f} minutes'


# A 429 means every query slot this instance gives an address is busy: a slot is held
# for the length of the query that used it, so a fixed short backoff retries into the
# same refusal. The status endpoint says when the next one frees up.
_STATUS_FREE_RE = re.compile(r'(\d+)\s+slots? available now')
_STATUS_AFTER_RE = re.compile(r'in (-?\d+) seconds')
# Waiting longer than this for a slot is slower than asking the next instance, which has
# slots of its own.
_MAX_SLOT_WAIT = 20.0

# How long to spend reaching an instance before it counts as unreachable. A handshake
# with a machine that is there takes a fraction of a second, and the transport tries
# each address a few times over, so this is the length of a hang rather than of a
# connection worth waiting for.
_CONNECT_TIMEOUT = 5.0


async def slot_wait(url: str) -> float | None:
    """
    How many seconds until this instance has a free query slot, or None when it cannot say.

    0 means one is free already, which happens when the slot that refused the query has
    since been given back.
    """
    status_url = url.removesuffix('/').removesuffix('interpreter') + 'status'

    try:
        r = await HTTP.get(status_url, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError:
        return None

    text = r.text
    # both lines appear together when some slots are free and others are not
    free = _STATUS_FREE_RE.search(text)
    if free is not None and int(free[1]) > 0:
        return 0.0

    waits = [int(m) for m in _STATUS_AFTER_RE.findall(text)]
    return max(0.0, float(min(waits))) if waits else None


def describe_transport_error(error: BaseException) -> str:
    """
    What actually went wrong underneath an httpx error, in as few words as it takes.

    httpx says the same thing whatever the reason - "All connection attempts failed"
    covers a name that would not resolve, a machine that refused the connection and a
    network that has gone away - and the reason is what says whether to look at the
    instance or at the connection here. It is the far end of the __cause__ chain, or the
    ends of all of them when the addresses failed for different reasons.
    """
    reasons: list[str] = []
    seen: set[int] = set()

    def walk(e: BaseException | None, depth: int = 0) -> None:
        if e is None or id(e) in seen or depth > 10:
            return

        seen.add(id(e))

        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                walk(sub, depth + 1)
            return

        deeper = e.__cause__ or e.__context__
        if deeper is not None:
            walk(deeper, depth + 1)
            return

        # A timeout ends in the cancellation that enforced it, which describes the
        # machinery rather than what happened; the httpx error itself says it better.
        if isinstance(e, asyncio.CancelledError):
            return

        # the end of the chain: the OSError the operating system raised
        reason = f'{type(e).__name__}: {e}' if str(e) else type(e).__name__
        if reason not in reasons:
            reasons.append(reason)

    walk(error)

    if reasons:
        return ', '.join(reasons)

    return f'{type(error).__name__}: {error}' if str(error) else type(error).__name__


async def overpass_post(query: str, query_timeout: float) -> httpx.Response:
    last_error: Exception | None = None
    # whether any instance answered at all, whatever it said: none did points at the
    # connection here rather than at instances that are merely busy
    answered = False
    # the least far behind of the instances that answered but are too old to use
    stale: tuple[float, str] | None = None

    for url in OVERPASS_API_INTERPRETERS:
        for attempt in range(1, OVERPASS_API_ATTEMPTS + 1):
            # how long this instance said to wait for a free slot, used in place of the
            # backoff below once it has answered a 429
            wait_for_slot: float | None = None

            try:
                # the query's own allowance covers reading the answer; reaching the
                # machine at all is quick or not happening, and a connect left to the
                # allowance of a large download would hang on it for minutes
                r = await HTTP.post(
                    url,
                    data={'data': query},
                    timeout=httpx.Timeout(query_timeout * 2, connect=_CONNECT_TIMEOUT),
                )
            except httpx.HTTPError as e:
                last_error = e
                print(f'[OVERPASS] ⚠️ {url} unreachable (attempt {attempt}): {describe_transport_error(e)}')
            else:
                answered = True

                if r.status_code in _RETRY_STATUS_CODES:
                    last_error = httpx.HTTPStatusError(
                        f'{url} returned {r.status_code}', request=r.request, response=r
                    )
                    print(f'[OVERPASS] ⚠️ {url} returned {r.status_code} (attempt {attempt})')

                    if r.status_code == 429:
                        wait_for_slot = await slot_wait(url)

                        # Another instance is quicker than sitting out a long query
                        # someone else is running, and an instance that will not say is
                        # no better than one that says too long.
                        if wait_for_slot is None or wait_for_slot > _MAX_SLOT_WAIT:
                            said = (
                                'did not say when a slot frees up'
                                if wait_for_slot is None
                                else f'has no slot for another {wait_for_slot:.0f}s'
                            )
                            print(f'[OVERPASS] ⚠️ {url} {said}, trying another instance')
                            break

                elif (reported := reply_error(r)) is not None:
                    last_error = OverpassReplyError(f'{url} reported: {reported}')
                    print(f'[OVERPASS] ⚠️ {url} answered 200 with an error (attempt {attempt}): {reported}')

                else:
                    r.raise_for_status()

                    age = data_age(r)
                    if not OVERPASS_MAX_DATA_AGE or age is None or age <= OVERPASS_MAX_DATA_AGE:
                        return r

                    # An instance this far behind answers everything successfully and
                    # wrongly, so it is passed over for one that has caught up. Waiting
                    # would not help, so the remaining attempts on it are skipped.
                    print(f'[OVERPASS] ⚠️ {url} is {_describe_age(age)} behind, trying another instance')
                    if stale is None or age < stale[0]:
                        stale = (age, url)
                    break

            if attempt < OVERPASS_API_ATTEMPTS:
                # a second past the slot, so the retry does not race the release
                delay = wait_for_slot + 1 if wait_for_slot is not None else 2 ** (attempt - 1)
                await asyncio.sleep(delay)

    if stale is not None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f'Every Overpass instance is out of date - the closest, {stale[1]}, is '
            f'{_describe_age(stale[0])} behind. Editing from data that old would recreate stops '
            'that already exist, so please try again later.',
        )

    if not answered:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            'Could not reach any Overpass instance. None of them answered at all, which is '
            'usually this connection rather than Overpass - check the network and try again.',
        ) from last_error

    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        'Overpass API is currently unavailable, please try again later',
    ) from last_error


class QueryRelationResult(NamedTuple):
    """Everything one download of a route's area says, including what rode along with it."""

    bounds: BoundingBox
    download_hist: DownloadHistory
    download_triggers: dict[ElementId, tuple[Cell, ...]]
    ways: dict[ElementId, FetchRelationElement]
    id_map: dict[int, list[ElementId]]
    bus_stop_collections: list[FetchRelationBusStopCollection]
    stop_areas: list[StopArea]
    driving_side: DrivingSide | None
    route_master_candidates: list[RouteMaster]


class QueryParentsResult(NamedTuple):
    id_relations_map: dict[int, list[dict]]
    ways_map: dict[int, dict]


def split_by_count(elements: Iterable[dict]) -> list[list[dict]]:
    result = []
    current_split = []

    for e in elements:
        if e['type'] == 'count':
            result.append(current_split)
            current_split = []
        else:
            current_split.append(e)

    assert not current_split, 'Last element must be count type'
    return result


def download_maxsize_mib(cells: int) -> int:
    """
    How much memory an area download says it may need, for the number of areas it covers.

    A cell is about a kilometre square, and one dense with roads and stops takes a few
    tens of megabytes to work through. Asking for what the download plausibly needs
    rather than the 512 MiB default gets it past a busy instance's admission check,
    while still leaving room for a city centre.
    """
    return min(512, max(128, 32 * cells))


def build_bb_query(relation_id: int, timeout: int) -> str:
    return overpass_settings(timeout, 64) + f'rel({relation_id});way(r);out ids bb qt;'


class DownloadExtras(NamedTuple):
    """What the area download carries on top of the area itself."""

    statements: str
    blocks: int


def build_download_extras(cell_bbs: Sequence[BoundingBox], ref: str, route_value: str) -> DownloadExtras:
    """
    The driving side and the route master candidates, to ride along with the download.

    Asked on their own they were two more queries in the same moment as the download, and
    the public instance hands one address two query slots a minute: loading a route asked
    for four and the third came back 429. Neither needs anything the download does not
    already have - the area, and the route's ref - so neither needs a query of its own.
    """
    bounds = BoundingBox(
        minlat=min(bb.minlat for bb in cell_bbs),
        minlon=min(bb.minlon for bb in cell_bbs),
        maxlat=max(bb.maxlat for bb in cell_bbs),
        maxlon=max(bb.maxlon for bb in cell_bbs),
    )
    # rounded so that panning a little does not make a different query out of it
    lat = round((bounds.minlat + bounds.maxlat) / 2, 2)
    lon = round((bounds.minlon + bounds.maxlon) / 2, 2)

    statements = build_driving_side_statements(lat, lon) + 'out count;'
    blocks = 1

    masters = build_route_master_candidates_statements(ref, route_value, bounds)
    if masters:
        statements += masters + 'out count;'
        blocks += 1

    return DownloadExtras(statements, blocks)


# Where a bus can turn around on the spot. A turning circle is the tag for it, and a
# mini roundabout is the same thing by another name - a bus that fits round one comes back
# out the way it came in, which is exactly what routes do at the end of a residential leg.
_TURN_IN_PLACE_TAGS = ('turning_circle', 'mini_roundabout')


def build_query(
    cell_bbs: Sequence[BoundingBox],
    cell_bbs_expanded: Sequence[BoundingBox],
    timeout: int,
    route_type: str,
    extras: str = '',
) -> str:
    settings = overpass_settings(timeout, download_maxsize_mib(len(cell_bbs)))

    if route_type == 'bus':
        return (
            settings
            + '(' + ''.join(f'way[highway][!footway]({bb});' for bb in cell_bbs) + ');'
            'out body qt;'
            'out count;'
            '>;'
            'out skel qt;'
            'out count;'
            '(' + ''.join(
                f'node[highway={tag}]({bb});' for bb in cell_bbs for tag in _TURN_IN_PLACE_TAGS
            ) + ');'
            'out tags qt;'
            'out count;'
            + ''.join(
                # unnamed too: one mapped without a name is still the stop, and NaPTAN
                # would otherwise offer a second one on top of it
                f'node[highway=bus_stop][public_transport=platform]({bb});'
                f'out tags center qt;'
                f'nwr[highway=platform][public_transport=platform][name]({bb});'
                f'out tags center qt;'
                f'nwr[highway=platform][public_transport=platform][ref]({bb});'
                f'out tags center qt;'
                f'node[public_transport=stop_position][name]({bb});'
                f'out tags center qt;'
                f'node[public_transport=stop_position][bus=yes][!name]({bb});'
                f'out tags center qt;'
                for bb in cell_bbs_expanded
            )
            + 'out count;'
            '(' + ''.join(f'rel[public_transport=stop_area]({bb});' for bb in cell_bbs_expanded) + ')->.r;'
            '.r out body qt;'
            '.r out count;'
            'node(r.r:platform);'
            'out tags center qt;'
            'way(r.r:platform);'
            'out tags center qt;'
            'rel(r.r:platform);'
            'out tags center qt;'
            'out count;'
            '(node(r.r:stop);node(r.r:stop_position););'
            'out tags center qt;'
            'out count;' + extras
        )

    if route_type == 'tram':
        return (
            settings
            + '(' + ''.join(f'way[railway=tram]({bb});' for bb in cell_bbs) + ');'
            'out body qt;'
            'out count;'
            '>;'
            'out skel qt;'
            'out count;'
            '(' + ''.join(
                f'node[highway={tag}]({bb});' for bb in cell_bbs for tag in _TURN_IN_PLACE_TAGS
            ) + ');'
            'out tags qt;'
            'out count;'
            + ''.join(
                f'node[railway=tram_stop][public_transport=stop_position][name]({bb});'
                f'out tags center qt;'
                f'nwr[railway=platform][public_transport=platform][name]({bb});'
                f'out tags center qt;'
                f'nwr[railway=platform][public_transport=platform][ref]({bb});'
                f'out tags center qt;'
                f'nwr[tram][public_transport=platform][name]({bb});'
                f'out tags center qt;'
                for bb in cell_bbs_expanded
            )
            + 'out count;'
            '(' + ''.join(f'rel[public_transport=stop_area]({bb});' for bb in cell_bbs_expanded) + ')->.r;'
            '.r out body qt;'
            '.r out count;'
            'node(r.r:platform);'
            'out tags center qt;'
            'way(r.r:platform);'
            'out tags center qt;'
            'rel(r.r:platform);'
            'out tags center qt;'
            'out count;'
            '(node(r.r:stop);node(r.r:stop_position););'
            'out tags center qt;'
            'out count;' + extras
        )

    raise NotImplementedError(f'Unsupported route type {route_type!r}')


def build_parents_query(way_ids: Iterable[int], timeout: int) -> str:
    def _parents(way_id: int) -> str:
        return f'way({way_id});(rel(bw);.r;)->.r;'

    return (
        overpass_settings(timeout, 128, out='xml')
        + '._->.r;' + ''.join(_parents(way_id) for way_id in way_ids) + '.r out meta qt;'
        'way(r.r);'
        'out skel qt;'
    )


def is_routable(tags: dict[str, str], route_type: str) -> bool:
    if route_type == 'bus':
        highway_valid = tags['highway'] in {
            'residential',
            'service',
            'unclassified',
            'tertiary',
            'tertiary_link',
            'secondary',
            'secondary_link',
            'primary',
            'primary_link',
            'living_street',
            'trunk',
            'trunk_link',
            'motorway',
            'motorway_link',
            'motorway_junction',
            'road',
            'busway',
            'bus_guideway',
        }

        highway_designated_valid = tags['highway'] in {
            'pedestrian',
        }

        # parking_aisle is allowed: routes are often added before the road is retagged
        service_valid = tags.get('service', 'no') not in {
            'driveway',
            'alley',
            'emergency_access',
        }

        access_designated = False
        access_valid = True

        if 'bus:conditional' in tags:
            access_designated = access_valid = True
        elif 'bus' in tags:
            access_designated = access_valid = tags['bus'] not in {'no'}
        elif 'psv' in tags:
            access_designated = access_valid = tags['psv'] not in {'no'}
        elif 'motor_vehicle' in tags:
            access_valid = tags['motor_vehicle'] not in {'private', 'customers', 'no'}
        elif 'access' in tags:
            access_valid = tags['access'] not in {'private', 'customers', 'no'}

        noarea_valid = tags.get('area', 'no') in {'no'}

        return all(
            (
                (highway_valid or (highway_designated_valid and access_designated)),
                (service_valid or access_designated),
                access_valid,
                noarea_valid,
            )
        )

    if route_type == 'tram':
        # all overpass-fetched elements are routable
        return True

    raise NotImplementedError(f'Unsupported route type {route_type!r}')


def is_oneway(tags: dict[str, str]) -> bool:
    # TODO: it would be nice to support oneway=-1

    roundabout_valid = False

    if 'junction' in tags:
        roundabout_valid = tags['junction'] in {'roundabout'}

    oneway_valid = roundabout_valid

    if 'oneway:bus' in tags:
        oneway_valid = tags['oneway:bus'] in {'yes'}
    elif 'oneway:psv' in tags:
        oneway_valid = tags['oneway:psv'] in {'yes'}
    elif 'oneway' in tags:
        oneway_valid = tags['oneway'] in {'yes'}

    return oneway_valid


def is_roundabout(tags: dict[str, str]) -> bool:
    return tags.get('junction', 'no') in {'roundabout'}


def is_bus_explicit(tags: dict[str, str]) -> bool:
    return tags.get('bus') == 'yes' or tags.get('trolleybus') == 'yes'


def is_any_rail_related(tags: dict[str, str]) -> bool:
    rail_valid = 'railway' in tags
    tram_valid = tags.get('tram', 'no') in {'yes'}
    train_valid = tags.get('train', 'no') in {'yes'}
    subway_valid = tags.get('subway', 'no') in {'yes'}
    return any((rail_valid, tram_valid, train_valid, subway_valid))


def is_tram_element(tags: dict[str, str]) -> bool:
    rail_valid = 'railway' in tags
    tram_valid = tags.get('tram', 'no') in {'yes'}
    train_valid = tags.get('train', 'no') in {'yes'}
    subway_valid = tags.get('subway', 'no') in {'yes'}
    return tram_valid or (rail_valid and not train_valid and not subway_valid)


# stop_position is the role older stop areas give their stop positions, before PTv2
# settled on stop; it means the same
_STOP_AREA_ROLES = {'platform': 'platform', 'stop': 'stop_position', 'stop_position': 'stop_position'}


@dataclass(frozen=True, slots=True)
class StopAreaPlace:
    """What a stop takes from the stop area it is in, without it becoming the stop's tags."""

    # the area's tags, for telling which kind of transport the stop is for
    tags: dict[str, str]
    # the name of the place, for a stop that has none of its own
    name: str
    # what the area's role says the stop is, for one not tagged public_transport itself
    public_transport: str


def stop_area_places(
    relations: Iterable[dict],
    platforms: Iterable[dict],
    stop_positions: Iterable[dict],
) -> dict[tuple[str, int], StopAreaPlace]:
    """
    What each member of a stop area takes from it, keyed by (type, id).

    None of it goes into the members' tags, which are what OSM holds and what edits are
    checked against: a platform with no name of its own is not one called whatever its
    area is. An area with no name is named by its members instead, so the stop position
    of an unnamed pair is still grouped with the platform beside it.
    """
    elements = {(e['type'], e['id']): e for e in chain(platforms, stop_positions)}
    result: dict[tuple[str, int], StopAreaPlace] = {}

    for relation in sorted(relations, key=lambda r: r['id']):
        members = [
            (member, _STOP_AREA_ROLES[member['role']])
            for member in relation['members']
            if member['role'] in _STOP_AREA_ROLES
        ]

        tags = relation.get('tags', {})
        name = tags.get('name', '').strip()

        if not name:
            # platforms first, being what the sign is on
            for member, _ in sorted(members, key=lambda m: m[1] != 'platform'):
                element = elements.get((member['type'], member['ref']))
                if element is not None and (name := element.get('tags', {}).get('name', '').strip()):
                    break

        for member, public_transport in members:
            key = (member['type'], member['ref'])

            if key not in elements:
                print(f'🚧 Warning: Stop area member {member["type"]}/{member["ref"]} not found in map')
                continue

            # the lowest-numbered area a stop is in speaks for it
            result.setdefault(key, StopAreaPlace(tags=tags, name=name, public_transport=public_transport))

    return result


def stop_elements(elements: Iterable[dict], places: Mapping[tuple[str, int], StopAreaPlace]) -> tuple[dict, ...]:
    """
    The elements that can be read as stops, each with its tags, even if it has none.

    A stop area can hold a bare node, with nothing but its role to say what it is.
    Anything neither tagged as a stop nor holding a stop's role says nothing at all.
    """
    result = []

    for element in elements:
        element.setdefault('tags', {})

        if 'public_transport' in element['tags'] or (element['type'], element['id']) in places:
            result.append(element)

    return tuple(result)


def _create_node_counts(ways: list[dict]) -> Counter[int]:
    node_counts: Counter[int] = Counter()
    for way in ways:
        node_counts.update(way['nodes'])
    return node_counts


def _split_way_on_intersection(way: dict, node_counts: Mapping[int, int]) -> list[list[int]]:
    segments: list[list[int]] = []
    current_segment: list[int] = []

    for node in way['nodes']:
        current_segment.append(node)

        if node_counts[node] > 1 and len(current_segment) > 1:
            segments.append(current_segment)
            current_segment = [node]

    if len(current_segment) > 1:
        segments.append(current_segment)

    return segments


def organize_ways(ways: list[dict], turn_in_place_nodes: set[int]) -> tuple[list[dict], dict[ElementId, set[ElementId]], dict[int, list[ElementId]]]:
    node_counts = _create_node_counts(ways)
    node_to_way_map = defaultdict(set)

    split_ways: list[dict] = []
    connected_ways_map: dict[ElementId, set[ElementId]] = defaultdict(set)
    id_map = defaultdict(list)

    for way in ways:
        split_segments = _split_way_on_intersection(way, node_counts)

        for extra_num, segment in enumerate(split_segments, 1):
            extra_num = extra_num if len(split_segments) > 1 else None
            max_num = len(split_segments) if extra_num is not None else None

            split_way = {
                **way,
                'id': element_id(way['id'], extra_num=extra_num, max_num=max_num),
                'nodes': segment,
                '_turn_in_place_start': segment[0] in turn_in_place_nodes,
                '_turn_in_place_end': segment[-1] in turn_in_place_nodes,
            }

            split_ways.append(split_way)
            id_map[way['id']].append(split_way['id'])

            for node in segment:
                if node_counts[node] > 1:
                    for other_way_id in node_to_way_map[node]:
                        connected_ways_map[split_way['id']].add(other_way_id)
                        connected_ways_map[other_way_id].add(split_way['id'])
                    node_to_way_map[node].add(split_way['id'])

    return split_ways, connected_ways_map, id_map


def existing_stop_areas(
    relations: Iterable[dict],
    collections: Iterable[FetchRelationBusStopCollection],
) -> list[StopArea]:
    """
    The stop areas the downloaded stops are already in, so none of them is duplicated.

    Read out of the download itself: it already asks for every stop_area relation in the
    area, to name the stops that take their name from one. Asking Overpass a second time
    told us no more than this does, and each query is another chance of being turned away
    by a busy instance.
    """
    stop_keys = {stop.nice_id for c in collections for stop in (c.platform, c.stop) if stop is not None}

    result = []
    seen: set[int] = set()

    # the same relation comes back once per area it reaches into
    for area in parse_stop_areas(relations):
        if area.id in seen or not any(member in stop_keys for member in area.members):
            continue

        seen.add(area.id)
        result.append(area)

    return result


def preprocess_elements(elements: Iterable[dict]) -> tuple[dict, ...]:
    # deduplicate by type/id
    result: tuple[dict, ...] = tuple({(e['type'], e['id']): e for e in elements}.values())
    # extract center
    for e in result:
        center: dict | None = e.get('center')
        if center is not None:
            e.update(center)
    return result


def optimize_cells_and_get_bbs(
    cells: Sequence[Cell],
    *,
    start_horizontal: bool,
) -> tuple[Sequence[BoundingBox], Sequence[BoundingBox]]:
    def merge(sorted: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
        result = []
        current = sorted[0]

        for next in sorted[1:]:
            # merge horizontally
            if current[2] + 1 == next[0] and current[1] == next[1] and current[3] == next[3]:
                current = (current[0], current[1], next[2], current[3])

            # merge vertically
            elif current[3] + 1 == next[1] and current[0] == next[0] and current[2] == next[2]:
                current = (current[0], current[1], current[2], next[3])

            # add to merged cells if cells can't be merged
            else:
                result.append(current)
                current = next

        # add the last cell
        result.append(current)

        return result

    cells_bounds = ((c.x, c.y, c.x, c.y) for c in cells)

    if start_horizontal:
        cells_bounds = sorted(cells_bounds, key=lambda c: (c[1], c[0]))
    else:
        cells_bounds = sorted(cells_bounds, key=lambda c: (c[0], c[1]))

    cells_bounds = merge(cells_bounds)

    if start_horizontal:
        cells_bounds = sorted(cells_bounds, key=lambda c: (c[0], c[1]))
    else:
        cells_bounds = sorted(cells_bounds, key=lambda c: (c[1], c[0]))

    cells_bounds = merge(cells_bounds)

    bbs = tuple(BoundingBox.from_grid_cell(*c) for c in cells_bounds)

    return bbs, tuple(bb.extend(unit_degrees=DOWNLOAD_RELATION_GRID_CELL_EXPAND) for bb in bbs)


def get_download_triggers(
    bbc: BoundingBoxCollection,
    cells: Sequence[Cell],
    ways: dict[ElementId, FetchRelationElement],
) -> dict[ElementId, tuple[Cell, ...]]:
    cells_set = frozenset(cells)
    result = {}

    for way_id, way in ways.items():
        way_new_cells = set()

        for latLng in way.latLngs:
            if bbc.contains(latLng):
                continue

            new_cells = BoundingBox(
                minlat=latLng[0],
                minlon=latLng[1],
                maxlat=latLng[0],
                maxlon=latLng[1],
            ).get_grid_cells(expand=1)  # 3x3 grid

            way_new_cells |= new_cells - cells_set

        if way_new_cells:
            result[way_id] = tuple(way_new_cells)

    return dict(result)


# TODO: check data freshness
class Overpass:
    def __init__(self):
        pass

    @cached(TTLCache(maxsize=1024, ttl=7200))  # 2 hours
    async def _query_relation_history_post(
        self,
        session: str,  # cache busting  # noqa: ARG002
        query: str,
        http_timeout: float,
        describe: str,
    ) -> list[list[dict]]:
        # here rather than at the call site, so an area already downloaded in this
        # session says nothing: it is answered from the cache without asking Overpass
        print(f'[OVERPASS] {describe}')
        r = await overpass_post(query, http_timeout)
        elements: list[dict] = r.json()['elements']
        return split_by_count(elements)

    async def _query_relation_history(
        self,
        relation_id: int,
        download_hist: DownloadHistory,
        route_type: str,
        ref: str,
        route_value: str,
    ) -> tuple[list[list[dict]], BoundingBoxCollection, DrivingSide | None, list[RouteMaster]]:
        if not download_hist.history or not all(download_hist.history):
            raise ValueError('No grid cells to download')

        all_elements_split = None
        all_bbs = []
        driving_side = None
        candidates: dict[int, RouteMaster] = {}

        for cells in download_hist.history:
            hor_bbs_t = optimize_cells_and_get_bbs(cells, start_horizontal=True)
            ver_bbs_t = optimize_cells_and_get_bbs(cells, start_horizontal=False)

            # pick more optimal cells
            cell_bbs_t = hor_bbs_t if len(hor_bbs_t) <= len(ver_bbs_t) else ver_bbs_t
            cell_bbs, cell_bbs_expand = cell_bbs_t
            all_bbs.extend(cell_bbs)

            # A cell is about a kilometre square and takes seconds to answer, so the
            # allowance grows with the area rather than always claiming the three
            # minutes the largest download might want. A busy instance weighs what a
            # query asks for when deciding whether to run it at all.
            timeout = min(180, 30 + 15 * len(cell_bbs))
            extras = build_download_extras(cell_bbs, ref, route_value)
            query = build_query(cell_bbs, cell_bbs_expand, timeout, route_type, extras.statements)
            elements_split = await self._query_relation_history_post(
                download_hist.session,
                query,
                timeout,
                f'Downloading {len(cell_bbs)} cells for relation {relation_id}',
            )

            # what rode along with this area, before the area itself is merged with the rest
            extra_split = elements_split[len(elements_split) - extras.blocks :]
            elements_split = elements_split[: len(elements_split) - extras.blocks]

            if driving_side is None:
                driving_side = parse_driving_side(extra_split[0])

            if extras.blocks > 1:
                for master in parse_route_masters(extra_split[1]):
                    candidates.setdefault(master.id, master)

            if all_elements_split is None:
                # copied: what the post returned is held in its cache, and the areas
                # downloaded after this one are merged into what is built up here
                all_elements_split = [list(elements) for elements in elements_split]
            else:
                for i, elements in enumerate(elements_split):
                    all_elements_split[i].extend(elements)

        bbc = BoundingBoxCollection(all_bbs)

        return all_elements_split, bbc, driving_side, list(candidates.values())

    @cached(TTLCache(maxsize=128, ttl=60))
    async def query_relation(
        self,
        relation_id: int,
        download_hist: DownloadHistory | None,
        download_targets: Sequence[Cell] | None,
        route_type: str,  # bus, tram...
        ref: str = '',
        route_value: str = '',
        member_way_ids: tuple[int, ...] = (),
    ) -> QueryRelationResult:
        if download_targets is None:
            timeout = 60
            query = build_bb_query(relation_id, timeout)
            r = await overpass_post(query, timeout)

            elements: list[dict] = r.json()['elements']
            if not elements:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Relation is empty, which is not supported')

            relation_way_members = {e['id'] for e in elements}
            union_grid_cells_set: set[Cell] = set()

            for way in elements:
                union_grid_cells_set.update(
                    BoundingBox(
                        minlat=way['bounds']['minlat'],
                        minlon=way['bounds']['minlon'],
                        maxlat=way['bounds']['maxlat'],
                        maxlon=way['bounds']['maxlon'],
                    )
                    .extend(DOWNLOAD_RELATION_WAY_BB_EXPAND)
                    .get_grid_cells()
                )

            union_grid_cells = tuple(union_grid_cells_set)
        else:
            # in merge mode, members are set by the client
            relation_way_members = set()

            union_grid_cells = download_targets

        if download_hist is None:
            download_hist = DownloadHistory(session=DownloadHistory.make_session(), history=(union_grid_cells,))
        elif union_grid_cells:
            download_hist = replace(download_hist, history=(*download_hist.history, union_grid_cells))

        elements_split, bbc, driving_side, route_master_candidates = await self._query_relation_history(
            relation_id, download_hist, route_type, ref, route_value
        )

        maybe_road_elements = elements_split[0]
        maybe_road_elements = preprocess_elements(maybe_road_elements)
        node_elements = elements_split[1]
        node_elements = preprocess_elements(node_elements)
        turn_in_place_elements = elements_split[2]
        turn_in_place_elements = preprocess_elements(turn_in_place_elements)

        bus_elements = elements_split[3]

        stop_area_relations = elements_split[4]
        stop_area_platform_elements = elements_split[5]
        stop_area_stop_position_elements = elements_split[6]

        places = stop_area_places(stop_area_relations, stop_area_platform_elements, stop_area_stop_position_elements)

        road_elements = tuple(e for e in maybe_road_elements if is_routable(e['tags'], route_type))

        nodes_map = {e['id']: e for e in node_elements}
        turn_in_place_nodes = {e['id'] for e in turn_in_place_elements}
        # what the map says, plus what the relation itself says by listing a way twice
        turn_in_place_nodes |= relation_u_turn_nodes(
            member_way_ids, {e['id']: e['nodes'] for e in maybe_road_elements}
        )

        for e in road_elements:
            e['_member'] = e['id'] in relation_way_members
            e['_oneway'] = is_oneway(e['tags'])
            e['_roundabout'] = is_roundabout(e['tags'])

        # before the ways are cut up, as a stop position's direction is relative to its way
        unsplit_road_elements = road_elements
        road_elements, connected_ways_map, id_map = organize_ways(road_elements, turn_in_place_nodes)

        ways = {
            e['id']: FetchRelationElement(
                id=e['id'],
                member=e['_member'],
                oneway=e['_oneway'],
                roundabout=e['_roundabout'],
                nodes=e['nodes'],
                latLngs=[(nodes_map[n_id]['lat'], nodes_map[n_id]['lon']) for n_id in e['nodes']],
                connectedTo=list(connected_ways_map[e['id']]),
                turn_in_place_start=e['_turn_in_place_start'],
                turn_in_place_end=e['_turn_in_place_end'],
            )
            for e in road_elements
        }

        elements_ex = chain(stop_area_platform_elements, stop_area_stop_position_elements, bus_elements)
        elements_ex = preprocess_elements(elements_ex)

        elements_ex = stop_elements(elements_ex, places)

        def kind_tags(e: dict) -> dict[str, str]:
            # a stop in a bus stop area is a bus stop, even if it does not say so itself
            place = places.get((e['type'], e['id']))
            return {**place.tags, **e['tags']} if place is not None else e['tags']

        if route_type == 'bus':
            elements_ex = (
                e for e in elements_ex if is_bus_explicit(kind_tags(e)) or not is_any_rail_related(kind_tags(e))
            )
        elif route_type == 'tram':
            elements_ex = (e for e in elements_ex if is_tram_element(kind_tags(e)))

        stops = name_unnamed_stop_positions(
            tuple(FetchRelationBusStop.from_data(e, places.get((e['type'], e['id']))) for e in elements_ex)
        )
        headings = stop_position_headings(
            stops,
            unsplit_road_elements,
            {n_id: (node['lat'], node['lon']) for n_id, node in nodes_map.items()},
        )
        bus_stop_collections = build_bus_stop_collections(stops, headings)
        bus_stop_collections = tuple(c for c in bus_stop_collections if bbc.contains(c.best.latLng))

        stop_areas = existing_stop_areas(stop_area_relations, bus_stop_collections)

        global_bb = BoundingBox(*bbc.idx.bounds)
        download_triggers = get_download_triggers(bbc, union_grid_cells, ways)

        return QueryRelationResult(
            bounds=global_bb,
            download_hist=download_hist,
            download_triggers=download_triggers,
            ways=ways,
            id_map=id_map,
            bus_stop_collections=bus_stop_collections,
            stop_areas=stop_areas,
            driving_side=driving_side,
            route_master_candidates=route_master_candidates,
        )

    @cached(TTLCache(maxsize=128, ttl=60))
    async def query_route_master_candidates(
        self,
        ref: str,
        route_value: str,
        bounds: BoundingBox,
    ) -> list[RouteMaster]:
        """The route masters that routes sharing this ref already belong to, asked on their own."""
        timeout = 30
        query = build_route_master_candidates_query(ref, route_value, bounds, timeout)
        if not query:
            return []

        r = await overpass_post(query, timeout)
        return parse_route_masters(r.json().get('elements', ()))

    @cached(TTLCache(maxsize=128, ttl=60))
    async def query_parents(self, way_ids_set: frozenset[int]) -> QueryParentsResult:
        timeout = 60
        query = build_parents_query(way_ids_set, timeout)
        r = await overpass_post(query, timeout)

        data: dict[str, list[dict]] = xmltodict.parse(
            r.text,
            postprocessor=postprocessor,
            force_list=('relation', 'way', 'member', 'tag', 'nd'),
        )['osm']

        relations = data.get('relation', [])
        id_relations_map = defaultdict(list)

        for relation in relations:
            members = relation['member'] = relation.get('member', [])
            # tags = relation['tag'] = relation.get('tag', [])

            if len(members) <= 1:
                continue

            for member in members:
                if member['@type'] == 'way' and member['@ref'] in way_ids_set:
                    id_relations_map[member['@ref']].append(relation)

        # unique relations
        for way_id, relations in id_relations_map.items():
            id_relations_map[way_id] = list({r['@id']: r for r in relations}.values())

        ways = data.get('way', [])
        ways_map = {w['@id']: w for w in ways}

        return QueryParentsResult(
            id_relations_map=id_relations_map,
            ways_map=ways_map,
        )
