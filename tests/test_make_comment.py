import pytest
from pydantic import ValidationError

from config import TAG_MAX_LENGTH
from main import PostDownloadOsmChangeModel


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
