"""End-to-end coverage of the tag-editing wiring in build_osm_change()."""

import asyncio

import pytest
import xmltodict

from models.final_route import FinalRoute
from models.relation_member import RelationMember
from relation_builder import build_osm_change

RELATION_ID = 7

RELATION_XML = """<?xml version="1.0"?>
<osm><relation id="7" version="3" changeset="99" timestamp="2020-01-01T00:00:00Z" user="someone" uid="1">
  <member type="way" ref="10" role=""/>
  <tag k="type" v="route"/>
  <tag k="route" v="bus"/>
  <tag k="public_transport:version" v="2"/>
  <tag k="name" v="Bus 12"/>
  <tag k="operator" v="Acme"/>
</relation></osm>"""

ORIGINAL_TAGS = {
    'type': 'route',
    'route': 'bus',
    'public_transport:version': '2',
    'name': 'Bus 12',
    'operator': 'Acme',
}


class FakeOpenStreetMap:
    """Stands in for OpenStreetMap; only get_relation is reached when no ways are split."""

    def __init__(self, xml: str = RELATION_XML):
        self._xml = xml

    async def get_relation(self, relation_id, *, json: bool = True) -> dict:
        assert relation_id == RELATION_ID
        assert not json, 'build_osm_change must request XML'
        return xmltodict.parse(self._xml)['osm']['relation']


class UnusedOverpass:
    """build_osm_change must not touch Overpass when nothing is split."""

    def __getattr__(self, name):
        raise AssertionError(f'Overpass.{name} should not be called')


@pytest.fixture
def route() -> FinalRoute:
    return FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=dict(ORIGINAL_TAGS),
        extraWaysToUpdate=(),
        members=(RelationMember(id='10', type='way', role=''),),
        warnings=(),
    )


def build(route: FinalRoute, **kwargs) -> dict:
    osm_change = asyncio.run(
        build_osm_change(
            RELATION_ID,
            route,
            include_changeset_id=False,
            overpass=UnusedOverpass(),
            osm=FakeOpenStreetMap(),
            **kwargs,
        )
    )
    return xmltodict.parse(osm_change)['osmChange']['modify']['relation']


def tags_of(relation: dict) -> dict[str, str]:
    tag = relation.get('tag') or []
    if isinstance(tag, dict):
        tag = [tag]
    return {t['@k']: t['@v'] for t in tag}


def test_tags_are_untouched_without_edits(route):
    """Omitting the tag arguments must reproduce the pre-feature behaviour exactly."""
    relation = build(route)
    assert tags_of(relation) == ORIGINAL_TAGS


def test_tags_are_untouched_when_original_is_missing(route):
    """An older client sends tags but no tagsOriginal; that must not be read as a change."""
    relation = build(route, tags_edited={'name': 'Something Else'})
    assert tags_of(relation) == ORIGINAL_TAGS


def test_edited_tag_reaches_the_osm_change(route):
    relation = build(
        route,
        tags_original=ORIGINAL_TAGS,
        tags_edited={**ORIGINAL_TAGS, 'operator': 'Beta', 'colour': '#FF0000'},
    )

    assert tags_of(relation)['operator'] == 'Beta'
    assert tags_of(relation)['colour'] == '#FF0000'
    assert tags_of(relation)['name'] == 'Bus 12'


def test_members_are_still_rewritten_alongside_tags(route):
    """Tag application must not disturb the member rewrite it sits next to."""
    relation = build(
        route,
        tags_original=ORIGINAL_TAGS,
        tags_edited={**ORIGINAL_TAGS, 'operator': 'Beta'},
    )

    assert relation['member'] == {'@type': 'way', '@ref': '10', '@role': ''}


def test_metadata_is_still_stripped(route):
    relation = build(route, tags_original=ORIGINAL_TAGS, tags_edited=dict(ORIGINAL_TAGS))

    assert '@timestamp' not in relation
    assert '@user' not in relation
    assert '@uid' not in relation
    assert '@changeset' not in relation
    assert relation['@version'] == '3', 'version must be preserved for optimistic locking'
