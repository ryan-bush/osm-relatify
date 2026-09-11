import asyncio
import time
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from functools import partial
from heapq import heappop, heappush
from itertools import chain, count
from typing import NamedTuple, Self

import cython
import networkx as nx

from cython_lib.geoutils import haversine_distance
from models.element_id import ElementId
from models.fetch_relation import FetchRelationBusStopCollection, FetchRelationElement
from models.final_route import FinalRoute, FinalRouteWay
from relation_builder import SortedBusEntry, sort_bus_on_path
from utils import print_run_time

if cython.compiled:
    from cython.cimports.libc.math import acos, pi

    print(f'{__name__}: 🐇 compiled')
else:
    from math import acos, pi

    print(f'{__name__}: 🐌 not compiled')


@cython.cfunc
def _degrees(x: cython.double) -> cython.double:
    return x * (180 / pi)


BOOL_START = True
BOOL_END = False

VISITED_LIMIT = 2
MAX_LOOP_LENGTH = 1000
MAX_AFTER_FINISH_LENGTH = 1000
MAX_EXTRA_DISTANCE_TO_CONVERT = 1000
MAX_PATH_LENGTH_FACTOR = 2.2

# The search is exhaustive, so anything that widens the graph - a U-turn most of
# all - can grow it beyond what is searchable. Past this budget, return the best
# route found so far instead of letting the request time out with nothing.
MAX_SEARCH_TIME = 10.0  # seconds, must stay below the request timeout in main.py


class GraphKey(NamedTuple):
    way_id: ElementId
    is_start: bool


class GraphValue(NamedTuple):
    intersection_id: int
    connected_to: tuple[GraphKey, ...]


class StackElement(NamedTuple):
    path: tuple[GraphKey, ...]
    visited_bus_stops: dict[ElementId, int]
    almost_visited_bus_stops: dict[ElementId, int]
    intersection_bus_stops_snapshot: dict[int, tuple[GraphKey, int]]
    length: float
    complete_path: set[ElementId]
    complete_length: float
    angle_sum: float = 0
    loop_length: float = 0
    after_finish_length: float = 0
    roundabout_enter: GraphKey | None = None


class BestPath(NamedTuple):
    path: tuple[GraphKey, ...]
    visited_bus_stops: dict[ElementId, int]
    bus_stops_count: int
    almost_bus_stops_count: int
    length: float
    complete_path: set[ElementId]
    complete_length: float
    angle_sum: float

    @classmethod
    def zero(cls) -> Self:
        return cls(
            path=(),
            visited_bus_stops={},
            bus_stops_count=0,
            almost_bus_stops_count=0,
            length=0,
            complete_path=set(),
            complete_length=0,
            angle_sum=0,
        )

    def select_best(self, other: Self) -> Self:
        complete_length_diff: cython.double = other.complete_length - self.complete_length
        if abs(complete_length_diff) < 0.1:  # avoid floating point errors
            complete_length_diff = 0

        # more complete
        if complete_length_diff > 0:
            return other
        if complete_length_diff < 0:
            return self

        length_diff: cython.double = other.length - self.length
        if abs(length_diff) < 0.1:  # avoid floating point errors
            length_diff = 0

        bus_stops_count_diff: cython.int = other.bus_stops_count - self.bus_stops_count
        almost_bus_stops_count_diff: cython.int = other.almost_bus_stops_count - self.almost_bus_stops_count

        if bus_stops_count_diff and bus_stops_count_diff + almost_bus_stops_count_diff == 0:
            max_convert_distance: cython.int = MAX_EXTRA_DISTANCE_TO_CONVERT * bus_stops_count_diff

            if length_diff < max_convert_distance < 0:
                return other
            if 0 < max_convert_distance < length_diff:
                return self

        # more bus stops
        if bus_stops_count_diff > 0:
            return other
        if bus_stops_count_diff < 0:
            return self

        if almost_bus_stops_count_diff > 0:
            return other
        if almost_bus_stops_count_diff < 0:
            return self

        # shorter path
        if length_diff < 0:
            return other
        if length_diff > 0:
            return self

        # simpler angles
        if self.angle_sum > other.angle_sum:
            return other
        if self.angle_sum < other.angle_sum:
            return self

        return self  # paths are equal


