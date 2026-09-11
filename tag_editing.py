import re

from fastapi import HTTPException, status

from config import PROTECTED_TAG_KEYS, TAG_MAX_LENGTH
from utils import ensure_list

# rejected by the OSM API; xmltodict would happily serialize them
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def normalize_tags(tags: dict[str, str]) -> dict[str, str]:
    """
    Strip surrounding whitespace and drop empty entries.

    An emptied-out value is how the editor expresses "delete this tag", so it must not
    survive normalization as a present-but-blank tag.
    """
    result = {}

    for key, value in tags.items():
        key = key.strip()
        value = value.strip()
        if key and value:
            result[key] = value

    return result


def validate_tag(key: str, value: str) -> None:
    # the limit is in characters, not bytes
    if len(key) > TAG_MAX_LENGTH or len(value) > TAG_MAX_LENGTH:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'Tag {key!r} exceeds {TAG_MAX_LENGTH} characters')

    if _CONTROL_CHARS_RE.search(key) or _CONTROL_CHARS_RE.search(value):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'Tag {key!r} contains control characters')


def apply_tag_changes(relation_data: dict, original: dict[str, str], edited: dict[str, str]) -> bool:
    """
    Merge the user's tag edits into a freshly fetched relation.

    Only the keys the user actually changed are touched; every other tag keeps the value
    currently on the server, so concurrent edits to unrelated tags are not clobbered.

    Returns True if any tag was modified.
    """
    original = normalize_tags(original)
    edited = normalize_tags(edited)

    changed_keys = {k for k, v in edited.items() if original.get(k) != v}
    removed_keys = original.keys() - edited.keys()
    if not changed_keys and not removed_keys:
        return False

    protected = sorted((changed_keys | removed_keys) & PROTECTED_TAG_KEYS)
    if protected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'These tags cannot be edited here: {", ".join(protected)}')

    for key in changed_keys:
        validate_tag(key, edited[key])

    current = {tag['@k']: tag['@v'] for tag in ensure_list(relation_data.get('tag') or [])}

    # optimistic concurrency check, scoped to the keys actually being written
    stale = sorted(k for k in (changed_keys | removed_keys) if current.get(k) != original.get(k))
    if stale:
        plural = len(stale) > 1
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Conflict: Tag{"s" if plural else ""} {", ".join(stale)} '
            f'{"were" if plural else "was"} modified. '
            'Go back and click the relation reload button.',
        )

    merged = {k: v for k, v in current.items() if k not in removed_keys}
    merged.update({k: edited[k] for k in changed_keys})

    if merged:
        relation_data['tag'] = [{'@k': k, '@v': v} for k, v in merged.items()]
    else:
        relation_data.pop('tag', None)

    return True
