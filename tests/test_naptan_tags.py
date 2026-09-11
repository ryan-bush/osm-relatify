import asyncio

import pytest
import xmltodict
from fastapi import HTTPException
from pydantic import ValidationError

from main import PostDownloadOsmChangeModel
from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStop, FetchRelationBusStopCollection, PublicTransport
from models.final_route import FinalRoute
from models.naptan_stop import NaptanStop
from models.relation_member import RelationMember
from naptan import match_stops
from naptan_tags import StopTagAddition, add_missing_tags, build_tag_addition_elements, missing_tags
from relation_builder import build_osm_change

NAPTAN_TAGS = {
    'name': 'Union Grove',
    'ref': '23234375',
    'local_ref': 'A',
    'naptan:AtcoCode': '639000011',
    'naptan:NaptanCode': '23234375',
    'naptan:CommonName': 'Union Grove',
    'naptan:Indicator': 'Stop A',
    'naptan:Street': 'Union Grove',
    'naptan:Bearing': 'SW',
    'naptan:verified': 'no',
}


def test_only_missing_fillable_tags_are_offered():
    osm_tags = {'name': 'Union Grove Road', 'ref': '999', 'naptan:Street': ' '}

    assert missing_tags(osm_tags, NAPTAN_TAGS) == {
        'local_ref': 'A',
        'naptan:AtcoCode': '639000011',
        'naptan:NaptanCode': '23234375',
        'naptan:CommonName': 'Union Grove',
        'naptan:Indicator': 'Stop A',
        'naptan:Street': 'Union Grove',
        'naptan:Bearing': 'SW',
    }


def test_name_and_verified_are_never_offered():
    offered = missing_tags({}, NAPTAN_TAGS)

    assert 'name' not in offered
    assert 'naptan:verified' not in offered


# about 25 m apart, either side of a road
NORTH_SIDE = (57.14120, -2.11750)
SOUTH_SIDE = (57.14098, -2.11750)


def _naptan(code, lat_lng, local_ref=None):
    tags = {**NAPTAN_TAGS, 'naptan:AtcoCode': code}
    if local_ref:
        tags['local_ref'] = local_ref
    else:
        tags.pop('local_ref')
    return NaptanStop(atcoCode=code, name='Union Grove', indicator='', latLng=lat_lng, tags=tags)


def _osm_stop(id, lat_lng, tags, public_transport=PublicTransport.PLATFORM):
    return FetchRelationBusStop(
        id=ElementId(id),
        type='node',
        member=True,
        latLng=lat_lng,
        tags={'name': 'Union Grove', **tags},
        name='Union Grove',
        groupName='',
        highway='bus_stop',
        public_transport=public_transport,
    )


def _platform(id, lat_lng, tags):
    return FetchRelationBusStopCollection(platform=_osm_stop(id, lat_lng, tags), stop=None)


def _suggestions(naptan_stops, collections):
    return {s.id: (s.atcoCode, s.tags) for s in match_stops(naptan_stops, collections).tag_suggestions}


def test_a_stop_matched_by_code_is_offered_what_it_lacks():
    suggestions = _suggestions(
        [_naptan('A', NORTH_SIDE)],
        [_platform('1', NORTH_SIDE, {'naptan:AtcoCode': 'A', 'naptan:Bearing': 'SW'})],
    )

    atco_code, tags = suggestions['1']
    assert atco_code == 'A'
    assert 'naptan:AtcoCode' not in tags
    assert 'naptan:Bearing' not in tags
    assert tags['naptan:NaptanCode'] == '23234375'


def test_a_fully_tagged_stop_gets_no_suggestion():
    complete = {key: value for key, value in NAPTAN_TAGS.items() if key != 'local_ref'} | {'naptan:AtcoCode': 'A'}

    assert _suggestions([_naptan('A', NORTH_SIDE)], [_platform('1', NORTH_SIDE, complete)]) == {}


def test_an_unambiguous_name_match_is_offered_the_code():
    suggestions = _suggestions([_naptan('A', NORTH_SIDE)], [_platform('1', (57.14125, -2.11750), {})])

    assert suggestions['1'][1]['naptan:AtcoCode'] == 'A'


