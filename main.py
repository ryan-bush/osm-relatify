import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from itertools import chain
from typing import Annotated
from urllib.parse import urlencode

import orjson
from dacite import Config, from_dict
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.responses import ORJSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from httpx import HTTPStatusError
from pydantic import BaseModel, Field
from sentry_sdk import start_transaction
from starlette.websockets import WebSocketState

from bus_stop_creation import NewBusStop, NewStopPosition
from compression import deflate_compress, deflate_decompress
from config import (
    CALC_ROUTE_MAX_PROCESSES,
    CALC_ROUTE_N_PROCESSES,
    CREATED_BY,
    NAPTAN_ENABLED,
    OSM_CLIENT,
    OSM_IS_LIVE,
    OSM_SCOPES,
    OSM_SECRET,
    OSM_URL,
    TAG_MAX_LENGTH,
    TEST_ENV,
    WEBSITE,
)
from cython_lib.route import calc_bus_route
from deflate_middleware import DeflateRoute
from models.bounding_box import BoundingBox
from models.download_history import Cell, DownloadHistory
from models.element_id import ElementId, split_element_id
from models.fetch_relation import (
    FetchRelation,
    FetchRelationBusStopCollection,
    FetchRelationElement,
    PublicTransport,
    assign_none_members,
    find_start_stop_ways,
)
from models.final_route import FinalRoute, WarningSeverity
from models.route_master import RouteMaster
from models.stop_area import StopArea
from naptan import NAPTAN, Roads
from naptan_tags import StopTagAddition
from openstreetmap import OpenStreetMap
from overpass import Overpass
from placeholder_ids import RelationPlaceholders
from relation_builder import (
    build_osm_change,
    build_route_master_only_change,
    get_relation_members,
    sort_and_upgrade_members,
)
from route_types import get_route_type, get_route_value
from route_masters import (
    MAX_DESCRIBED_ROUTES,
    RouteMasterChange,
    build_route_master_view,
    describe_members,
    is_route_master,
    member_route_ids,
    parse_route_masters,
    parse_routes,
)
from route_warnings import check_for_issues
from stop_areas import StopAreaChange
from tag_editing import normalize_tags
from user_session import fetch_user_details, require_user_access_token, require_user_details
from utils import HTTP, print_run_time

_SESSION_MAX_AGE = 31536000  # 1 year
_TEMPLATES = Jinja2Templates(directory='templates', auto_reload=TEST_ENV)
_TEMPLATES.env.globals.update(osm_url=OSM_URL, osm_is_live=OSM_IS_LIVE)

_PROCESS_EXECUTOR = ProcessPoolExecutor(CALC_ROUTE_MAX_PROCESSES)
_OSM = OpenStreetMap()
_OVERPASS = Overpass()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with _OSM:
        # in the background, so starting up does not wait on the download
        naptan_task = asyncio.create_task(NAPTAN.keep_fresh()) if NAPTAN_ENABLED else None
        try:
            yield
        finally:
            if naptan_task is not None:
                naptan_task.cancel()


