from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import xmltodict
from asyncache import cached
from cachetools import TTLCache

from config import CHANGESET_ID_PLACEHOLDER, OSM_API_URL, TAG_MAX_LENGTH
from models.stop_area import StopArea
from utils import ensure_list, get_http_client

if TYPE_CHECKING:
    # imported for typing only: stop_areas reaches OSM through this module
    from stop_areas import NewStopAreaPlan


# the element types a changeset creates, as the diffResult names them
_CREATED_TYPES = ('node', 'way', 'relation')


def _parse_created_ids(diff_result: str) -> dict[str, dict[int, int]]:
    """
    The real ids OSM assigned to the elements a changeset created, by placeholder.

    A changeset can create several relations at once — the route, the stop areas its stops
    are grouped into, the route master it joins — and the diffResult says which is which
    only by the placeholder each went up with. Reading the first created relation and
    calling it the route was wrong as soon as anything else was created alongside it: the
    stop areas are written before the route, so the route's id was whichever of them came
    first.
    """
    try:
        parsed = xmltodict.parse(diff_result, force_list=_CREATED_TYPES)
    except Exception:
        print('🚧 Warning: Could not parse the upload diffResult')
        return {}

    result: dict[str, dict[int, int]] = {}

    for element_type in _CREATED_TYPES:
        created: dict[int, int] = {}

        for element in parsed.get('diffResult', {}).get(element_type) or ():
            old_id = element.get('@old_id')
            new_id = element.get('@new_id')

            # a created element is the only one whose id changes
            if old_id is None or new_id is None or int(old_id) >= 0:
                continue

            created[int(old_id)] = int(new_id)

        if created:
            result[element_type] = created

    return result


def _resolve_new_stop_areas(
    plans: Sequence['NewStopAreaPlan'],
    created_ids: dict[str, dict[int, int]],
) -> list[StopArea]:
    """
    The stop areas a change created, as the relations they now are.

    A member may be a stop the same change created, whose placeholder is no more a name
    for it than the relation's own was; one that cannot be resolved is left out rather
    than named by a placeholder the client would take for an element id.
    """
    result = []

    for plan in plans:
        relation_id = created_ids.get('relation', {}).get(plan.placeholder_id)
        if relation_id is None:
            print(f'🚧 Warning: The upload did not say what id stop area {plan.placeholder_id} was given')
            continue

        members = []

        for member in plan.members:
            member_id = member.id if member.id > 0 else created_ids.get(member.type, {}).get(member.id)
            if member_id is not None:
                members.append(f'{member.type}/{member_id}')

        result.append(StopArea(id=relation_id, name=plan.name, members=members))

    return result


@dataclass(frozen=True, kw_only=True, slots=True)
class UploadResult:
    ok: bool
    error_code: int | None
    error_message: str | None
    changeset_id: int | None
    # the id OSM assigned to a newly created relation, if the change created one
    relation_id: int | None = None
    # The stop areas the change created, with the ids OSM has now given them. The client
    # learns which stop areas exist from Overpass, which runs minutes behind, so without
    # this the next route of the same line is offered a second relation for stops this
    # change has only just grouped.
    new_stop_areas: list[StopArea] = field(default_factory=list)


