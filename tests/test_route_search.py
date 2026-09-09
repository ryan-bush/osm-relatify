import asyncio
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

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
    return _search_between(ways, ElementId('0'), ElementId(str(WAY_COUNT - 1)))


def _search_between(ways, start_way, end_way):
    graph = build_graph(ways)

    async def run():
        with ProcessPoolExecutor(2) as executor:
            return await modified_dfs(
                graph,
                ways,
                start_way,
                end_way,
                {},
                executor,
                n_processes=2,
            )

    return asyncio.run(run())


def _way_at(ways, way_id, lat_lngs, connected_to, **kwargs):
    ways[way_id] = FetchRelationElement(
        id=way_id,
        member=True,
        oneway=False,
        roundabout=False,
        nodes=list(range(len(lat_lngs))),
        latLngs=lat_lngs,
        connectedTo=connected_to,
        turn_in_place_start=kwargs.get('turn_start', False),
        turn_in_place_end=kwargs.get('turn_end', False),
    )


def _entry_exit(way):
    return way.latLngs[0], way.latLngs[-1]


def _assert_continuous(path, ways):
    """A route is only uploadable if each way starts where the one before it ended."""
    for previous, current in zip(path, path[1:], strict=False):
        previous_start, previous_end = _entry_exit(ways[previous.way_id])
        current_start, current_end = _entry_exit(ways[current.way_id])

        previous_exit = previous_end if previous.is_start else previous_start
        current_entry = current_start if current.is_start else current_end

        assert previous_exit == current_entry, f'{previous} does not meet {current}'


def test_u_turns_at_real_dead_ends_stay_searchable(cul_de_sac_chain):
    assert _search(cul_de_sac_chain).path


def test_u_turns_everywhere_stay_searchable(cul_de_sac_chain):
    """The turn used to be modelled as a jump between junctions, which exploded here."""
    for way in cul_de_sac_chain.values():
        cul_de_sac_chain[way.id] = replace(way, turn_in_place_start=True, turn_in_place_end=True)

    assert _search(cul_de_sac_chain).path


def test_turning_around_doubles_back_instead_of_leaving_a_gap():
    """The C6 turns at the end of a spur; the spur must then be driven a second time."""
    ways = {}
    _way_at(ways, ElementId('0'), [(51.0, 0.0), (51.0, 0.001)], [ElementId('1')])
    _way_at(ways, ElementId('1'), [(51.0, 0.001), (51.0, 0.002)], [ElementId('0'), ElementId('2')])
    _way_at(ways, ElementId('2'), [(51.0, 0.002), (51.001, 0.002)], [ElementId('1')], turn_end=True)

    path = _search_between(ways, ElementId('0'), ElementId('1')).path

    _assert_continuous(path, ways)
    assert [key.way_id for key in path].count(ElementId('2')) == 2


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