app = FastAPI(
    debug=True,
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
app.router.route_class = DeflateRoute


class RevalidatedStaticFiles(StaticFiles):
    """
    Serves static files with `Cache-Control: no-cache`.

    Nothing here is versioned in its filename, so a browser given no instruction applies
    heuristic freshness and may reuse a file for the rest of a session without asking.
    For a module that means an edited script quietly not running, which looks like the
    change never happened rather than like a caching problem.

    `no-cache` is revalidate, not do-not-store: the browser still keeps the file and still
    asks, and the answer is a 304 with no body whenever it has not changed.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers['cache-control'] = 'no-cache'
        return response


app.mount('/static', RevalidatedStaticFiles(directory='static', html=True), name='static')


@app.get('/')
async def index(request: Request, user=Depends(fetch_user_details)):
    if user is not None:
        return _TEMPLATES.TemplateResponse('authorized.jinja2', {'request': request, 'user': user})
    else:
        return _TEMPLATES.TemplateResponse('index.jinja2', {'request': request})


@app.post('/login')
async def login(request: Request):
    state = os.urandom(32).hex()
    authorization_url = f'{OSM_URL}/oauth2/authorize?' + urlencode({
        'client_id': OSM_CLIENT,
        'redirect_uri': str(request.url_for('callback')),
        'response_type': 'code',
        'scope': OSM_SCOPES,
        'state': state,
    })
    response = RedirectResponse(authorization_url, status.HTTP_303_SEE_OTHER)
    response.set_cookie('oauth_state', state, secure=not TEST_ENV, httponly=True)
    return response


@app.get('/callback')
async def callback(request: Request, code: Annotated[str, Query()], state: Annotated[str, Query()]):
    cookie_state = request.cookies.get('oauth_state')
    if cookie_state != state:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Invalid OAuth state')
    r = await HTTP.post(
        f'{OSM_URL}/oauth2/token',
        data={
            'client_id': OSM_CLIENT,
            'client_secret': OSM_SECRET,
            'redirect_uri': str(request.url_for('callback')),
            'grant_type': 'authorization_code',
            'code': code,
        },
    )
    r.raise_for_status()
    access_token = r.json()['access_token']
    response = RedirectResponse('/', status.HTTP_302_FOUND)
    response.set_cookie('access_token', access_token, _SESSION_MAX_AGE, secure=not TEST_ENV, httponly=True)
    return response


@app.post('/logout')
def logout():
    response = RedirectResponse('/', status.HTTP_302_FOUND)
    response.delete_cookie('access_token')
    return response


# a full viewport at low zoom is far too much to download in one go; panning grows
# the area from a sensible starting point instead
NEW_RELATION_MAX_CELLS = 256


class PostQueryModel(BaseModel):
    # absent when creating a relation that does not exist yet
    relationId: int | None = None
    downloadHistory: dict | None = None
    downloadTargets: tuple[dict, ...] | None = None
    reload: bool = False
    # creation only: the route type the user picked, and the map viewport to seed
    # the first download from, as (minlat, minlon, maxlat, maxlon)
    routeType: str | None = None
    bounds: tuple[float, float, float, float] | None = None


# the tags that make a relation a PTv2 route, and that the app itself requires to
# load one back; see get_route_type()
def make_new_relation_tags(route_type: str) -> dict[str, str]:
    return {'type': 'route', 'route': route_type, 'public_transport:version': '2'}


@app.post('/query')
async def post_query(model: PostQueryModel, _=Depends(require_user_details)):
    print(f'🔍 Querying relation ({model.relationId})')

    if model.downloadHistory is not None:
        assert model.downloadTargets is not None
        download_hist = from_dict(DownloadHistory, model.downloadHistory, Config(cast=[tuple], strict=True))
        download_targets = tuple(from_dict(Cell, t, Config(cast=[], strict=True)) for t in model.downloadTargets)

        if model.reload:
            download_hist = replace(
                download_hist,
                session=DownloadHistory.make_session(),
                history=(tuple(chain.from_iterable(download_hist.history)),),
            )
    else:
        download_hist = None
        download_targets = None

    with print_run_time('Querying relation data'):
        if model.relationId is None:
            # nothing to fetch yet: the relation is invented here and only exists
            # in OSM once the user uploads
            route_type = model.routeType
            if route_type not in {'bus', 'tram'}:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Route type must be bus or tram')

            relation = {'tags': make_new_relation_tags(route_type), 'members': []}
            relation_tags = relation['tags']

            if download_targets is None:
                if model.bounds is None:
                    raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Creating a relation requires map bounds')

                cells = BoundingBox(*model.bounds).get_grid_cells()
                if len(cells) > NEW_RELATION_MAX_CELLS:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        'Zoom in before creating a relation; the visible area is too large to download. '
                        'Panning downloads more as you go.',
                    )

                # sorted for a stable cache key, and query_relation expects a sequence
                download_targets = tuple(sorted(cells, key=lambda c: (c.x, c.y)))
        else:
            try:
                relation = await _OSM.get_relation(model.relationId)
            except HTTPStatusError as e:
                if e.response.status_code == status.HTTP_404_NOT_FOUND:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, 'Relation not found') from e
                raise

            relation_tags = relation.get('tags', {})

            # A route master is a perfectly good relation to be handed; it is simply not
            # the thing that gets edited. Its variants are listed instead, and the one
            # picked is loaded the way any route is — so nothing is downloaded here.
            if is_route_master(relation_tags):
                return await _build_route_master_view(relation)

            route_type = get_route_type(relation_tags)
            if route_type is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Relation must be a PTv2 bus/tram/trolleybus route')

        bounds, download_hist, download_triggers, ways, id_map, bus_stop_collections = await _OVERPASS.query_relation(
            relation_id=model.relationId,
            download_hist=download_hist,
            download_targets=download_targets,
            route_type=route_type,
        )

    with print_run_time('Finding start/stop ways'):
        start_way, stop_way = find_start_stop_ways(ways, id_map, relation)

    with print_run_time('Assigning members for stops'):
        bus_stop_collections = assign_none_members(bus_stop_collections, relation)

    naptan_stops = []
    naptan_tags = []
    naptan_matched = {}
    # get_route_type() reads trolleybus routes as bus
    if NAPTAN_ENABLED and route_type == 'bus':
        with print_run_time('Matching stops with NaPTAN'):
            matches = await NAPTAN.match(
                download_hist, bus_stop_collections, Roads([way.latLngs for way in ways.values()])
            )
        naptan_stops = matches.unmapped
        naptan_tags = matches.tag_suggestions
        naptan_matched = matches.matched

    with print_run_time('Finding existing stop areas'):
        stop_areas = await _query_stop_areas(bus_stop_collections)

    with print_run_time('Finding route masters'):
        route_masters, route_master_candidates = await _query_route_masters(
            model.relationId, relation_tags, bounds
        )

    return FetchRelation(
        fetchMerge=len(download_hist.history) > 1 or model.reload,
        nameOrRef=relation_tags.get('name', relation_tags.get('ref', '')).strip(),
        bounds=bounds,
        downloadHistory=download_hist,
        downloadTriggers=download_triggers,
        tags=relation_tags,
        startWay=start_way,
        stopWay=stop_way,
        ways=ways,
        busStops=bus_stop_collections,
        naptanStops=naptan_stops,
        naptanTags=naptan_tags,
        naptanMatched=naptan_matched,
        stopAreas=stop_areas,
        routeMasters=route_masters,
        routeMasterCandidates=route_master_candidates,
    )


async def _query_stop_areas(bus_stop_collections) -> list[StopArea] | None:
    """
    The stop areas the downloaded stops already belong to.

    None when Overpass could not say, which is not the same as there being none: an empty
    list is what invites the mapper to create one, and doing that unknowingly would put a
    second relation beside the one the stops are already in.
    """
    node_ids: set[int] = set()
    way_ids: set[int] = set()

    for collection in bus_stop_collections:
        for stop in (collection.platform, collection.stop):
            if stop is None:
                continue
            target = node_ids if stop.type == 'node' else way_ids
            target.add(split_element_id(stop.id).id)

    try:
        return await _OVERPASS.query_stop_areas(frozenset(node_ids), frozenset(way_ids))
    except Exception as e:
        # the download still works without them, and the client stops offering stop areas
        print(f'🚧 Warning: Could not look up stop areas: {e!r}')
        return None


async def _build_route_master_view(relation: dict):
    """The variants of a route master, for choosing which one to edit."""
    print(f'🚏 Listing route master ({relation["id"]})')

    [master] = parse_route_masters([relation])
    routes = await _describe_route_master_members([master])

    return build_route_master_view(master, routes)


async def _query_route_masters(
    relation_id: int | None,
    relation_tags: dict[str, str],
    bounds: BoundingBox,
) -> tuple[list[RouteMaster] | None, list[RouteMaster] | None]:
    """
    The route masters this route is in, and the ones it could be linked into.

    Membership is asked of OSM itself rather than of Overpass, which may be behind: a
    master created since its last snapshot would leave a route that is already in one
    looking unlinked, which is exactly what invites putting it in a second. When even OSM
    cannot say, nothing is offered — None is not the same answer as an empty list.
    """
    if relation_id is None:
        # invented here, so it is a member of nothing yet; siblings are still worth finding
        current: list[RouteMaster] = []
    else:
        try:
            current = parse_route_masters(await _OSM.get_parent_relations('relation', relation_id))
        except Exception as e:
            print(f'🚧 Warning: Could not look up route masters: {e!r}')
            return None, None

    try:
        candidates = await _OVERPASS.query_route_master_candidates(
            relation_tags.get('ref', ''),
            get_route_value(relation_tags),
            bounds,
        )
        # the route's own master is reached through the route itself, and is not a
        # relation to offer joining
        current_ids = {master.id for master in current}
        candidates = [master for master in candidates if master.id not in current_ids]
    except Exception as e:
        print(f'🚧 Warning: Could not look up route master candidates: {e!r}')
        candidates = None

    routes = await _describe_route_master_members([*current, *(candidates or ())])

    return describe_members(current, routes), (
        describe_members(candidates, routes) if candidates is not None else None
    )


async def _describe_route_master_members(masters):
    """The member routes of these masters, by id, so the client can name the variants."""
    route_ids = member_route_ids(masters)
    if not route_ids or len(route_ids) > MAX_DESCRIBED_ROUTES:
        return {}

    try:
        return parse_routes(await _OSM.get_relations(tuple(route_ids)))
    except Exception as e:
        # the masters are still worth showing, just without their variants listed
        print(f'🚧 Warning: Could not look up route master members: {e!r}')
        return {}


class PostRouteMastersModel(BaseModel):
    # absent when the relation is being created and does not exist yet
    relationId: int | None = None
    # the route's tags as the mapper has them now, which is what its siblings are found by
    tags: dict[str, str] = Field(default_factory=dict)
    # the downloaded area, as (minlat, minlon, maxlat, maxlon)
    bounds: tuple[float, float, float, float]


@app.post('/query_route_masters')
async def post_query_route_masters(model: PostRouteMastersModel, _=Depends(require_user_details)):
    """
    The route masters for the tags the route has now.

    A relation being created is downloaded before it has a ref, and a ref is the whole of
    what its siblings are found by, so the answer at download time is always that there
    are none. This asks again once there is something to ask with — and likewise once a
    ref that was wrong has been corrected.
    """
    print(f'🔍 Querying route masters ({model.relationId})')

    with print_run_time('Finding route masters'):
        current, candidates = await _query_route_masters(model.relationId, model.tags, BoundingBox(*model.bounds))

    return {'routeMasters': current, 'routeMasterCandidates': candidates}


@dataclass(frozen=True, kw_only=True, slots=True)
class PostCalcBusRouteModel:
    relationId: int | None
    startWay: ElementId
    stopWay: ElementId
    ways: dict[ElementId | str, FetchRelationElement]
    busStops: list[FetchRelationBusStopCollection]
    tags: dict[str, str]


@app.websocket('/ws/calc_bus_route')
async def post_calc_bus_route(ws: WebSocket, _=Depends(require_user_details)):
    await ws.accept()

    try:
        while True:
            request = await ws.receive_bytes()

            with start_transaction(op='websocket.server', name='/ws/calc_bus_route'):
                model = from_dict(
                    PostCalcBusRouteModel,
                    orjson.loads(deflate_decompress(request)),
                    Config(cast=[ElementId, tuple, PublicTransport], strict=True),
                )

                print(f'🛣️ Calculating bus route ({model.relationId})')
                assert model.startWay in model.ways, 'Start way not in ways'
                assert model.stopWay in model.ways, 'Stop way not in ways'
                assert all(way_id == way.id for way_id, way in model.ways.items()), 'Way ids must match'

                ways_members = {way_id: way for way_id, way in model.ways.items() if way.member}
                ways_non_members = {way_id: way for way_id, way in model.ways.items() if not way.member}

                assert ways_members, 'No ways are members of the relation'

                assert all(collection.platform.member for collection in model.busStops if collection.platform), (
                    'All bus platforms must be members of the relation'
                )
                assert all(collection.stop.member for collection in model.busStops if collection.stop), (
                    'All bus stops must be members of the relation'
                )

                try:
                    async with asyncio.TaskGroup() as tg:
                        # a relation being created has no members to preserve roles from
                        get_task = (
                            tg.create_task(_OSM.get_relation(model.relationId))
                            if model.relationId is not None
                            else None
                        )
                        route_task = tg.create_task(
                            asyncio.wait_for(
                                calc_bus_route(
                                    ways_members,
                                    model.startWay,
                                    model.stopWay,
                                    model.busStops,
                                    model.tags,
                                    _PROCESS_EXECUTOR,
                                    n_processes=CALC_ROUTE_N_PROCESSES,
                                ),
                                # MAX_SEARCH_TIME plus room for building the graph and
                                # for the workers still running when the search stops
                                timeout=11,
                            )
                        )

                # a TaskGroup reports failures as an ExceptionGroup, so a plain
                # `except TimeoutError` never matched and the timeout escaped as an
                # unhandled traceback
                except* TimeoutError:
                    print('🛑 Route calculation timed out')
                    raise HTTPException(status.HTTP_408_REQUEST_TIMEOUT, 'Route calculation timed out') from None

                relation_members = get_relation_members(get_task.result()) if get_task is not None else []

                route = route_task.result()
                route = replace(route, extraWaysToUpdate=tuple(ways_non_members.values()))
                route = sort_and_upgrade_members(route, relation_members)

                inactive_naptan_codes = await NAPTAN.find_inactive(route.busStops) if NAPTAN_ENABLED else frozenset()

                final_route = check_for_issues(
                    route=route,
                    ways=ways_members,
                    start_way=model.startWay,
                    end_way=model.stopWay,
                    bus_stop_collections=model.busStops,
                    relation_members=relation_members,
                    inactive_naptan_codes=inactive_naptan_codes,
                )

                response = deflate_compress(orjson.dumps(final_route, option=orjson.OPT_STRICT_INTEGER))
                await ws.send_bytes(response)

    except WebSocketDisconnect:
        pass
    finally:
        if ws.client_state == WebSocketState.CONNECTED and ws.application_state == WebSocketState.CONNECTED:
            await ws.close(1011)


class PostDownloadOsmChangeModel(BaseModel):
    # absent when the relation is being created by this very changeset
    relationId: int | None = None
    route: dict
    tags: dict[str, str]
    # tags exactly as the client loaded them; the baseline the tag edits are diffed against.
    # absent (older clients) means no tag editing, in which case relation tags are left alone.
    tagsOriginal: dict[str, str] | None = None
    # overrides the generated changeset comment when the user provides one
    comment: str | None = Field(default=None, max_length=TAG_MAX_LENGTH)
    # bus stops placed on the map, created by this changeset
    newStops: list[NewBusStop] = Field(default_factory=list)
    # stop positions to put on the road, for new stops and for stops already in OSM
    newStopPositions: list[NewStopPosition] = Field(default_factory=list)
    # stop areas to create, and existing ones to add the stops they are missing to
    stopAreas: list[StopAreaChange] = Field(default_factory=list)
    # NaPTAN tags to add to stops already in OSM
    naptanTagAdditions: list[StopTagAddition] = Field(default_factory=list)
    # the route master to put this route in, created by this changeset or already in OSM
    routeMaster: RouteMasterChange | None = Field(default=None)
    # route masters to take this route out of
    routeMasterDetach: list[int] = Field(default_factory=list)

    def make_comment(self) -> str:
        if self.comment is not None and (comment := self.comment.strip()):
            return comment

        comment = self._make_route_comment()

        if stop_count := len(self.newStops):
            comment += f'; added {stop_count} bus stop{"s" if stop_count != 1 else ""}'

        if position_count := len(self.newStopPositions):
            comment += f'; added {position_count} stop position{"s" if position_count != 1 else ""}'

        if created := sum(1 for area in self.stopAreas if area.id is None):
            comment += f'; added {created} stop area{"s" if created != 1 else ""}'

        if renamed := sum(1 for area in self.stopAreas if area.id is not None and area.expectedName is not None):
            comment += f'; renamed {renamed} stop area{"s" if renamed != 1 else ""}'

        completed = sum(1 for area in self.stopAreas if area.id is not None and area.expectedName is None)
        if completed:
            comment += f'; completed {completed} stop area{"s" if completed != 1 else ""}'

        # one stop can carry both, and the two are not the same thing to say
        if edited := sum(1 for addition in self.naptanTagAdditions if addition.byHand):
            comment += f'; edited {edited} bus stop{"s" if edited != 1 else ""}'

        if tagged := sum(1 for addition in self.naptanTagAdditions if addition.from_naptan()):
            comment += f'; added NaPTAN tags to {tagged} bus stop{"s" if tagged != 1 else ""}'

        comment += self._make_route_master_comment()

        return comment

    def _make_route_master_comment(self) -> str:
        comment = ''

        if self.routeMaster is not None:
            if self.routeMaster.id is None:
                comment += '; created route master'
            else:
                comment += f'; added to route master #{self.routeMaster.id}'
                # the tags of a master already in OSM are only touched when they changed
                if self.routeMaster.tagsOriginal is not None and normalize_tags(
                    self.routeMaster.tags
                ) != normalize_tags(self.routeMaster.tagsOriginal):
                    comment += ' and edited its tags'

        if detached := len(self.routeMasterDetach):
            comment += f'; removed from {detached} route master{"s" if detached != 1 else ""}'

        return comment

    def make_changeset_tags(self) -> dict[str, str]:
        tags = {
            'comment': self.make_comment(),
            'created_by': CREATED_BY,
            'host': WEBSITE,
        }

        # Credits NaPTAN, as its licence requires, when a stop was made or tagged from it.
        # What the mapper typed themselves is not from NaPTAN and does not credit it.
        if any(addition.from_naptan() for addition in self.naptanTagAdditions) or any(
            'naptan:AtcoCode' in stop.tags for stop in self.newStops
        ):
            tags['source'] = 'NaPTAN'

        return tags

    def _make_route_comment(self) -> str:
        tags_name = self.tags.get('name', '')
        tags_ref = self.tags.get('ref', '')

        # only include ref if it's not already in the name
        if tags_ref and tags_ref in tags_name:
            tags_ref = None

        # there is no id to cite until OSM assigns one
        verb = 'Updated' if self.relationId is not None else 'Created'

        if tags_name and tags_ref:
            described = f'{tags_ref} {tags_name}'
        elif tags_name:
            described = tags_name
        elif tags_ref:
            described = tags_ref
        else:
            described = None

        if self.relationId is None:
            return f'{verb} route: {described}' if described else f'{verb} route'
        if described:
            return f'{verb} route: {described}, #{self.relationId}'
        return f'{verb} route #{self.relationId}'


class PostRouteMasterOnlyModel(BaseModel):
    """Edits to a route master's own tags, made from the list of a line's variants."""

    id: int = Field(gt=0)
    tags: dict[str, str]
    # its tags exactly as the client loaded them, the baseline the edits are diffed against
    tagsOriginal: dict[str, str]
    comment: str | None = Field(default=None, max_length=TAG_MAX_LENGTH)

    def to_change(self) -> RouteMasterChange:
        return RouteMasterChange(id=self.id, tags=self.tags, tagsOriginal=self.tagsOriginal)

    def make_comment(self) -> str:
        if self.comment is not None and (comment := self.comment.strip()):
            return comment

        described = self.tags.get('name', '').strip() or self.tags.get('ref', '').strip()
        if described:
            return f'Updated route master: {described}, #{self.id}'
        return f'Updated route master #{self.id}'

    def make_changeset_tags(self) -> dict[str, str]:
        return {'comment': self.make_comment(), 'created_by': CREATED_BY, 'host': WEBSITE}


@app.post('/download_route_master_change')
async def post_download_route_master_change(model: PostRouteMasterOnlyModel, _=Depends(require_user_details)):
    print(f'💾 Downloading route master change ({model.id})')

    with print_run_time('Building OSM change'):
        osm_change = await build_route_master_only_change(model.to_change(), False, _OSM)

    return Response(content=osm_change, media_type='text/xml; charset=utf-8')


@app.post('/upload_route_master')
async def post_upload_route_master(
    model: PostRouteMasterOnlyModel,
    access_token: str = Depends(require_user_access_token),
):
    print(f'🌐 Uploading route master change ({model.id})')

    with print_run_time('Building OSM change'):
        osm_change = await build_route_master_only_change(model.to_change(), True, _OSM)

    async with OpenStreetMap(access_token=access_token) as osm:
        osm_user = await osm.get_authorized_user()
        upload_result = await osm.upload_osm_change(
            osm_change,
            {'changesets_count': osm_user['changesets']['count'] + 1, **model.make_changeset_tags()},
        )

    if upload_result.ok:
        print(f'✅ Changeset upload success: #{upload_result.changeset_id}')
    else:
        print(f'🚩 Changeset upload failure: {upload_result}')

    return upload_result


@app.post('/download_osm_change')
async def post_download_osm_change(model: PostDownloadOsmChangeModel, _=Depends(require_user_details)):
    print(f'💾 Downloading OSM change ({model.relationId})')

    route = from_dict(
        FinalRoute,
        model.route,
        Config(cast=[ElementId, tuple, PublicTransport, WarningSeverity], strict=True),
    )

    with print_run_time('Building OSM change'):
        osm_change = await build_osm_change(
            model.relationId,
            route,
            include_changeset_id=False,
            overpass=_OVERPASS,
            osm=_OSM,
            tags_original=model.tagsOriginal,
            tags_edited=model.tags,
            new_stops=model.newStops,
            new_stop_positions=model.newStopPositions,
            tag_additions=model.naptanTagAdditions,
            stop_areas=model.stopAreas,
            route_master=model.routeMaster,
            route_master_detach=model.routeMasterDetach,
        )

    return Response(content=osm_change.xml, media_type='text/xml; charset=utf-8')


@app.post('/upload_osm')
async def post_upload_osm(model: PostDownloadOsmChangeModel, access_token: str = Depends(require_user_access_token)):
    print(f'🌐 Uploading OSM change ({model.relationId})')

    route = from_dict(
        FinalRoute,
        model.route,
        Config(cast=[ElementId, tuple, PublicTransport, WarningSeverity], strict=True),
    )

    with print_run_time('Building OSM change'):
        osm_change = await build_osm_change(
            model.relationId,
            route,
            include_changeset_id=True,
            overpass=_OVERPASS,
            osm=_OSM,
            tags_original=model.tagsOriginal,
            tags_edited=model.tags,
            new_stops=model.newStops,
            new_stop_positions=model.newStopPositions,
            tag_additions=model.naptanTagAdditions,
            stop_areas=model.stopAreas,
            route_master=model.routeMaster,
            route_master_detach=model.routeMasterDetach,
        )

    async with OpenStreetMap(access_token=access_token) as osm:
        osm_user = await osm.get_authorized_user()
        user_edits = osm_user['changesets']['count']
        upload_result = await osm.upload_osm_change(
            osm_change.xml,
            {'changesets_count': user_edits + 1, **model.make_changeset_tags()},
            # a route being created is the relation the client is waiting on an id for
            relation_placeholder=None if model.relationId is not None else RelationPlaceholders.ROUTE,
            new_stop_areas=osm_change.new_stop_areas,
        )

    if upload_result.ok:
        created = f', created relation #{upload_result.relation_id}' if upload_result.relation_id else ''
        print(f'✅ Changeset upload success: #{upload_result.changeset_id}{created}')
    else:
        print(f'🚩 Changeset upload failure: {upload_result}')

    return upload_result
