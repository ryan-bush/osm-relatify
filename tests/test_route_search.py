import asyncio
from concurrent.futures import ProcessPoolExecutor

import pytest

from cython_lib import route as route_module
from cython_lib.route import build_graph, modified_dfs
from models.element_id import ElementId
from models.fetch_relation import FetchRelationElement

WAY_COUNT = 6


@pytest.fixture
def cul_de_sac_chain():
    """A dead-end road: a chain of ways, turnable only at the two real dead ends."""
    nodes = [(51.0 + i * 0.001, 0.0) for i in range(WAY_COUNT + 1)]
    ways = {}

    for i in range(WAY_COUNT):
        way_id = ElementId(str(i))
        ways[way_id] = FetchRelationElement(
            id=way_id,
            member=True,
            oneway=False,
            roundabout=False,
            nodes=[i, i + 1],
            latLngs=[nodes[i], nodes[i + 1]],
            connectedTo=[ElementId(str(j)) for j in (i - 1, i + 1) if 0 <= j < WAY_COUNT],
            turn_in_place_start=(i == 0),
            turn_in_place_end=(i == WAY_COUNT - 1),
        )

    return ways


def _search(ways):
    graph = build_graph(ways)

    async def run():
        with ProcessPoolExecutor(2) as executor:
            return await modified_dfs(
                graph,
                ways,
                ElementId('0'),
                ElementId(str(WAY_COUNT - 1)),
                {},
                executor,
                n_processes=2,
            )

    return asyncio.run(run())


def test_u_turns_at_real_dead_ends_stay_searchable(cul_de_sac_chain):
    assert _search(cul_de_sac_chain).path


def test_search_gives_up_on_budget_instead_of_running_to_exhaustion(cul_de_sac_chain, monkeypatch, capsys):
    # a worker that never drains its stack, standing in for a search too large to
    # exhaust; without a budget the driver would loop on it forever
    def never_finishes(_graph, _ways, _end_way, _bus_map, stack, best_path, **_kwargs):
        return list(stack), best_path

    monkeypatch.setattr(route_module, 'modified_dfs_worker', never_finishes)
    monkeypatch.setattr(route_module, 'MAX_SEARCH_TIME', 0)

    best_path = _search(cul_de_sac_chain)

    assert 'budget' in capsys.readouterr().out
    # nothing was explored, so there is no route - but the driver returned
    assert not best_path.path