def test_twins_across_the_road_without_letters_get_no_codes():
    suggestions = _suggestions(
        [_naptan('A', NORTH_SIDE), _naptan('B', SOUTH_SIDE)],
        [_platform('1', NORTH_SIDE, {}), _platform('2', SOUTH_SIDE, {})],
    )

    assert suggestions == {}


def test_twins_told_apart_by_their_letters_get_their_own_codes():
    suggestions = _suggestions(
        [_naptan('A', NORTH_SIDE, 'A'), _naptan('B', SOUTH_SIDE, 'B')],
        [_platform('1', SOUTH_SIDE, {'local_ref': 'A'}), _platform('2', NORTH_SIDE, {'local_ref': 'B'})],
    )

    assert suggestions['1'][0] == 'A'
    assert suggestions['2'][0] == 'B'


def test_a_stop_with_an_old_code_is_not_given_a_new_one():
    suggestions = _suggestions(
        [_naptan('NEW', NORTH_SIDE, 'A')], [_platform('1', NORTH_SIDE, {'naptan:AtcoCode': 'OLD', 'local_ref': 'A'})]
    )

    assert suggestions == {}


def test_a_stop_carrying_two_codes_is_left_alone():
    suggestions = _suggestions(
        [_naptan('A', NORTH_SIDE), _naptan('B', SOUTH_SIDE)],
        [_platform('1', NORTH_SIDE, {'naptan:AtcoCode': 'A;B'})],
    )

    assert suggestions == {}


def test_a_lone_stop_position_is_not_tagged():
    collection = FetchRelationBusStopCollection(
        platform=None,
        stop=_osm_stop('1', NORTH_SIDE, {'naptan:AtcoCode': 'A'}, PublicTransport.STOP_POSITION),
    )

    assert _suggestions([_naptan('A', NORTH_SIDE)], [collection]) == {}


def _element(tags, id='42'):
    return {
        '@id': id,
        '@version': '3',
        '@lat': '57.1',
        '@lon': '-2.1',
        'tag': [{'@k': k, '@v': v} for k, v in tags.items()],
    }


def _tags_of(element):
    tag = element['tag']
    return {t['@k']: t['@v'] for t in (tag if isinstance(tag, list) else [tag])}


def test_missing_tags_are_added_and_existing_ones_kept():
    element = _element({'highway': 'bus_stop', 'name': 'Union Grove'})

    assert add_missing_tags(element, 'node/42', {'naptan:AtcoCode': 'A', 'naptan:Bearing': 'SW'})
    assert _tags_of(element) == {
        'highway': 'bus_stop',
        'name': 'Union Grove',
        'naptan:AtcoCode': 'A',
        'naptan:Bearing': 'SW',
    }


def test_tags_someone_already_added_are_skipped():
    element = _element({'naptan:AtcoCode': 'A'})

    assert not add_missing_tags(element, 'node/42', {'naptan:AtcoCode': 'A'})


def test_a_tag_given_another_value_since_loading_is_a_conflict():
    element = _element({'naptan:Bearing': 'NE'})

    with pytest.raises(HTTPException) as e:
        add_missing_tags(element, 'node/42', {'naptan:Bearing': 'SW', 'naptan:AtcoCode': 'A'})

    assert e.value.status_code == 409
    assert 'naptan:Bearing' in e.value.detail
    assert _tags_of(element) == {'naptan:Bearing': 'NE'}


class FakeOpenStreetMap:
    def __init__(self, nodes=(), ways=()):
        self.nodes = {n['@id']: n for n in nodes}
        self.ways = {w['@id']: w for w in ways}
        self.requested = []

    async def get_nodes(self, ids, *, json=True):
        assert not json
        self.requested.append(('node', tuple(ids)))
        return [self.nodes[i] for i in ids]

    async def get_ways(self, ids, *, json=True):
        assert not json
        self.requested.append(('way', tuple(ids)))
        return [self.ways[i] for i in ids]


def _build_elements(additions, osm):
    return asyncio.run(build_tag_addition_elements(additions, osm))


