from cython_lib.route import BOOL_END, BOOL_START, GraphKey, build_graph
from models.element_id import ElementId
from models.fetch_relation import FetchRelationElement

JUNCTION = (51.0, -1.0)
MAIN_FAR_END = (51.0, -1.001)
STUB_DEAD_END = (51.001, -1.0)

MAIN_ID = ElementId('1')
STUB_ID = ElementId('2')


def _way(id: ElementId, lat_lngs, connected_to, *, oneway=False, turn_start=False, turn_end=False):
    return FetchRelationElement(
        id=id,
        member=True,
        oneway=oneway,
        roundabout=False,
        nodes=list(range(len(lat_lngs))),
        latLngs=lat_lngs,
        connectedTo=connected_to,
        turn_in_place_start=turn_start,
        turn_in_place_end=turn_end,
    )


def _cul_de_sac(**stub_kwargs):
    """A main road meeting a stub whose far end is a dead end."""
    main = _way(MAIN_ID, [MAIN_FAR_END, JUNCTION], [STUB_ID])
    stub = _way(STUB_ID, [JUNCTION, STUB_DEAD_END], [MAIN_ID], **stub_kwargs)
    return build_graph({main.id: main, stub.id: stub})


def test_dead_end_without_turn_in_place_is_a_sink():
    graph = _cul_de_sac()

    # nothing to continue to, so the stub cannot be part of a route and ends up unused
    assert graph[GraphKey(STUB_ID, BOOL_END)].connected_to == ()


def test_turn_in_place_end_connects_the_dead_end_back():
    graph = _cul_de_sac(turn_end=True)

    # the bus may turn around and come back out the way it came in
    assert graph[GraphKey(STUB_ID, BOOL_END)].connected_to == (GraphKey(MAIN_ID, BOOL_END),)


def test_turn_in_place_start_is_ignored_on_a_oneway():
    graph = _cul_de_sac(oneway=True, turn_start=True)

    # reaching the entry end of a oneway to turn there would mean driving it backwards
    assert graph[GraphKey(STUB_ID, BOOL_START)].connected_to == (GraphKey(MAIN_ID, BOOL_END),)


def test_turn_in_place_on_a_through_way_teleports_between_junctions():
    """Why the UI only offers a U-turn at a dead end."""
    junction_a = (51.0, 0.0)
    junction_b = (51.0, 0.01)

    before = _way(ElementId('V'), [(51.0, -0.01), junction_a], [ElementId('W')])
    through = _way(ElementId('W'), [junction_a, junction_b], [ElementId('V'), ElementId('X')], turn_end=True)
    after = _way(ElementId('X'), [junction_b, (51.0, 0.02)], [ElementId('W')])

    graph = build_graph({w.id: w for w in (before, through, after)})

    # the far end gains an edge to a way at the *near* junction, which is not a
    # turn but a jump between two different places
    assert graph[GraphKey(ElementId('W'), BOOL_END)].connected_to == (
        GraphKey(ElementId('X'), BOOL_START),
        GraphKey(ElementId('V'), BOOL_END),
    )