class BestPathCollection(NamedTuple):
    invalid: BestPath
    valid: BestPath

    def merge(self, other: Self, ways: dict[ElementId, FetchRelationElement]) -> 'BestPathCollection':
        return BestPathCollection(
            invalid=self.invalid.select_best(other.invalid),
            valid=self.valid.select_best(other.valid),
        )


def build_graph(ways: dict[ElementId, FetchRelationElement]) -> dict[GraphKey, GraphValue]:
    convert_graph: dict[GraphKey, list[GraphKey]] = {}

    for way_id, way in ways.items():

        def find_connections_at(latlon: tuple[float, float]) -> list[GraphKey]:
            connections: list[GraphKey] = []
            for connected_way_id in way.connectedTo:  # noqa: B023
                connected_way = ways.get(connected_way_id)
                if not connected_way:  # skip non-member ways
                    continue
                connected_start = connected_way.latLngs[0]
                connected_end = connected_way.latLngs[-1]
                if latlon == connected_start:
                    connections.append(GraphKey(connected_way_id, BOOL_START))
                elif latlon == connected_end and not connected_way.oneway:
                    connections.append(GraphKey(connected_way_id, BOOL_END))
            return connections

        # A U-turn is turning around on the spot and driving back over the way
        # just travelled, so the edge it adds is the way itself, re-entered from
        # the end the turn happens at. Either way round, that means driving the
        # way in both directions, which a oneway does not allow.

        # Build neighbors for START
        start_neighbors = find_connections_at(way.latLngs[0])
        if way.turn_in_place_start and not way.oneway:
            start_neighbors.append(GraphKey(way_id, BOOL_START))

        # Build neighbors for END
        end_neighbors = find_connections_at(way.latLngs[-1])
        if way.turn_in_place_end and not way.oneway:
            end_neighbors.append(GraphKey(way_id, BOOL_END))

        convert_graph[GraphKey(way_id, BOOL_START)] = start_neighbors
        convert_graph[GraphKey(way_id, BOOL_END)] = end_neighbors

    intersection_num: cython.int = -1
    result: dict[GraphKey, GraphValue] = {}
    while convert_graph:
        intersection_num += 1
        key, neighbors = convert_graph.popitem()
        result[key] = GraphValue(intersection_num, tuple(neighbors))
        for neighbor in neighbors:
            if neighbor in convert_graph:
                # normal convert
                neighbor_neighbors = convert_graph.pop(neighbor)
                result[neighbor] = GraphValue(intersection_num, tuple(neighbor_neighbors))
            else:
                # merge convert (happens due to oneway)
                result[neighbor] = result[neighbor]._replace(intersection_id=intersection_num)
    return result


def loop_components(graph: dict[GraphKey, GraphValue]) -> dict[GraphKey, int]:
    """Number each way entry by the strongly connected component it belongs to.

    Two entries share a component when each can be driven to from the other, so a way
    in the same component as the one before it can bring the bus back again.
    """
    digraph = nx.DiGraph()
    digraph.add_nodes_from(graph)
    for key in graph:
        exit_at_key = key._replace(is_start=not key.is_start)
        digraph.add_edges_from((key, neighbor) for neighbor in graph[exit_at_key].connected_to)

    return {
        key: component_num
        for component_num, component in enumerate(nx.strongly_connected_components(digraph))
        for key in component
    }


