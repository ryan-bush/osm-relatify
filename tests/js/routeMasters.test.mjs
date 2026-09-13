// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    currentRouteMasters,
    describeMaster,
    describeRoute,
    mismatchesOf,
    routeMasterCandidates,
    routeMastersKnown,
    routeValue,
    setRouteMasters,
    undescribedMemberCount,
} from "../../static/js/routeMasters.js"

const master = (over = {}) => ({
    id: 100,
    tags: {
        type: "route_master",
        route_master: "bus",
        ref: "71",
        name: "Bus 71",
    },
    members: ["relation/1", "relation/2"],
    routes: [
        { id: 1, ref: "71", name: "Bus 71: Oxford => Witney" },
        { id: 2, ref: "71", name: "Bus 71: Witney => Oxford" },
    ],
    ...over,
})

const routeTags = { type: "route", route: "bus", ref: "71" }

beforeEach(() => {
    setRouteMasters({ routeMasters: [], routeMasterCandidates: [] })
})

test("a download reports what the route is in and what it could join", () => {
    setRouteMasters({
        routeMasters: [master()],
        routeMasterCandidates: [master({ id: 101 })],
    })

    assert.equal(routeMastersKnown(), true)
    assert.deepEqual(
        currentRouteMasters().map((m) => m.id),
        [100],
    )
    assert.deepEqual(
        routeMasterCandidates().map((m) => m.id),
        [101],
    )
})

// An empty list would read as "not in a route master", which is what invites linking the
// route into a second one beside the master it already belongs to.
test("a lookup that could not answer is not an answer of none", () => {
    setRouteMasters({ routeMasters: null, routeMasterCandidates: null })

    assert.equal(routeMastersKnown(), false)
    assert.deepEqual(currentRouteMasters(), [])
    assert.deepEqual(routeMasterCandidates(), [])
})

test("unloading forgets what was found", () => {
    setRouteMasters({
        routeMasters: [master()],
        routeMasterCandidates: [master({ id: 101 })],
    })
    setRouteMasters(null)

    assert.deepEqual(currentRouteMasters(), [])
    assert.deepEqual(routeMasterCandidates(), [])
})

test("a master is named after its name, its ref, or neither", () => {
    assert.equal(describeMaster(master()), "Bus 71")
    assert.equal(
        describeMaster(master({ tags: { ref: "71" } })),
        "Route master 71",
    )
    assert.equal(describeMaster(master({ tags: {} })), "Unnamed route master")
    assert.equal(
        describeMaster(master({ tags: { name: "  Bus 71  " } })),
        "Bus 71",
    )
})

test("a variant is named by its ref and name together", () => {
    assert.equal(
        describeRoute({ id: 1, ref: "71", name: "Oxford => Witney" }),
        "71 Oxford => Witney",
    )
    assert.equal(
        describeRoute({ id: 1, ref: "", name: "Oxford => Witney" }),
        "Oxford => Witney",
    )
    assert.equal(describeRoute({ id: 1, ref: "", name: "" }), "Relation 1")
})

// the type tag names the tag carrying the kind, so a disused route reads its own
test("a route's kind is read from the tag its type names", () => {
    assert.equal(routeValue({ type: "route", route: "bus" }), "bus")
    assert.equal(
        routeValue({
            type: "disused:route",
            "disused:route": "tram",
            route: "bus",
        }),
        "tram",
    )
    assert.equal(routeValue({}), "")
})

test("a master that agrees with its route has nothing to say", () => {
    assert.deepEqual(mismatchesOf(master(), routeTags), [])
})

test("a master with a different ref is worth pointing out", () => {
    const found = mismatchesOf(
        master({ tags: { route_master: "bus", ref: "72" } }),
        routeTags,
    )

    assert.equal(found.length, 1)
    assert.match(found[0], /72/)
    assert.match(found[0], /71/)
})

test("a master of another kind of route is worth pointing out", () => {
    const found = mismatchesOf(
        master({ tags: { route_master: "tram", ref: "71" } }),
        routeTags,
    )

    assert.equal(found.length, 1)
    assert.match(found[0], /tram/)
})

// a trolleybus route belongs in a trolleybus master, not a bus one
test("a trolleybus route is not mistaken for a bus one", () => {
    const trolleybus = { type: "route", route: "trolleybus", ref: "71" }

    assert.deepEqual(
        mismatchesOf(
            master({ tags: { route_master: "trolleybus", ref: "71" } }),
            trolleybus,
        ),
        [],
    )
    assert.equal(mismatchesOf(master(), trolleybus).length, 1)
})

test("nothing is claimed about a master or route that does not say", () => {
    assert.deepEqual(mismatchesOf(master({ tags: {} }), routeTags), [])
    assert.deepEqual(mismatchesOf(master(), { type: "route" }), [])
})

test("members that were not described are counted, not invented", () => {
    assert.equal(undescribedMemberCount(master()), 0)
    // a master holding a way as well as its two routes
    assert.equal(
        undescribedMemberCount(
            master({ members: ["relation/1", "relation/2", "way/9"] }),
        ),
        1,
    )
    // one too large to have been expanded at all
    assert.equal(undescribedMemberCount(master({ routes: [] })), 2)
})
