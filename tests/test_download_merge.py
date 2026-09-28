"""
What a download of more map data says about itself.

Panning far enough downloads another area for the relation already open, and the answer
says so with fetchMerge. The client leans on it: the tags in the table, the route master
queued against them, the ways marked as members and the ends of the line are the mapper's
work, and an answer that merges into what is open must not replace them. A relation being
created makes that plain — it exists nowhere but here, so every answer about it carries
the bare tags a new route starts from.
"""

import asyncio
from dataclasses import replace

import pytest

from models.download_history import Cell, DownloadHistory
from overpass import Overpass


class _Reply:
    """An Overpass answer with nothing in it, which is all the history needs to grow."""

    def __init__(self, splits: int):
        # the elements come back in groups, each closed by a count element
        self._elements = [{'type': 'count'} for _ in range(splits)]

    def json(self):
        return {'elements': self._elements}


@pytest.fixture
def overpass(monkeypatch):
    posted = []

    async def fake_post(query, query_timeout):  # noqa: ARG001
        posted.append(query)
        # seven groups for the area, and one more for the driving side riding along
        return _Reply(8)

    monkeypatch.setattr('overpass.overpass_post', fake_post)
    return posted


def _query(download_hist, download_targets):
    return asyncio.run(
        Overpass().query_relation(
            relation_id=None,
            download_hist=download_hist,
            download_targets=download_targets,
            route_type='bus',
        )
    )


def test_the_first_download_is_not_a_merge(overpass):  # noqa: ARG001
    _, download_hist, *_ = _query(None, (Cell(x=0, y=0),))

    assert len(download_hist.history) == 1, 'nothing to merge into yet'


def test_downloading_more_merges_into_what_is_open(overpass):  # noqa: ARG001
    """This is the answer that must not replace the tags the mapper has typed."""
    _, first, *_ = _query(None, (Cell(x=0, y=0),))
    _, second, *_ = _query(first, (Cell(x=1, y=0),))

    assert len(second.history) == 2
    assert second.session == first.session, 'the same download, continued'


def test_every_further_area_keeps_it_a_merge(overpass):  # noqa: ARG001
    _, hist, *_ = _query(None, (Cell(x=0, y=0),))

    for x in range(1, 4):
        _, hist, *_ = _query(hist, (Cell(x=x, y=0),))

    assert len(hist.history) == 4


def test_a_reload_collapses_the_history_it_had(overpass):  # noqa: ARG001
    """
    Which is why the endpoint says a reload is a merge in its own right.

    A reload starts a new session over the cells already downloaded, leaving one entry in
    the history — so the history alone would call it a first download, and the tags the
    mapper typed would be replaced by an answer they did not ask for.
    """
    _, first, *_ = _query(None, (Cell(x=0, y=0),))
    _, second, *_ = _query(first, (Cell(x=1, y=0),))

    # as the /query endpoint does when the client asks for a reload
    collapsed = replace(
        second,
        session=DownloadHistory.make_session(),
        history=(tuple(cell for cells in second.history for cell in cells),),
    )

    assert len(collapsed.history) == 1
    assert len(collapsed.history[0]) == 2, 'both cells, in one entry'