def angle_between_ways(
    latlons1: Sequence[tuple[cython.double, cython.double]],
    latlons2: Sequence[tuple[cython.double, cython.double]],
) -> cython.double:
    start1 = latlons1[0]
    end1 = latlons1[-1]
    start2 = latlons2[0]
    end2 = latlons2[-1]

    # consider very end segments for angle calculation
    if end1 == start2:
        start1 = latlons1[-2]
        end2 = latlons2[1]

        d12: cython.double = haversine_distance(start1, end1)
        d23: cython.double = haversine_distance(end1, end2)
        d13: cython.double = haversine_distance(start1, end2)

    elif end1 == end2:
        start1 = latlons1[-2]
        start2 = latlons2[-2]

        d12: cython.double = haversine_distance(start1, end1)
        d23: cython.double = haversine_distance(end1, start2)
        d13: cython.double = haversine_distance(start1, start2)

    elif start1 == start2:
        end1 = latlons1[1]
        end2 = latlons2[1]

        d12: cython.double = haversine_distance(start1, end1)
        d23: cython.double = haversine_distance(end1, end2)
        d13: cython.double = haversine_distance(start1, end2)

    elif start1 == end2:
        end1 = latlons1[1]
        start2 = latlons2[-2]

        d12: cython.double = haversine_distance(start1, end1)
        d23: cython.double = haversine_distance(end1, start2)
        d13: cython.double = haversine_distance(start1, start2)

    else:
        raise Exception('Ways are not connected')

    # law of cosines
    cos_angle = (d12 * d12 + d23 * d23 - d13 * d13) / (2 * d12 * d23)
    angle = _degrees(acos(min(max(cos_angle, -1), 1)))
    return angle


# A U-turn doubles back on itself, so there is no angle between two ways to
# measure - angle_between_ways() would divide by zero. Charge it the most an
# ordinary turn can cost, so a route only turns around where it has to.
U_TURN_ANGLE_DIFFERENCE = 90


def select_neighbors(
    way: FetchRelationElement,
    neighbors: Sequence[GraphKey],
    ways: dict[ElementId, FetchRelationElement],
) -> Sequence[tuple[GraphKey, cython.double]]:
    if not neighbors:
        return ()
    elif len(neighbors) == 1 and neighbors[0].way_id != way.id:
        return ((neighbors[0], 0),)

    # the angle difference from the straight path
    # TODO: support 0-180 range by utilizing is_start
    angle_differences: list[tuple[GraphKey, cython.double]] = []

    for neighbor in neighbors:
        if neighbor.way_id == way.id:
            angle_differences.append((neighbor, U_TURN_ANGLE_DIFFERENCE))
            continue

        angle = angle_between_ways(way.latLngs, ways[neighbor.way_id].latLngs)
        angle_differences.append((neighbor, 90 - abs(90 - angle)))

    return tuple(angle_differences)


def get_bus_stops_at(
    neighbor: GraphKey,
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
) -> tuple[list[SortedBusEntry], list[SortedBusEntry]]:
    neighbor_is_forward = neighbor.is_start

    visited = []
    almost_visited = []

    for sorted_bus in id_sorted_bus_map.get(neighbor.way_id, []):
        if sorted_bus.right_hand_side is None or neighbor_is_forward == sorted_bus.right_hand_side:
            visited.append(sorted_bus)
        else:
            almost_visited.append(sorted_bus)

    if not neighbor_is_forward:
        visited.reverse()
        almost_visited.reverse()

    return visited, almost_visited


