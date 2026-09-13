import asyncio

import main
from models.bounding_box import BoundingBox

BOUNDS = BoundingBox(51.0, -1.5, 51.5, -1.0)
ROUTE_TAGS = {'type': 'route', 'route': 'bus', 'public_transport:version': '2', 'ref': '71'}


def _master(id=100, members=(('relation', 1),)):
    return {
        'type': 'relation',
        'id': id,
        'tags': {'type': 'route_master', 'route_master': 'bus', 'ref': '71'},
        'members': [{'type': t, 'ref': r, 'role': ''} for t, r in members],
    }


class FakeOsm:
    """Stands in for the OSM API, answering what a route belongs to and what holds it."""

    def __init__(self, parents=None, relations=(), fail_parents=False, fail_relations=False):
        self._parents = parents if parents is not None else []
        self._relations = list(relations)
        self._fail_parents = fail_parents
        self._fail_relations = fail_relations
        self.requested_relations = None

    async def get_parent_relations(self, element_type, element_id):  # noqa: ARG002
        if self._fail_parents:
            raise RuntimeError('OSM is down')
        return self._parents

    async def get_relations(self, relation_ids, json: bool = True):  # noqa: ARG002
        self.requested_relations = tuple(relation_ids)
        if self._fail_relations:
            raise RuntimeError('OSM is down')
        return self._relations


class FakeOverpass:
    def __init__(self, candidates=(), fail=False):
        self._candidates = list(candidates)
        self._fail = fail
        self.called_with = None

    async def query_route_master_candidates(self, ref, route_value, bounds):
        self.called_with = (ref, route_value, bounds)
        if self._fail:
            raise RuntimeError('Overpass is down')
        from route_masters import parse_route_masters

        return parse_route_masters(self._candidates)


def _query(osm, overpass, relation_id=5, tags=None):
    """Runs the lookup against stand-ins for OSM and Overpass."""
    original = (main._OSM, main._OVERPASS)
    main._OSM, main._OVERPASS = osm, overpass
    try:
        return asyncio.run(main._query_route_masters(relation_id, tags or ROUTE_TAGS, BOUNDS))
    finally:
        main._OSM, main._OVERPASS = original


def test_reports_the_master_the_route_is_already_in():
    osm = FakeOsm(parents=[_master()], relations=[{'type': 'relation', 'id': 1, 'tags': {'name': 'Bus 71: A => B'}}])

    current, candidates = _query(osm, FakeOverpass())

    assert [master.id for master in current] == [100]
    assert [route.name for route in current[0].routes] == ['Bus 71: A => B']
    assert candidates == []


def test_a_parent_that_is_not_a_route_master_is_not_one():
    # a route can also be a member of, say, a network relation
    parent = {'type': 'relation', 'id': 200, 'tags': {'type': 'network'}, 'members': []}

    current, _ = _query(FakeOsm(parents=[parent]), FakeOverpass())

    assert current == []


def test_the_master_the_route_is_in_is_not_offered_as_one_to_join():
    # the route itself matches the candidate query, so its own master comes back with them
    current, candidates = _query(
        FakeOsm(parents=[_master()]),
        FakeOverpass(candidates=[_master(), _master(id=101)]),
    )

    assert [master.id for master in current] == [100]
    assert [master.id for master in candidates] == [101]


def test_candidates_are_searched_for_by_ref_and_route_kind():
    overpass = FakeOverpass()

    _query(FakeOsm(), overpass, tags={**ROUTE_TAGS, 'route': 'trolleybus'})

    assert overpass.called_with == ('71', 'trolleybus', BOUNDS)


def test_a_relation_being_created_is_in_nothing_but_can_still_join_one():
    current, candidates = _query(FakeOsm(), FakeOverpass(candidates=[_master()]), relation_id=None)

    assert current == []
    assert [master.id for master in candidates] == [100]


def test_nothing_is_offered_when_osm_cannot_say_what_the_route_is_in():
    # an empty list would read as "not in a master", which is what invites adding a second
    result = _query(FakeOsm(fail_parents=True), FakeOverpass(candidates=[_master()]))

    assert result == (None, None)


def test_a_failed_candidate_lookup_still_reports_the_current_master():
    current, candidates = _query(FakeOsm(parents=[_master()]), FakeOverpass(fail=True))

    assert [master.id for master in current] == [100]
    assert candidates is None


def test_masters_are_still_reported_when_their_variants_cannot_be_looked_up():
    current, _ = _query(FakeOsm(parents=[_master()], fail_relations=True), FakeOverpass())

    assert [master.id for master in current] == [100]
    assert current[0].routes == []


def test_variants_of_current_and_candidate_masters_are_looked_up_together():
    osm = FakeOsm(parents=[_master(members=(('relation', 1),))])

    _query(osm, FakeOverpass(candidates=[_master(id=101, members=(('relation', 2),))]))

    assert osm.requested_relations == (1, 2)


def test_an_implausibly_large_master_is_not_expanded(monkeypatch):
    monkeypatch.setattr(main, 'MAX_DESCRIBED_ROUTES', 2)
    osm = FakeOsm(parents=[_master(members=(('relation', 1), ('relation', 2), ('relation', 3)))])

    current, _ = _query(osm, FakeOverpass())

    assert osm.requested_relations is None
    assert current[0].routes == []
