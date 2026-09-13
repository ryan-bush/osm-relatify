import pytest
from pydantic import ValidationError

from config import TAG_MAX_LENGTH
from main import PostDownloadOsmChangeModel
from route_masters import RouteMasterChange


def make(tags: dict[str, str] | None = None, **kwargs) -> PostDownloadOsmChangeModel:
    return PostDownloadOsmChangeModel(relationId=7, route={}, tags=tags or {}, **kwargs)


def test_custom_comment_wins():
    assert make({'name': 'Bus 12'}, comment='Fixed the northbound leg').make_comment() == 'Fixed the northbound leg'


def test_custom_comment_is_stripped():
    assert make(comment='  spaced  ').make_comment() == 'spaced'


@pytest.mark.parametrize('comment', [None, '', '   '])
def test_blank_custom_comment_falls_back_to_the_generated_one(comment):
    assert make({'name': 'Bus 12'}, comment=comment).make_comment() == 'Updated route: Bus 12, #7'


def test_generated_comment_uses_name_and_ref():
    assert make({'name': 'Airport Line', 'ref': '12'}).make_comment() == 'Updated route: 12 Airport Line, #7'


def test_generated_comment_drops_a_ref_already_in_the_name():
    assert make({'name': 'Bus 12', 'ref': '12'}).make_comment() == 'Updated route: Bus 12, #7'


def test_generated_comment_with_only_a_ref():
    assert make({'ref': '12'}).make_comment() == 'Updated route: 12, #7'


def test_generated_comment_without_name_or_ref():
    assert make().make_comment() == 'Updated route #7'


def test_generated_comment_follows_edited_tags():
    """The working copy is what gets submitted, so a renamed route names itself in the changeset."""
    assert make({'name': 'Bus 13'}).make_comment() == 'Updated route: Bus 13, #7'


def test_comment_at_the_length_limit_is_accepted():
    comment = 'x' * TAG_MAX_LENGTH
    assert make(comment=comment).make_comment() == comment


def test_comment_over_the_length_limit_is_rejected():
    """Rejected up front rather than silently truncated with an ellipsis at upload time."""
    with pytest.raises(ValidationError):
        make(comment='x' * (TAG_MAX_LENGTH + 1))


def _position(id=-2, name='High Street'):
    return {'id': id, 'lat': 51.5, 'lon': -0.12, 'wayId': 201, 'afterNode': 10, 'beforeNode': 11, 'name': name}


def test_generated_comment_counts_stop_positions():
    model = make({'name': 'Bus 12'}, newStopPositions=[_position()])
    assert model.make_comment() == 'Updated route: Bus 12, #7; added 1 stop position'


def test_generated_comment_counts_several_stop_positions():
    model = make({'name': 'Bus 12'}, newStopPositions=[_position(), _position(id=-3)])
    assert model.make_comment() == 'Updated route: Bus 12, #7; added 2 stop positions'


def test_generated_comment_counts_stops_and_their_stop_positions():
    model = make(
        {'name': 'Bus 12'},
        newStops=[{'id': -1, 'lat': 51.5, 'lon': -0.12, 'tags': {'name': 'High Street'}}],
        newStopPositions=[_position()],
    )
    assert model.make_comment() == 'Updated route: Bus 12, #7; added 1 bus stop; added 1 stop position'


def _area(id=None, name='The Station'):
    return {'id': id, 'name': name, 'members': [{'type': 'node', 'id': 1, 'role': 'platform'},
                                                {'type': 'node', 'id': 2, 'role': 'stop'}]}


def test_generated_comment_counts_new_stop_areas():
    model = make({'name': 'Bus 12'}, stopAreas=[_area()])
    assert model.make_comment() == 'Updated route: Bus 12, #7; added 1 stop area'


def test_generated_comment_counts_completed_stop_areas():
    model = make({'name': 'Bus 12'}, stopAreas=[_area(id=99)])
    assert model.make_comment() == 'Updated route: Bus 12, #7; completed 1 stop area'


def test_generated_comment_tells_new_and_completed_apart():
    model = make({'name': 'Bus 12'}, stopAreas=[_area(), _area(id=99), _area(id=98)])
    assert model.make_comment() == 'Updated route: Bus 12, #7; added 1 stop area; completed 2 stop areas'


