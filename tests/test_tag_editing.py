import pytest
import xmltodict
from fastapi import HTTPException, status

from tag_editing import apply_tag_changes, normalize_tags

RELATION_XML = """<?xml version="1.0"?>
<osm><relation id="1" version="7">
  <member type="way" ref="10" role=""/>
  <tag k="type" v="route"/>
  <tag k="route" v="bus"/>
  <tag k="public_transport:version" v="2"/>
  <tag k="name" v="Bus 12: A =&gt; B"/>
  <tag k="ref" v="12"/>
  <tag k="operator" v="Acme"/>
  <tag k="note" v="keep me"/>
</relation></osm>"""

ORIGINAL = {
    'type': 'route',
    'route': 'bus',
    'public_transport:version': '2',
    'name': 'Bus 12: A => B',
    'ref': '12',
    'operator': 'Acme',
    'note': 'keep me',
}


@pytest.fixture
def relation() -> dict:
    """A relation in the exact shape osm.get_relation(json=False) returns."""
    return xmltodict.parse(RELATION_XML)['osm']['relation']


def tags_of(relation: dict) -> dict[str, str]:
    tag = relation.get('tag') or []
    if isinstance(tag, dict):
        tag = [tag]
    return {t['@k']: t['@v'] for t in tag}


def test_no_changes_is_a_noop(relation):
    before = tags_of(relation)
    assert apply_tag_changes(relation, ORIGINAL, dict(ORIGINAL)) is False
    assert tags_of(relation) == before


def test_changed_value_is_written(relation):
    assert apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'operator': 'Beta'}) is True
    assert tags_of(relation)['operator'] == 'Beta'
    assert tags_of(relation)['note'] == 'keep me'


def test_new_tag_is_added(relation):
    apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'colour': '#FF0000'})
    assert tags_of(relation)['colour'] == '#FF0000'


def test_omitted_tag_is_removed(relation):
    edited = {k: v for k, v in ORIGINAL.items() if k != 'note'}
    apply_tag_changes(relation, ORIGINAL, edited)
    assert 'note' not in tags_of(relation)
    assert tags_of(relation)['operator'] == 'Acme'


def test_emptied_value_removes_the_tag(relation):
    """The editor expresses deletion by blanking the input; normalization must not write a blank tag."""
    apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'note': '   '})
    assert 'note' not in tags_of(relation)


def test_concurrent_edit_to_an_untouched_tag_is_preserved(relation):
    """The whole point of diffing: tags nobody touched keep whatever the server currently has."""
    relation['tag'].append({'@k': 'colour', '@v': '#00FF00'})

    apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'operator': 'Beta'})

    assert tags_of(relation)['colour'] == '#00FF00'
    assert tags_of(relation)['operator'] == 'Beta'


@pytest.mark.parametrize('key', ['type', 'route', 'public_transport:version'])
def test_protected_tags_cannot_be_changed(relation, key):
    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, key: 'anything'})

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert key in exc_info.value.detail


def test_protected_tags_cannot_be_removed(relation):
    edited = {k: v for k, v in ORIGINAL.items() if k != 'public_transport:version'}

    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, ORIGINAL, edited)

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_conflict_when_the_same_tag_moved_underneath_us(relation):
    stale = {**ORIGINAL, 'operator': 'Gamma'}  # what the client loaded, now out of date

    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, stale, {**stale, 'operator': 'Beta'})

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert 'operator was modified' in exc_info.value.detail


def test_conflict_message_pluralizes(relation):
    stale = {**ORIGINAL, 'operator': 'Gamma', 'ref': '99'}

    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, stale, {**stale, 'operator': 'Beta', 'ref': '98'})

    assert 'operator, ref were modified' in exc_info.value.detail


def test_conflict_when_someone_else_added_the_tag_we_are_adding(relation):
    original_without_note = {k: v for k, v in ORIGINAL.items() if k != 'note'}

    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, original_without_note, {**original_without_note, 'note': 'mine'})

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT


def test_conflict_ignores_tags_we_did_not_touch(relation):
    """A concurrent edit only conflicts if it lands on a key the user is writing."""
    stale = {**ORIGINAL, 'name': 'something else entirely'}

    apply_tag_changes(relation, stale, {**stale, 'operator': 'Beta'})

    assert tags_of(relation)['name'] == 'Bus 12: A => B'


def test_single_tag_collapses_to_a_dict():
    relation = xmltodict.parse('<osm><relation id="1"><tag k="name" v="X"/></relation></osm>')['osm']['relation']
    assert isinstance(relation['tag'], dict), 'precondition: xmltodict does not wrap a lone child in a list'

    apply_tag_changes(relation, {'name': 'X'}, {'name': 'Y'})

    assert tags_of(relation) == {'name': 'Y'}


def test_relation_without_any_tags():
    relation = xmltodict.parse('<osm><relation id="1"/></osm>')['osm']['relation']

    apply_tag_changes(relation, {}, {'name': 'X'})

    assert tags_of(relation) == {'name': 'X'}


def test_removing_every_tag_drops_the_key():
    relation = xmltodict.parse('<osm><relation id="1"><tag k="name" v="X"/></relation></osm>')['osm']['relation']

    apply_tag_changes(relation, {'name': 'X'}, {})

    assert 'tag' not in relation


def test_value_at_the_length_limit_is_accepted(relation):
    apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'note': 'x' * 255})
    assert tags_of(relation)['note'] == 'x' * 255


def test_value_over_the_length_limit_is_rejected(relation):
    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'note': 'x' * 256})

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_control_characters_are_rejected(relation):
    with pytest.raises(HTTPException) as exc_info:
        apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'note': 'bad\x07value'})

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST


def test_newlines_are_allowed(relation):
    """Multiline note=* is legal and common in OSM; only true control characters are rejected."""
    apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'note': 'line1\nline2'})
    assert tags_of(relation)['note'] == 'line1\nline2'


def test_survives_an_xml_round_trip(relation):
    apply_tag_changes(relation, ORIGINAL, {**ORIGINAL, 'name': 'Bus 12: A <=> B & C'})

    reparsed = xmltodict.parse(xmltodict.unparse({'osm': {'relation': relation}}))['osm']['relation']

    assert tags_of(reparsed)['name'] == 'Bus 12: A <=> B & C'
    assert reparsed['member']['@ref'] == '10', 'members must be left untouched'


def test_normalize_tags_strips_and_drops_blanks():
    assert normalize_tags({'  ': 'v', ' name ': ' X ', 'note': ''}) == {'name': 'X'}