def modified_dfs_worker(
    graph: dict[GraphKey, GraphValue],
    ways: dict[ElementId, FetchRelationElement],
    end_way: ElementId,
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
    stack: list[StackElement],
    best_path: BestPathCollection,
    max_length: cython.double,
    max_iter: cython.int,
    components: dict[GraphKey, int],
) -> tuple[list[StackElement], BestPathCollection]:
    message_ref = [f'Worker with {len(stack)} stack size']
    current_iter = 0

    with print_run_time(message_ref):
        for current_iter in range(1, max_iter + 1):  # noqa: B007
            if not stack:
                break

            s = stack.pop()

            current_key = s.path[-1]
            exit_at_key = current_key._replace(is_start=not current_key.is_start)

            current_best_path = BestPath(
                s.path,
                visited_bus_stops=s.visited_bus_stops | s.almost_visited_bus_stops,
                bus_stops_count=len(s.visited_bus_stops),
                almost_bus_stops_count=len(s.almost_visited_bus_stops),
                length=s.length,
                complete_path=s.complete_path,
                complete_length=s.complete_length,
                angle_sum=s.angle_sum,
            )

            if current_key.way_id == end_way:
                if (replace := best_path.valid.select_best(current_best_path)) == current_best_path:
                    best_path = best_path._replace(valid=replace)
            else:
                if (replace := best_path.invalid.select_best(current_best_path)) == current_best_path:
                    best_path = best_path._replace(invalid=replace)

            current_way = ways[current_key.way_id]
            neighbors = graph[exit_at_key].connected_to
            valid_neighbors = select_neighbors(current_way, neighbors, ways)

            # A long route runs out of search time long before the search is exhaustive,
            # so whatever is tried first is what gets returned. The stack pops the last
            # neighbor pushed, so order the likeliest continuation last: a member way not
            # driven yet over one already driven, then a way that can lead back here - a
            # detour off the main road to serve a stop - over one that leaves it behind
            # for good. Taken the other way round, the bus skips the detour and the rest
            # of the budget goes on refining the route beyond it.
            current_component = components[current_key]
            valid_neighbors = sorted(
                valid_neighbors,
                key=lambda n: (
                    n[0].way_id not in s.complete_path,
                    components[n[0]] == current_component,
                ),
            )

            intersection_id = graph[exit_at_key].intersection_id

            if (t := s.intersection_bus_stops_snapshot.get(intersection_id, None)) is not None:
                intersection_bus_stops_count, intersection_visit_count = t
            else:
                intersection_bus_stops_count = None
                intersection_visit_count = 0

            new_intersection_bus_stops_snapshot = s.intersection_bus_stops_snapshot.copy()

            if (intersection_bus_stops_count is None) or (
                intersection_bus_stops_count < len(s.visited_bus_stops) + len(s.almost_visited_bus_stops)
            ):
                new_intersection_visit_count = 1
                new_intersection_bus_stops_snapshot[intersection_id] = (
                    len(s.visited_bus_stops) + len(s.almost_visited_bus_stops),
                    new_intersection_visit_count,
                )
            elif intersection_visit_count < VISITED_LIMIT:
                new_intersection_visit_count = intersection_visit_count + 1
                new_intersection_bus_stops_snapshot[intersection_id] = (
                    intersection_bus_stops_count,
                    new_intersection_visit_count,
                )
            else:
                continue

            for neighbor, neighbor_angle in valid_neighbors:
                neighbor_way = ways[neighbor.way_id]

                new_path = (*s.path, neighbor)

                visited_bus_stops, almost_visited_bus_stops = get_bus_stops_at(neighbor, id_sorted_bus_map)

                if visited_bus_stops or almost_visited_bus_stops:
                    new_visited_bus_stops = s.visited_bus_stops.copy()
                    new_almost_visited_bus_stops = s.almost_visited_bus_stops.copy()

                    for b in visited_bus_stops:
                        new_visited_bus_stops.setdefault(b.bus_stop_collection.best.id, len(new_path))

                    for b in almost_visited_bus_stops:
                        new_almost_visited_bus_stops.setdefault(b.bus_stop_collection.best.id, len(new_path))

                    new_almost_visited_bus_stops = {
                        k: v for k, v in new_almost_visited_bus_stops.items() if k not in new_visited_bus_stops
                    }
                else:
                    new_visited_bus_stops = s.visited_bus_stops
                    new_almost_visited_bus_stops = s.almost_visited_bus_stops

                new_length = s.length + neighbor_way.length

                if new_length > max_length:
                    continue

                if neighbor_way.id not in s.complete_path:
                    new_complete_path = s.complete_path.copy()
                    new_complete_path.add(neighbor_way.id)
                    new_complete_length = s.complete_length + neighbor_way.length
                else:
                    new_complete_path = s.complete_path
                    new_complete_length = s.complete_length

                # roundabout looping and exits are free
                if current_way.roundabout:  # noqa: SIM108
                    new_angle_sum = s.angle_sum
                else:
                    new_angle_sum = s.angle_sum + neighbor_angle

                if new_intersection_visit_count > 1:  # noqa: SIM108
                    new_loop_length = s.loop_length + neighbor_way.length
                else:
                    new_loop_length = 0

                # stop path if too long loop
                if new_loop_length > MAX_LOOP_LENGTH:
                    continue

                if s.after_finish_length > 0 or neighbor.way_id == end_way:
                    new_after_finish_length = s.after_finish_length + neighbor_way.length
                else:
                    new_after_finish_length = 0

                # stop path if too long after finish
                if new_after_finish_length > MAX_AFTER_FINISH_LENGTH:
                    continue

                if neighbor_way.roundabout:
                    if s.roundabout_enter:
                        # stop path if looping in roundabout
                        if s.roundabout_enter == neighbor:
                            continue
                        else:
                            new_roundabout_enter = s.roundabout_enter
                    else:
                        new_roundabout_enter = neighbor
                else:
                    new_roundabout_enter = None

                stack.append(
                    StackElement(
                        path=new_path,
                        visited_bus_stops=new_visited_bus_stops,
                        almost_visited_bus_stops=new_almost_visited_bus_stops,
                        intersection_bus_stops_snapshot=new_intersection_bus_stops_snapshot,
                        length=new_length,
                        complete_path=new_complete_path,
                        complete_length=new_complete_length,
                        angle_sum=new_angle_sum,
                        loop_length=new_loop_length,
                        after_finish_length=new_after_finish_length,
                        roundabout_enter=new_roundabout_enter,
                    )
                )

        message_ref[0] += f' and {current_iter} iterations'

    return stack, best_path


