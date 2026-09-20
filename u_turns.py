from collections.abc import Mapping, Sequence


def relation_u_turn_nodes(
    member_way_ids: Sequence[int],
    way_nodes: Mapping[int, Sequence[int]],
) -> set[int]:
    """
    The nodes where a relation already says the bus turned around.

    A way listed twice in a row was driven out and back, and the only way to do that is to
    turn around at the far end of it. Nothing in OSM need say so: a turning circle or a
    mini roundabout is found from the map, but a bus that reverses into a side road leaves
    no tag behind at all. Without this, reloading such a route cannot get past the turn and
    reports every way beyond it as unused.

    Which end the turn is at is told by the neighbours: the bus came in, and left again, by
    the end the way shares with the members either side of it, so the turn is at the other.
    When that cannot be told apart - a member that loops back on itself, or a relation whose
    ways do not meet - either end may be the one, and both are offered.
    """
    result: set[int] = set()

    for i, way_id in enumerate(member_way_ids):
        if i == 0 or member_way_ids[i - 1] != way_id:
            continue

        nodes = way_nodes.get(way_id)
        if not nodes:
            continue

        ends = (nodes[0], nodes[-1])
        neighbour_nodes = {
            node
            for neighbour_id in (member_way_ids[i - 2] if i >= 2 else None, _after(member_way_ids, i))
            for node in way_nodes.get(neighbour_id, ())
        }

        turn = tuple(node for node in ends if node not in neighbour_nodes)
        result.update(turn if len(turn) == 1 else ends)

    return result


def _after(member_way_ids: Sequence[int], i: int) -> int | None:
    """The first member past this one that is a different way."""
    for way_id in member_way_ids[i + 1 :]:
        if way_id != member_way_ids[i]:
            return way_id

    return None
