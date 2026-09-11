from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from tag_editing import normalize_tags, validate_tag
from utils import ensure_list

# The NaPTAN tags a stop already in OSM can be given. name stays the mapper's to decide,
# and naptan:verified=no would mark a stop someone surveyed as unchecked.
FILLABLE_KEYS = (
    'ref',
    'local_ref',
    'naptan:AtcoCode',
    'naptan:NaptanCode',
    'naptan:CommonName',
    'naptan:Indicator',
    'naptan:Street',
    'naptan:Bearing',
)


def missing_tags(osm_tags: dict[str, str], naptan_tags: dict[str, str]) -> dict[str, str]:
    """NaPTAN's value for each fillable key the OSM stop lacks; values it has are kept."""
    return {key: naptan_tags[key] for key in FILLABLE_KEYS if key in naptan_tags and not osm_tags.get(key, '').strip()}


class StopTagAddition(BaseModel):
    """NaPTAN tags to add to a stop already in OSM, uploaded with the route."""

    # platforms are nodes or ways; relations are left out so this cannot reach the route
    type: Literal['node', 'way']
    id: int = Field(gt=0)
    tags: dict[str, str]


def add_missing_tags(element: dict, element_name: str, tags: dict[str, str]) -> bool:
    """
    Add the tags an element fetched from OSM still lacks.

    A key that has since been given a different value is a conflict, rather than being
    overwritten. Returns True if any tag was added.
    """
    tags = normalize_tags(tags)
    current = {tag['@k']: tag['@v'] for tag in ensure_list(element.get('tag') or [])}

    changed = sorted(key for key, value in tags.items() if key in current and current[key] != value)
    if changed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Conflict: {", ".join(changed)} on {element_name} was changed. '
            'Go back and click the relation reload button.',
        )

    added = {key: value for key, value in tags.items() if key not in current}
    if not added:
        return False

    for key, value in added.items():
        validate_tag(key, value)

    element['tag'] = [{'@k': k, '@v': v} for k, v in {**current, **added}.items()]
    return True


async def build_tag_addition_elements(additions: Sequence[StopTagAddition], osm) -> list[tuple[str, dict]]:
    """The elements to modify, as (type, element), with the missing tags added."""
    if not additions:
        return []

    by_type: dict[str, dict[int, StopTagAddition]] = defaultdict(dict)

    for addition in additions:
        name = f'{addition.type}/{addition.id}'

        if addition.id in by_type[addition.type]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f'NaPTAN tags for {name} were sent twice')

        if disallowed := sorted(addition.tags.keys() - set(FILLABLE_KEYS)):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f'These tags cannot be added to {name}: {", ".join(disallowed)}',
            )

        by_type[addition.type][addition.id] = addition

    result: list[tuple[str, dict]] = []

    for element_type, type_additions in by_type.items():
        get_elements = osm.get_nodes if element_type == 'node' else osm.get_ways

        # fetched again rather than trusting what was loaded, so a tag someone else set in
        # the meantime is noticed
        for element in await get_elements(tuple(map(str, type_additions)), json=False):
            addition = type_additions[int(element['@id'])]

            if add_missing_tags(element, f'{element_type}/{addition.id}', addition.tags):
                result.append((element_type, element))

    return result
