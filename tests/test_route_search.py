import asyncio
from collections import defaultdict
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


def _oneway_network(way_nodes, coords, roundabouts=()):
    """Oneway member ways from lists of named nodes, connected wherever they share a node."""
    node_ways = defaultdict(list)
    for way_id, nodes in way_nodes.items():
        for node in nodes:
            node_ways[node].append(way_id)

    ways = {}
    for way_id, nodes in way_nodes.items():
        # definition order, so which neighbor the search meets first is fixed
        connected = dict.fromkeys(other for node in nodes for other in node_ways[node] if other != way_id)
        ways[ElementId(way_id)] = FetchRelationElement(
            id=ElementId(way_id),
            member=True,
            oneway=True,
            roundabout=way_id in roundabouts,
            nodes=list(range(len(nodes))),
            latLngs=[coords[node] for node in nodes],
            connectedTo=[ElementId(other) for other in connected],
            turn_in_place_start=False,
            turn_in_place_end=False,
        )
    return ways


DIAMONDS = 16


def test_detour_off_the_main_road_is_tried_before_carrying_on(monkeypatch):
    """M5 J24 on the Falcon: off the motorway, round the interchange, along to the next
    roundabout to serve a stop and back, round the interchange again and back on.

    A long route runs out of search time, and then only what was tried first comes back.
    The run of forks after the junction stands in for the rest of the way to Plymouth,
    with more alternatives than the search gets through before it would backtrack.
    """
    coords = {
        'm0': (51.1150, -2.9750),
        'm1': (51.1050, -2.9850),  # off-slip leaves the motorway
        'm2': (51.0970, -2.9930),  # on-slip joins it
        'R1': (51.1015, -2.9880),  # off-slip meets the interchange
        'R2': (51.0990, -2.9905),  # on-slip leaves the interchange
        'R3': (51.0988, -2.9930),
        'R4': (51.0995, -2.9950),
        'R5': (51.1012, -2.9962),  # A38 out
        'R6': (51.1020, -2.9955),  # A38 back
        'R7': (51.1026, -2.9915),
        'a1': (51.1030, -2.9990),
        'W1': (51.1047, -3.0015),
        'W2': (51.1045, -3.0028),
        'W3': (51.1063, -3.0030),
        'W4': (51.1055, -3.0012),
        'a2': (51.1035, -2.9975),
    }
    way_nodes = {
        'mot_in': ['m0', 'm1'],
        'off_slip': ['m1', 'R1'],
        'i12': ['R1', 'R2'],
        'i23': ['R2', 'R3'],
        'i34': ['R3', 'R4'],
        'i45': ['R4', 'R5'],
        'a38_out': ['R5', 'a1', 'W1'],
        'w12': ['W1', 'W2'],
        'w23': ['W2', 'W3'],
        'w34': ['W3', 'W4'],
        'a38_back': ['W4', 'a2', 'R6'],
        'i67': ['R6', 'R7'],
        'i71': ['R7', 'R1'],
        # after the roundabout carries on, so an unordered search leaves by it first
        'on_slip': ['R2', 'm2'],
    }

    previous = 'm2'
    for i in range(DIAMONDS):
        lat, lon = coords[previous]
        coords[f'd{i}l'] = (lat - 0.0005, lon - 0.0015)
        coords[f'd{i}r'] = (lat - 0.0015, lon - 0.0005)
        coords[f'd{i}'] = (lat - 0.002, lon - 0.002)
        way_nodes[f'fork{i}l'] = [previous, f'd{i}l', f'd{i}']
        way_nodes[f'fork{i}r'] = [previous, f'd{i}r', f'd{i}']
        previous = f'd{i}'
    coords['end'] = (coords[previous][0] - 0.002, coords[previous][1] - 0.002)
    way_nodes['mot_end'] = [previous, 'end']

    ways = _oneway_network(way_nodes, coords, roundabouts={'i12', 'i23', 'i34', 'i45', 'i67', 'i71'})

    # only the head start the search always gets, as when a long route hits the budget
    monkeypatch.setattr(route_module, 'MAX_SEARCH_TIME', 0)

    path = _search_between(ways, ElementId('mot_in'), ElementId('mot_end')).path
    way_ids = [key.way_id for key in path]

    _assert_continuous(path, ways)
    assert way_ids[-1] == ElementId('mot_end')
    assert ElementId('a38_out') in way_ids
    assert ElementId('a38_back') in way_ids
    # passed once on the way round, then driven again to reach the on-slip
    assert way_ids.count(ElementId('i12')) == 2


def _junction_loops():
    """A way in and a way out of one junction, with two loops that start and end there."""
    coords = {
        'n0': (50.9990, 0.0000),
        'n1': (51.0000, 0.0000),  # the junction
        'n2': (51.0000, 0.0010),
        'n3': (51.0010, 0.0010),
        'n4': (51.0000, -0.0010),
        'n5': (50.9995, -0.0010),
        'n6': (50.9993, -0.0003),
    }
    way_nodes = {
        'in': ['n0', 'n1'],
        'a': ['n1', 'n2'],
        'b': ['n2', 'n3'],
        'c': ['n3', 'n1'],
        'd': ['n1', 'n5'],
        'e': ['n5', 'n6', 'n1'],
        'out': ['n1', 'n4'],
    }
    return _oneway_network(way_nodes, coords)


def _drop_loops(ways, way_ids):
    path = tuple(route_module.GraphKey(ElementId(way_id), route_module.BOOL_START) for way_id in way_ids)
    best_path = route_module.BestPath.zero()._replace(path=path)

    result = route_module.drop_redundant_loops(best_path, build_graph(ways), ways, {})

    _assert_continuous(result.path, ways)
    return [str(key.way_id) for key in result.path]


def test_a_lap_driven_again_for_nothing_is_dropped():
    """Blackbrook Park Avenue on the Falcon: a search out of time went past the turning loop
    at its end, lapped the roundabout and drove the avenue again to reach it."""
    ways = _junction_loops()

    assert _drop_loops(ways, ['in', 'a', 'b', 'c', 'a', 'b', 'c', 'out']) == ['in', 'a', 'b', 'c', 'out']


def test_a_lap_apart_from_its_repeat_is_dropped():
    ways = _junction_loops()

    assert _drop_loops(ways, ['in', 'a', 'b', 'c', 'd', 'e', 'a', 'b', 'c', 'out']) == [
        'in',
        'd',
        'e',
        'a',
        'b',
        'c',
        'out',
    ]


def test_a_loop_that_is_the_only_pass_over_its_ways_stays():
    ways = _junction_loops()

    assert _drop_loops(ways, ['in', 'a', 'b', 'c', 'd', 'e', 'out']) == ['in', 'a', 'b', 'c', 'd', 'e', 'out']