def test_a_stop_edited_by_hand_is_not_called_a_naptan_addition():
    model = make(
        naptanTagAdditions=[
            {'type': 'node', 'id': 1, 'tags': {'name': 'High Street'}, 'byHand': ['name']},
            {'type': 'node', 'id': 2, 'tags': {'naptan:Bearing': 'NE'}},
        ]
    )

    comment = model.make_comment()
    assert 'edited 1 bus stop' in comment
    assert 'added NaPTAN tags to 1 bus stop' in comment


def test_one_stop_with_both_is_counted_in_both():
    model = make(
        naptanTagAdditions=[
            {'type': 'node', 'id': 1, 'tags': {'name': 'High Street', 'naptan:Bearing': 'NE'}, 'byHand': ['name']}
        ]
    )

    comment = model.make_comment()
    assert 'edited 1 bus stop' in comment
    assert 'added NaPTAN tags to 1 bus stop' in comment


def test_a_renamed_stop_area_says_so():
    model = make(
        stopAreas=[
            {'id': 99, 'name': 'Market Square', 'expectedName': 'The Station', 'members': [{'type': 'node', 'id': 1, 'role': 'platform'}]},
            {'id': 98, 'name': '', 'members': [{'type': 'node', 'id': 1, 'role': 'platform'}]},
        ]
    )

    comment = model.make_comment()
    assert 'renamed 1 stop area' in comment
    assert 'completed 1 stop area' in comment


def test_naptan_is_not_credited_for_what_the_mapper_typed():
    model = make(
        {'name': 'Bus 12'},
        naptanTagAdditions=[{'type': 'node', 'id': 1, 'tags': {'name': 'High Street'}, 'byHand': ['name']}],
    )

    assert 'source' not in model.make_changeset_tags()


def test_naptan_is_still_credited_when_it_supplied_something():
    model = make(
        {'name': 'Bus 12'},
        naptanTagAdditions=[
            {'type': 'node', 'id': 1, 'tags': {'name': 'High Street', 'naptan:Bearing': 'NE'}, 'byHand': ['name']}
        ],
    )

    assert model.make_changeset_tags()['source'] == 'NaPTAN'


MASTER_TAGS = {'type': 'route_master', 'route_master': 'bus', 'ref': '12', 'name': 'Bus 12'}


def _master(**kwargs):
    return RouteMasterChange(**kwargs)


def test_comment_says_a_route_master_was_created():
    comment = make({'name': 'Bus 12'}, routeMaster=_master(tags={'name': 'Bus 12'})).make_comment()

    assert comment == 'Updated route: Bus 12, #7; created route master'


def test_comment_names_the_route_master_joined():
    comment = make({'name': 'Bus 12'}, routeMaster=_master(id=100)).make_comment()

    assert comment == 'Updated route: Bus 12, #7; added to route master #100'


def test_comment_says_when_the_master_was_edited_too():
    comment = make(
        {'name': 'Bus 12'},
        routeMaster=_master(id=100, tags={**MASTER_TAGS, 'operator': 'Alpha'}, tagsOriginal=MASTER_TAGS),
    ).make_comment()

    assert comment == 'Updated route: Bus 12, #7; added to route master #100 and edited its tags'


def test_comment_does_not_claim_an_edit_that_was_not_made():
    comment = make(
        {'name': 'Bus 12'},
        routeMaster=_master(id=100, tags={**MASTER_TAGS, 'name': '  Bus 12  '}, tagsOriginal=MASTER_TAGS),
    ).make_comment()

    assert comment == 'Updated route: Bus 12, #7; added to route master #100'


def test_comment_counts_the_masters_left():
    assert make({'name': 'Bus 12'}, routeMasterDetach=[100]).make_comment().endswith(
        '; removed from 1 route master'
    )
    assert make({'name': 'Bus 12'}, routeMasterDetach=[100, 101]).make_comment().endswith(
        '; removed from 2 route masters'
    )


def test_comment_says_nothing_about_masters_when_none_changed():
    assert make({'name': 'Bus 12'}).make_comment() == 'Updated route: Bus 12, #7'
