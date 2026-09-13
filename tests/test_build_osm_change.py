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
        # the route has gained a way since, so the relation really does change; a route
        # identical to what OSM holds is its own case, covered below
        members=(RelationMember(id='10', type='way', role=''), RelationMember(id='11', type='way', role='')),
        warnings=(),
    )


@pytest.fixture
def unchanged_route() -> FinalRoute:
    """A route exactly as OSM already has it, members and all."""
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


def build_change(route: FinalRoute, relation_id=RELATION_ID, **kwargs) -> dict:
    """The whole osmChange, for checking whether the relation is in it at all."""
    osm_change = asyncio.run(
        build_osm_change(
            relation_id,
            route,
            include_changeset_id=False,
            overpass=UnusedOverpass(),
            osm=FakeOpenStreetMap(),
            **kwargs,
        )
    )
    return xmltodict.parse(osm_change)['osmChange']


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

    assert relation['member'] == [
        {'@type': 'way', '@ref': '10', '@role': ''},
        {'@type': 'way', '@ref': '11', '@role': ''},
    ]


def test_metadata_is_still_stripped(route):
    relation = build(route, tags_original=ORIGINAL_TAGS, tags_edited=dict(ORIGINAL_TAGS))

    assert '@timestamp' not in relation
    assert '@user' not in relation
    assert '@uid' not in relation
    assert '@changeset' not in relation
    assert relation['@version'] == '3', 'version must be preserved for optimistic locking'


def test_an_unchanged_relation_is_left_out(unchanged_route):
    """Uploading it would give the route a new version saying nothing at all."""
    change = build_change(unchanged_route, tags_original=ORIGINAL_TAGS, tags_edited=dict(ORIGINAL_TAGS))

    assert not change['modify'], 'nothing was changed, so nothing is uploaded'


def test_an_unchanged_relation_is_left_out_without_tag_edits(unchanged_route):
    assert not build_change(unchanged_route)['modify']


def test_a_relation_whose_tags_changed_is_sent(unchanged_route):
    change = build_change(
        unchanged_route,
        tags_original=ORIGINAL_TAGS,
        tags_edited={**ORIGINAL_TAGS, 'operator': 'Beta'},
    )

    assert change['modify']['relation']['@id'] == '7'


def test_a_relation_whose_members_changed_is_sent(route):
    # the route fixture has gained way 11
    assert build_change(route)['modify']['relation']['@id'] == '7'


def test_a_reordered_route_is_sent():
    """Same members, different order, is a real change to the relation."""
    reordered = FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=dict(ORIGINAL_TAGS),
        extraWaysToUpdate=(),
        members=(
            RelationMember(id='11', type='way', role=''),
            RelationMember(id='10', type='way', role=''),
        ),
        warnings=(),
    )

    assert build_change(reordered)['modify']['relation']['@id'] == '7'


def test_a_changed_role_is_sent():
    rerolled = FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=dict(ORIGINAL_TAGS),
        extraWaysToUpdate=(),
        members=(RelationMember(id='10', type='way', role='forward'),),
        warnings=(),
    )

    assert build_change(rerolled)['modify']['relation']['@id'] == '7'


def test_a_relation_being_created_is_always_sent():
    """There is nothing on the server to compare it with."""
    new_route = FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags=dict(ORIGINAL_TAGS),
        extraWaysToUpdate=(),
        members=(RelationMember(id='10', type='way', role=''),),
        warnings=(),
    )
    change = build_change(new_route, relation_id=None, tags_edited=dict(ORIGINAL_TAGS))

    assert change['create']['relation']['@id'] == '-1'
