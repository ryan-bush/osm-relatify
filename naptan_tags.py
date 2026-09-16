from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from tag_editing import normalize_tags, validate_tag
from utils import ensure_list

# The NaPTAN tags a stop already in OSM can be given, filled in where it has none or put
# in front of the mapper where it holds a different value - a stop NaPTAN has renamed, or
# one mapped with the wrong name, is exactly the disagreement worth seeing.
# naptan:verified=no is left out, as it would mark a stop someone surveyed as unchecked.
NAPTAN_KEYS = (
    'name',
    'ref',
    'local_ref',
    'naptan:AtcoCode',
    'naptan:NaptanCode',
    'naptan:CommonName',
    'naptan:Indicator',
    'naptan:Street',
    'naptan:Bearing',
    'naptan:BusStopType',
)


# What the mapper may write to a stop by hand, as opposed to what NaPTAN may fill in for
# them. These are the fields the stop form offers, and the mapper is the authority on all
# of them - what the sign says, and what is standing at the stop. Every write is still
# checked against the value the form was showing, so one that changed underneath is a
# conflict rather than a silent overwrite.
EDITABLE_KEYS = ('name', 'local_ref', 'shelter', 'bench')


def missing_tags(osm_tags: dict[str, str], naptan_tags: dict[str, str]) -> dict[str, str]:
    """NaPTAN's value for each key the OSM stop lacks; values it has are kept."""
    return {key: naptan_tags[key] for key in NAPTAN_KEYS if key in naptan_tags and not osm_tags.get(key, '').strip()}


def differing_tags(osm_tags: dict[str, str], naptan_tags: dict[str, str]) -> dict[str, str]:
    """NaPTAN's value for each key where the OSM stop holds a different one."""
    result = {}

    for key in NAPTAN_KEYS:
        naptan_value = naptan_tags.get(key, '').strip()
        osm_value = osm_tags.get(key, '').strip()

        # only a real disagreement: a tag the stop lacks is a fill, not a conflict
        if naptan_value and osm_value and naptan_value != osm_value:
            result[key] = naptan_value

    return result


class StopTagAddition(BaseModel):
    """Tags to write to a stop already in OSM, uploaded with the route."""

    # platforms are nodes or ways; relations are left out so this cannot reach the route
    type: Literal['node', 'way']
    id: int = Field(gt=0)
    tags: dict[str, str]
    # What the stop was believed to hold for each key being overwritten, so a value that
    # has changed since is a conflict rather than being quietly replaced. A key missing
    # from here is one the stop is believed not to have at all.
    expected: dict[str, str] = Field(default_factory=dict)
    # Which of these keys the mapper typed into the stop form, as against being offered
    # by NaPTAN and accepted. The two are allowed to write different things, and one stop
    # can carry both at once, so it is marked per key rather than per stop.
    byHand: set[str] = Field(default_factory=set)  # noqa: N815

    def from_naptan(self) -> bool:
        """Whether any of this came from NaPTAN, rather than being typed by the mapper."""
        return not self.byHand or bool(self.tags.keys() - self.byHand)

    def writable_keys(self) -> set[str]:
        """
        The keys this may write.

        What the mapper typed is theirs to decide, within the fields the form offers.
        What NaPTAN offers is narrower: the NaPTAN keys, either filled in where the
        stop has none or as a replacement the mapper accepted.
        """
        result = set()

        for key in self.tags:
            if key in self.byHand:
                if key in EDITABLE_KEYS:
                    result.add(key)
            elif key in NAPTAN_KEYS:
                result.add(key)

        return result


def apply_stop_tags(element: dict, element_name: str, tags: dict[str, str], expected: dict[str, str]) -> bool:
    """
    Write tags to an element fetched from OSM.

    Each key must still hold what the mapper was shown — nothing for one being filled in,
    the old value for one whose replacement they accepted. Anything else means someone
    edited the stop in the meantime, which is a conflict rather than a silent overwrite.
    Returns True if anything was written.
    """
    # an emptied value is how the form says "take this tag away", which normalize_tags
    # drops, so the removals are picked out before it runs
    removals = {key.strip() for key, value in tags.items() if not value.strip()}
    tags = normalize_tags(tags)
    current = {tag['@k']: tag['@v'] for tag in ensure_list(element.get('tag') or [])}

    writes: dict[str, str] = {}
    conflicts: list[str] = []

    for key, value in tags.items():
        current_value = current.get(key, '')

        # already says what we would write, so there is nothing to do and nothing wrong
        if current_value == value:
            continue

        if current_value != expected.get(key, ''):
            conflicts.append(key)
            continue

        writes[key] = value

    for key in removals:
        current_value = current.get(key, '')

        # already gone, which is where this was trying to get to
        if not current_value:
            continue

        if current_value != expected.get(key, ''):
            conflicts.append(key)

    if conflicts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Conflict: {", ".join(sorted(conflicts))} on {element_name} was changed. '
            'Go back and click the relation reload button.',
        )

    gone = removals & current.keys()
    if not writes and not gone:
        return False

    for key, value in writes.items():
        validate_tag(key, value)

    element['tag'] = [{'@k': k, '@v': v} for k, v in {**current, **writes}.items() if k not in gone]
    return True


async def build_tag_addition_elements(additions: Sequence[StopTagAddition], osm) -> list[tuple[str, dict]]:
    """The elements to modify, as (type, element), with the missing tags added."""
    if not additions:
        return []

    by_type: dict[str, dict[int, StopTagAddition]] = defaultdict(dict)

    for addition in additions:
        name = f'{addition.type}/{addition.id}'

        if addition.id in by_type[addition.type]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f'Tags for {name} were sent twice')

        if disallowed := sorted(addition.tags.keys() - addition.writable_keys()):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f'These tags cannot be written to {name}: {", ".join(disallowed)}',
            )

        by_type[addition.type][addition.id] = addition

    result: list[tuple[str, dict]] = []

    for element_type, type_additions in by_type.items():
        get_elements = osm.get_nodes if element_type == 'node' else osm.get_ways

        # fetched again rather than trusting what was loaded, so a tag someone else set in
        # the meantime is noticed
        for element in await get_elements(tuple(map(str, type_additions)), json=False):
            addition = type_additions[int(element['@id'])]

            if apply_stop_tags(element, f'{element_type}/{addition.id}', addition.tags, addition.expected):
                result.append((element_type, element))

    return result