async def modified_dfs(
    graph: dict[GraphKey, GraphValue],
    ways: dict[ElementId, FetchRelationElement],
    start_way: ElementId,
    end_way: ElementId,
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
    executor: ProcessPoolExecutor,
    n_processes: cython.int,
) -> BestPath:
    max_length = MAX_PATH_LENGTH_FACTOR * sum(w.length for w in ways.values())
    components = loop_components(graph)

    start_start_key = GraphKey(start_way, BOOL_START)
    start_end_key = GraphKey(start_way, BOOL_END)

    def init_stack_element(key: GraphKey) -> StackElement:
        intersection_id = graph[key].intersection_id
        visited_bus_stops, almost_visited_bus_stops = get_bus_stops_at(key, id_sorted_bus_map)

        return StackElement(
            path=(key,),
            visited_bus_stops={b.bus_stop_collection.best.id: 1 for b in visited_bus_stops},
            almost_visited_bus_stops={b.bus_stop_collection.best.id: 1 for b in almost_visited_bus_stops},
            intersection_bus_stops_snapshot={
                intersection_id: (len(visited_bus_stops) + len(almost_visited_bus_stops), 1)
            },
            length=ways[start_way].length,
            complete_path={key.way_id},
            complete_length=ways[start_way].length,
        )

    stack: list[StackElement] = [
        init_stack_element(start_start_key),
        init_stack_element(start_end_key),
    ]

    best_path = BestPathCollection(valid=BestPath.zero(), invalid=BestPath.zero())

    # for reference:
    # AMD Ryzen 9 5950X: 10,000 iterations in ~ 0.1s
    sync_max_iter = 3000  # .03s
    async_max_iter = 10000  # .10s

    # run a few iterations synchronously to get a head start
    stack, best_path = modified_dfs_worker(
        graph,
        ways,
        end_way,
        id_sorted_bus_map,
        stack,
        best_path,
        max_length=max_length,
        max_iter=sync_max_iter,
        components=components,
    )

    async def worker(
        stack_slice: list[StackElement],
        best_path: BestPathCollection,
        max_iter: cython.int,
    ) -> tuple[list[StackElement], BestPathCollection]:
        loop = asyncio.get_running_loop()

        return await loop.run_in_executor(
            executor,
            partial(
                modified_dfs_worker,
                graph,
                ways,
                end_way,
                id_sorted_bus_map,
                stack_slice,
                best_path,
                max_length=max_length,
                max_iter=max_iter,
                components=components,
            ),
        )

    deadline = time.monotonic() + MAX_SEARCH_TIME

    tasks: list[asyncio.Task] = []
    while stack or tasks:
        if time.monotonic() >= deadline:
            # in-flight workers are capped at async_max_iter, so they land promptly
            if tasks:
                done, _ = await asyncio.wait(tasks)
                for task in done:
                    _, best_path_slice = task.result()
                    best_path = best_path.merge(best_path_slice, ways)

            print(f'[⏱️] Route search hit its {MAX_SEARCH_TIME}s budget, returning the best route found so far')
            break

        stack_slices_len_target = n_processes - len(tasks)
        stack_slice_size_target, remainder = divmod(len(stack), stack_slices_len_target)
        stack_slices: list[list[StackElement]] = []

        for i in range(stack_slices_len_target):
            current_slice_size = stack_slice_size_target + (1 if i < remainder else 0)
            if current_slice_size == 0:
                break

            stack_slices.append(stack[:current_slice_size])
            stack = stack[current_slice_size:]

        assert not stack, 'Stack must be empty after slicing'

        tasks.extend(
            asyncio.create_task(
                worker(
                    stack_slice,
                    best_path,
                    async_max_iter,
                )
            )
            for stack_slice in stack_slices
        )

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            stack_slice, best_path_slice = task.result()
            stack += stack_slice
            best_path = best_path.merge(best_path_slice, ways)

        tasks = list(pending)

    return best_path.valid if best_path.valid.path else best_path.invalid