class OpenStreetMap:
    def __init__(self, *, access_token: str | None = None):
        self._http = get_http_client(
            f'{OSM_API_URL}/api',
            headers={'Authorization': f'Bearer {access_token}'} if access_token else None,
        )

    async def __aenter__(self) -> 'OpenStreetMap':
        await self._http.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._http.__aexit__(exc_type, exc_val, exc_tb)

    async def get_changeset_maxsize(self) -> int:
        r = await self._http.get('/capabilities')
        r.raise_for_status()
        caps = xmltodict.parse(r.text)
        return int(caps['osm']['api']['changesets']['@maximum_elements'])

    async def get_relation(self, relation_id: str | int, *, json: bool = True) -> dict:
        return (await self._get_elements('relations', (relation_id,), json=json))[0]

    async def get_way(self, way_id: str | int, *, json: bool = True) -> dict:
        return (await self._get_elements('ways', (way_id,), json=json))[0]

    async def get_node(self, node_id: str | int, *, json: bool = True) -> dict:
        return (await self._get_elements('nodes', (node_id,), json=json))[0]

    async def get_relations(self, relation_ids: Iterable[str | int], *, json: bool = True) -> list[dict]:
        return await self._get_elements('relations', relation_ids, json=json)

    async def get_ways(self, way_ids: Iterable[str | int], *, json: bool = True) -> list[dict]:
        return await self._get_elements('ways', way_ids, json=json)

    async def get_nodes(self, node_ids: Iterable[str | int], *, json: bool = True) -> list[dict]:
        return await self._get_elements('nodes', node_ids, json=json)

    @cached(TTLCache(maxsize=1024, ttl=60))
    async def _get_elements(
        self,
        elements_type: Literal['nodes', 'ways', 'relations'],
        element_ids: Iterable[str | int],
        json: bool,
    ) -> list[dict]:
        r = await self._http.get(
            f'/0.6/{elements_type}{".json" if json else ""}',
            params={elements_type: ','.join(map(str, element_ids))},
        )
        r.raise_for_status()
        if json:
            return r.json()['elements']
        else:
            return ensure_list(xmltodict.parse(r.text)['osm'][elements_type[:-1]])

    async def get_parent_relations(
        self, element_type: Literal['node', 'way', 'relation'], element_id: int
    ) -> list[dict]:
        """The relations this element belongs to, straight from OSM rather than Overpass."""
        r = await self._http.get(f'/0.6/{element_type}/{element_id}/relations.json')
        r.raise_for_status()
        return r.json()['elements']

    async def get_authorized_user(self) -> dict:
        r = await self._http.get('/0.6/user/details.json')
        r.raise_for_status()
        return r.json()['user']

    async def upload_osm_change(
        self,
        osm_change: str,
        tags: dict[str, str],
        *,
        relation_placeholder: int | None = None,
        new_stop_areas: Sequence['NewStopAreaPlan'] = (),
    ) -> UploadResult:
        """
        Uploads a change, and reads back the ids OSM gave whatever it created.

        `relation_placeholder` is the id the relation being created carried in the change,
        and `new_stop_areas` the stop areas it creates; both are named by placeholder
        because that is all the diffResult has to go on.
        """
        assert 'comment' in tags, 'You must provide a comment'

        for key, value in tuple(tags.items()):
            # remove empty tags
            if not value:
                del tags[key]
                continue

            # stringify the value
            if not isinstance(value, str):
                value = str(value)
                tags[key] = value

            # trim value if too long
            if len(value) > TAG_MAX_LENGTH:
                print(f'🚧 Warning: Trimming {key} value because it exceeds {TAG_MAX_LENGTH} characters: {value}')
                tags[key] = value[: TAG_MAX_LENGTH - 1] + '…'

        changeset_dict = {'osm': {'changeset': {'tag': [{'@k': k, '@v': v} for k, v in tags.items()]}}}
        changeset = xmltodict.unparse(changeset_dict)

        r = await self._http.put(
            '/0.6/changeset/create',
            content=changeset,
            headers={'Content-Type': 'text/xml; charset=utf-8'},
            follow_redirects=False,
        )
        r.raise_for_status()
        changeset_id_raw = r.text
        changeset_id = int(changeset_id_raw)

        osm_change = osm_change.replace(CHANGESET_ID_PLACEHOLDER, changeset_id_raw)
        upload_resp = await self._http.post(
            f'/0.6/changeset/{changeset_id_raw}/upload',
            content=osm_change,
            headers={'Content-Type': 'text/xml; charset=utf-8'},
            timeout=150,
        )

        r = await self._http.put(f'/0.6/changeset/{changeset_id_raw}/close')
        r.raise_for_status()

        if not upload_resp.is_success:
            return UploadResult(
                ok=False,
                error_code=upload_resp.status_code,
                error_message=upload_resp.text,
                changeset_id=changeset_id,
            )

        created_ids = _parse_created_ids(upload_resp.text)

        return UploadResult(
            ok=True,
            error_code=None,
            error_message=None,
            changeset_id=changeset_id,
            relation_id=(
                None if relation_placeholder is None else created_ids.get('relation', {}).get(relation_placeholder)
            ),
            new_stop_areas=_resolve_new_stop_areas(new_stop_areas, created_ids),
        )
