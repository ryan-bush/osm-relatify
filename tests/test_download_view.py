"""
Downloading everything in the map view at once.

A long rural route, Bude to Wadebridge say, spans some thousand grid cells, and growing it
a few roads at a time by clicking at the edge of the download is slow going. The map's
"download this view" button asks for the view instead, and only what is not yet downloaded
is fetched or counted against the limit on one request.
"""

import pytest
from fastapi import HTTPException

from main import VIEW_DOWNLOAD_MAX_CELLS, view_download_targets
from models.bounding_box import BoundingBox
from models.download_history import Cell
from overpass import get_download_triggers

# about 5 by 3 cells of north Cornwall: 0.01 degree cells, with room either side
VIEW = (50.501, -4.849, 50.529, -4.801)


def test_a_view_is_the_cells_it_covers():
    targets = view_download_targets(VIEW)

    assert set(targets) == BoundingBox(*VIEW).get_grid_cells()


def test_cells_already_downloaded_are_not_asked_again():
    downloaded = frozenset(list(BoundingBox(*VIEW).get_grid_cells())[:4])

    targets = view_download_targets(VIEW, downloaded)

    assert downloaded.isdisjoint(targets)
    assert len(targets) == len(BoundingBox(*VIEW).get_grid_cells()) - 4


def test_a_view_already_downloaded_asks_for_nothing():
    assert view_download_targets(VIEW, frozenset(BoundingBox(*VIEW).get_grid_cells())) == ()


def test_the_order_is_stable_for_the_cache():
    assert view_download_targets(VIEW) == tuple(sorted(view_download_targets(VIEW), key=lambda c: (c.x, c.y)))


def test_a_view_too_large_is_refused():
    # Bude to Wadebridge in one go
    with pytest.raises(HTTPException) as e:
        view_download_targets((50.52, -4.84, 50.84, -4.54))

    assert e.value.status_code == 400


def test_only_new_cells_count_against_the_limit():
    """A wide view reaching a little past what is downloaded is still fine."""
    wide = (50.005, 0.005, 50.195, 0.195)  # 20 by 20 cells
    cells = BoundingBox(*wide).get_grid_cells()
    assert len(cells) > VIEW_DOWNLOAD_MAX_CELLS

    # all but the eastern five columns
    min_x = min(c.x for c in cells)
    downloaded = frozenset(c for c in cells if c.x < min_x + 15)

    assert len(view_download_targets(wide, downloaded)) <= VIEW_DOWNLOAD_MAX_CELLS


class _Bbc:
    """Nothing is downloaded yet, as far as the triggers can tell."""

    def contains(self, lat_lng):  # noqa: ARG002
        return False


class _Way:
    def __init__(self, *lat_lngs):
        self.latLngs = lat_lngs


POINT = (50.005, 0.005)


def _cell_of(lat_lng):
    [cell] = BoundingBox(lat_lng[0], lat_lng[1], lat_lng[0], lat_lng[1]).get_grid_cells()
    return cell


def test_a_trigger_asks_for_the_5x5_cells_around_a_road():
    triggers = get_download_triggers(_Bbc(), (), {'w1': _Way(POINT)})

    c = _cell_of(POINT)
    assert set(triggers['w1']) == {Cell(x, y) for x in range(c.x - 2, c.x + 3) for y in range(c.y - 2, c.y + 3)}


def test_a_trigger_leaves_out_every_cell_downloaded_so_far():
    c = _cell_of(POINT)
    downloaded = (c, Cell(c.x + 1, c.y))

    triggers = get_download_triggers(_Bbc(), downloaded, {'w1': _Way(POINT)})

    assert set(downloaded).isdisjoint(triggers['w1'])
    assert len(triggers['w1']) == 25 - 2