def _bus_stop_visits(
    path: Sequence[GraphKey],
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
) -> tuple[dict[ElementId, int], dict[ElementId, int]]:
    """The stops a path serves, and those it only passes on the wrong side, counted as the search counts them."""
    visited: dict[ElementId, int] = {}
    almost_visited: dict[ElementId, int] = {}

    for index, key in enumerate(path, 1):
        visited_bus_stops, almost_visited_bus_stops = get_bus_stops_at(key, id_sorted_bus_map)
        for b in visited_bus_stops:
            visited.setdefault(b.bus_stop_collection.best.id, index)
        for b in almost_visited_bus_stops:
            almost_visited.setdefault(b.bus_stop_collection.best.id, index)

    almost_visited = {k: v for k, v in almost_visited.items() if k not in visited}
    return visited, almost_visited


def drop_redundant_loops(
    best_path: BestPath,
    graph: dict[GraphKey, GraphValue],
    ways: dict[ElementId, FetchRelationElement],
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
) -> BestPath:
    """Cut out any loop the route drives for nothing.

    A search stopped by its time budget can return a route that leaves a point and comes
    back to it without driving a way or serving a stop that it does not also get to
    elsewhere - a lap of a roundabout to reach a turning loop it went straight past the
    first time. The search already ranks the route without that loop as better, it just
    ran out of time before finding it; cutting the loop out gets there regardless.
    """

    def exit_point(key: GraphKey) -> tuple[float, float]:
        lat_lngs = ways[key.way_id].latLngs
        return lat_lngs[-1] if key.is_start else lat_lngs[0]

    path = best_path.path
    way_ids = {key.way_id for key in path}
    visited, almost_visited = _bus_stop_visits(path, id_sorted_bus_map)

    dropped = True
    while dropped:
        dropped = False
        # the index of the way that last brought the route to each point
        last_arrival: dict[tuple[float, float], int] = {}

        for index, key in enumerate(path[:-1]):
            point = exit_point(key)
            loop_start = last_arrival.get(point)
            last_arrival[point] = index
            if loop_start is None:
                continue

            # without the loop, the way after it has to follow straight on from the way before it
            before, after = path[loop_start], path[index + 1]
            if after not in graph[before._replace(is_start=not before.is_start)].connected_to:
                continue

            candidate = path[: loop_start + 1] + path[index + 1 :]
            if {k.way_id for k in candidate} != way_ids:
                continue

            candidate_visited, candidate_almost_visited = _bus_stop_visits(candidate, id_sorted_bus_map)
            if candidate_visited.keys() != visited.keys() or candidate_almost_visited.keys() != almost_visited.keys():
                continue

            path, visited, almost_visited = candidate, candidate_visited, candidate_almost_visited
            dropped = True
            break

    if path is best_path.path:
        return best_path

    return _with_path(best_path, path, ways, id_sorted_bus_map)


