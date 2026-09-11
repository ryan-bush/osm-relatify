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

from compression import deflate_compress, deflate_decompress
from config import (
    CALC_ROUTE_MAX_PROCESSES,
    CALC_ROUTE_N_PROCESSES,
    CREATED_BY,
    OSM_CLIENT,
    OSM_SCOPES,
    OSM_SECRET,
    TAG_MAX_LENGTH,
    TEST_ENV,
    WEBSITE,
)
from cython_lib.route import calc_bus_route
from deflate_middleware import DeflateRoute
from models.bounding_box import BoundingBox
from models.download_history import Cell, DownloadHistory
from models.element_id import ElementId
from models.fetch_relation import (
    FetchRelation,
    FetchRelationBusStopCollection,
    FetchRelationElement,
    PublicTransport,
    assign_none_members,
    find_start_stop_ways,
)
from models.final_route import FinalRoute, WarningSeverity
from openstreetmap import OpenStreetMap
from overpass import Overpass
from relation_builder import build_osm_change, get_relation_members, sort_and_upgrade_members
from route_warnings import check_for_issues
from user_session import fetch_user_details, require_user_access_token, require_user_details
from utils import HTTP, print_run_time

_SESSION_MAX_AGE = 31536000  # 1 year
_TEMPLATES = Jinja2Templates(directory='templates', auto_reload=TEST_ENV)

_PROCESS_EXECUTOR = ProcessPoolExecutor(CALC_ROUTE_MAX_PROCESSES)
_OSM = OpenStreetMap()
_OVERPASS = Overpass()


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with _OSM:
        yield


app = FastAPI(
    debug=True,
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)
app.router.route_class = DeflateRoute
app.mount('/static', StaticFiles(directory='static', html=True), name='static')


@app.get('/')
async def index(request: Request, user=Depends(fetch_user_details)):
    if user is not None:
        return _TEMPLATES.TemplateResponse('authorized.jinja2', {'request': request, 'user': user})
    else:
        return _TEMPLATES.TemplateResponse('index.jinja2', {'request': request})


@app.post('/login')
async def login(request: Request):
    state = os.urandom(32).hex()
    authorization_url = 'https://www.openstreetmap.org/oauth2/authorize?' + urlencode({
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
        'https://www.openstreetmap.org/oauth2/token',
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


def get_route_type(tags: dict[str, str]) -> str | None:
    if tags.get('public_transport:version') != '2':
        return None
    type = tags.get('type')
    if type not in {'route', 'disused:route', 'was:route'}:
        return None
    type_specifier = tags.get(type)
    if type_specifier == 'trolleybus':
        return 'bus'
    if type_specifier not in {'bus', 'tram'}:
        return None
    return type_specifier


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
    )


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
                                timeout=6,
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

                final_route = check_for_issues(
                    route=route,
                    ways=ways_members,
                    start_way=model.startWay,
                    end_way=model.stopWay,
                    bus_stop_collections=model.busStops,
                    relation_members=relation_members,
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

    def make_comment(self) -> str:
        if self.comment is not None and (comment := self.comment.strip()):
            return comment

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
        )

    return Response(content=osm_change, media_type='text/xml; charset=utf-8')


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
        )

    async with OpenStreetMap(access_token=access_token) as osm:
        osm_user = await osm.get_authorized_user()
        user_edits = osm_user['changesets']['count']
        upload_result = await osm.upload_osm_change(
            osm_change,
            {
                'changesets_count': user_edits + 1,
                'comment': model.make_comment(),
                'created_by': CREATED_BY,
                'host': WEBSITE,
            },
        )

    if upload_result.ok:
        created = f', created relation #{upload_result.relation_id}' if upload_result.relation_id else ''
        print(f'✅ Changeset upload success: #{upload_result.changeset_id}{created}')
    else:
        print(f'🚩 Changeset upload failure: {upload_result}')

    return upload_result