def test_elements_are_fetched_per_type_and_only_changed_ones_returned():
    osm = FakeOpenStreetMap(
        nodes=[_element({}, '1'), _element({'naptan:AtcoCode': 'B'}, '2')],
        ways=[_element({}, '3')],
    )

    elements = _build_elements(
        [
            StopTagAddition(type='node', id=1, tags={'naptan:AtcoCode': 'A'}),
            StopTagAddition(type='node', id=2, tags={'naptan:AtcoCode': 'B'}),
            StopTagAddition(type='way', id=3, tags={'naptan:AtcoCode': 'C'}),
        ],
        osm,
    )

    assert [(element_type, element['@id']) for element_type, element in elements] == [('node', '1'), ('way', '3')]
    assert sorted(osm.requested) == [('node', ('1', '2')), ('way', ('3',))]


def test_nothing_is_fetched_without_additions():
    assert _build_elements([], None) == []


def test_only_naptan_keys_can_be_added():
    with pytest.raises(HTTPException) as e:
        _build_elements(
            [StopTagAddition(type='node', id=1, tags={'name': 'Renamed', 'highway': 'no'})], FakeOpenStreetMap()
        )

    assert e.value.status_code == 400
    assert 'highway, name' in e.value.detail


def test_the_same_stop_twice_is_rejected():
    addition = StopTagAddition(type='node', id=1, tags={'naptan:AtcoCode': 'A'})

    with pytest.raises(HTTPException):
        _build_elements([addition, addition], FakeOpenStreetMap(nodes=[_element({}, '1')]))


@pytest.mark.parametrize('overrides', [{'type': 'relation'}, {'id': 0}, {'id': -1}])
def test_relations_and_new_elements_cannot_be_tagged_this_way(overrides):
    with pytest.raises(ValidationError):
        StopTagAddition(**{'type': 'node', 'id': 1, 'tags': {}, **overrides})


def _route(members):
    return FinalRoute(
        ways=(),
        latLngs=(),
        busStops=(),
        tags={'type': 'route', 'route': 'bus', 'public_transport:version': '2'},
        extraWaysToUpdate=(),
        members=tuple(members),
        warnings=(),
    )


def test_tagged_stops_are_modified_in_the_changeset():
    osm = FakeOpenStreetMap(nodes=[{**_element({'highway': 'bus_stop'}, '42'), '@user': 'someone', '@uid': '1'}])

    xml = asyncio.run(
        build_osm_change(
            None,
            _route([RelationMember(id='42', type='node', role='platform')]),
            include_changeset_id=False,
            overpass=None,
            osm=osm,
            tags_edited={'type': 'route', 'route': 'bus', 'public_transport:version': '2'},
            tag_additions=[StopTagAddition(type='node', id=42, tags={'naptan:AtcoCode': 'A'})],
        )
    )
    node = xmltodict.parse(xml)['osmChange']['modify']['node']

    assert node['@id'] == '42'
    assert node['@version'] == '3'
    assert '@user' not in node
    assert _tags_of(node) == {'highway': 'bus_stop', 'naptan:AtcoCode': 'A'}


def test_a_split_way_cannot_also_be_tagged():
    route = _route([RelationMember(id='7_1_2', type='way', role=''), RelationMember(id='7_2_2', type='way', role='')])

    with pytest.raises(HTTPException) as e:
        asyncio.run(
            build_osm_change(
                None,
                route,
                include_changeset_id=False,
                overpass=None,
                osm=None,
                tag_additions=[StopTagAddition(type='way', id=7, tags={'naptan:AtcoCode': 'A'})],
            )
        )

    assert e.value.status_code == 400


def _model(**kwargs):
    return PostDownloadOsmChangeModel(relationId=7, route={}, tags={'name': 'Bus 12'}, **kwargs)


def test_comment_and_source_mention_tagged_stops():
    model = _model(
        naptanTagAdditions=[StopTagAddition(type='node', id=1, tags={}), StopTagAddition(type='node', id=2, tags={})]
    )

    tags = model.make_changeset_tags()

    assert tags['comment'] == 'Updated route: Bus 12, #7; added NaPTAN tags to 2 bus stops'
    assert tags['source'] == 'NaPTAN'