def _with_path(
    best_path: BestPath,
    path: tuple[GraphKey, ...],
    ways: dict[ElementId, FetchRelationElement],
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
) -> BestPath:
    """best_path driven along another path, with what it serves and covers counted afresh."""
    visited, almost_visited = _bus_stop_visits(path, id_sorted_bus_map)
    complete_path = {key.way_id for key in path}

    return best_path._replace(
        path=path,
        visited_bus_stops=visited | almost_visited,
        bus_stops_count=len(visited),
        almost_bus_stops_count=len(almost_visited),
        length=sum(ways[key.way_id].length for key in path),
        complete_path=complete_path,
        complete_length=sum(ways[way_id].length for way_id in complete_path),
    )


# The furthest a skipped member way is driven to from the route, and the furthest back.
MAX_DETOUR_LENGTH = 5000  # meters


def _shortest_drives(
    start: GraphKey,
    edges,
    ways: dict[ElementId, FetchRelationElement],
) -> tuple[dict[GraphKey, float], dict[GraphKey, GraphKey]]:
    """Distances from start over way entries, each charged the length of its way, and the step back towards start."""
    distances: dict[GraphKey, float] = {start: 0.0}
    steps_back: dict[GraphKey, GraphKey] = {}
    tie_breaker = count()
    heap = [(0.0, next(tie_breaker), start)]

    while heap:
        distance, _, key = heappop(heap)
        if distance > distances[key]:
            continue

        for neighbor in edges(key):
            neighbor_distance = distance + ways[neighbor.way_id].length
            if neighbor_distance > MAX_DETOUR_LENGTH:
                continue
            if neighbor in distances and distances[neighbor] <= neighbor_distance:
                continue
            distances[neighbor] = neighbor_distance
            steps_back[neighbor] = key
            heappush(heap, (neighbor_distance, next(tie_breaker), neighbor))

    return distances, steps_back


def insert_skipped_detours(
    best_path: BestPath,
    graph: dict[GraphKey, GraphValue],
    ways: dict[ElementId, FetchRelationElement],
    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]],
) -> BestPath:
    """Drive the member ways the route skipped, each on a detour back to where it left.

    A search stopped by its time budget returns the best route it found, and which
    detours made it in comes down to the order it happened to try things in - on the
    Falcon, the loop round Bristol Airport's bus bays or the turning loop at the end of
    Blackbrook Park Avenue, but never both. select_best() ranks a route that drives more
    of the member ways as better, and a detour only adds to a route, so each way still
    missing is put back on the shortest drive that leaves the route and returns to the
    same point. Laps this adds are for drop_redundant_loops() to cut out.
    """

    def exit_at(key: GraphKey) -> GraphKey:
        return key._replace(is_start=not key.is_start)

    def successors(key: GraphKey) -> tuple[GraphKey, ...]:
        return graph[exit_at(key)].connected_to

    predecessors: dict[GraphKey, list[GraphKey]] = defaultdict(list)
    for key in graph:
        for neighbor in successors(key):
            predecessors[neighbor].append(key)

    def preceding(key: GraphKey) -> list[GraphKey]:
        return predecessors.get(key, [])

    path = best_path.path
    unreachable: set[ElementId] = set()

    while True:
        driven = {key.way_id for key in path}
        missing = sorted((way_id for way_id in ways if way_id not in driven and way_id not in unreachable), key=str)
        if not missing:
            break

        way_id = missing[0]
        best: tuple[float, int, list[GraphKey]] | None = None

        for entry in (GraphKey(way_id, BOOL_START), GraphKey(way_id, BOOL_END)):
            to_entry, towards_entry = _shortest_drives(entry, preceding, ways)
            from_entry, back_to_entry = _shortest_drives(entry, successors, ways)
            entry_length = ways[way_id].length

            for index in range(len(path) - 1):
                # leaving after path[index] and rejoining before path[index + 1] meets the route at one point
                leaves = [key for key in successors(path[index]) if key in to_entry]
                rejoins = [key for key in preceding(path[index + 1]) if key in from_entry]
                if not leaves or not rejoins:
                    continue

                leave = min(leaves, key=to_entry.__getitem__)
                rejoin = min(rejoins, key=from_entry.__getitem__)
                length = to_entry[leave] + entry_length + from_entry[rejoin]
                if best is not None and best[0] <= length:
                    continue

                detour = [leave]
                while detour[-1] != entry:
                    detour.append(towards_entry[detour[-1]])
                tail = []
                key = rejoin
                while key != entry:
                    tail.append(key)
                    key = back_to_entry[key]
                detour.extend(reversed(tail))

                best = (length, index, detour)

        if best is None:
            unreachable.add(way_id)
            continue

        _, index, detour = best
        path = (*path[: index + 1], *detour, *path[index + 1 :])

    if path is best_path.path:
        return best_path

    return _with_path(best_path, path, ways, id_sorted_bus_map)


def finalize_route(
    best_path: BestPath,
    ways: dict[ElementId, FetchRelationElement],
    bus_stop_collections: Sequence[FetchRelationBusStopCollection],
    tags: dict[str, str],
) -> FinalRoute:
    route_ways = tuple(
        FinalRouteWay(
            way=ways[key.way_id],
            reversed_latLngs=not key.is_start,
        )
        for key in best_path.path
    )

    route_latlons_gen = (
        route_way.way.latLngs[::-1] if route_way.reversed_latLngs else route_way.way.latLngs for route_way in route_ways
    )

    route_latlons = tuple(
        chain.from_iterable(latlons if i == 0 else latlons[1:] for i, latlons in enumerate(route_latlons_gen))
    )

    route_latlons_set = set(route_latlons)

    id_collection_map = {collection.best.id: collection for collection in bus_stop_collections}

    route_bus_stops = []

    for stop_id, _ in sorted(best_path.visited_bus_stops.items(), key=lambda x: x[1]):
        collection = id_collection_map[stop_id]

        if collection.stop is not None and collection.stop.latLng not in route_latlons_set:
            collection = replace(collection, stop=None)

        if collection.platform is None and collection.stop is None:
            continue

        route_bus_stops.append(collection)

    return FinalRoute(
        ways=route_ways,
        latLngs=route_latlons,
        busStops=tuple(route_bus_stops),
        tags=tags,
    )


async def calc_bus_route(
    ways_members: dict[ElementId, FetchRelationElement],
    start_way: ElementId,
    end_way: ElementId,
    bus_stop_collections: Sequence[FetchRelationBusStopCollection],
    tags: dict[str, str],
    executor: ProcessPoolExecutor,
    n_processes: cython.int,
) -> FinalRoute:
    with print_run_time('Sorting bus stops'):
        sorted_buses = sort_bus_on_path(bus_stop_collections, ways_members.values())

    id_sorted_bus_map: dict[ElementId, list[SortedBusEntry]] = {}

    for sorted_bus in sorted_buses:
        id_sorted_bus_map.setdefault(sorted_bus.neighbor_id, []).append(sorted_bus)

    with print_run_time('Building graph'):
        graph = build_graph(ways_members)

    with print_run_time('Calculating route'):
        best_path = await modified_dfs(
            graph,
            ways_members,
            start_way,
            end_way,
            id_sorted_bus_map,
            executor,
            n_processes,
        )

    with print_run_time('Inserting skipped detours'):
        best_path = insert_skipped_detours(best_path, graph, ways_members, id_sorted_bus_map)

    with print_run_time('Dropping redundant loops'):
        best_path = drop_redundant_loops(best_path, graph, ways_members, id_sorted_bus_map)

    return finalize_route(best_path, ways_members, bus_stop_collections, tags)
