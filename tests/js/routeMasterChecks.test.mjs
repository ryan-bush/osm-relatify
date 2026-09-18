// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { test } from "node:test"

import { routeMasterIssues } from "../../static/js/routeMasterChecks.js"

const MASTER = {
    type: "route_master",
    route_master: "bus",
    ref: "9",
    name: "Bus 9",
}

const route = (id, tags = {}) => ({
    id,
    tags: {
        type: "route",
        route: "bus",
        ref: "9",
        from: "A",
        to: "B",
        ...tags,
    },
})

const messages = (masterTags, routes) =>
    routeMasterIssues(masterTags, routes).map((issue) => issue.message)

test("a line whose variants agree has nothing to say", () => {
    assert.deepEqual(messages(MASTER, [route(1), route(2)]), [])
})

test("a variant with a different ref is named", () => {
    const found = routeMasterIssues(MASTER, [route(1), route(2, { ref: "92" })])

    assert.equal(found.length, 1)
    assert.match(found[0].message, /#2/)
    assert.match(found[0].message, /9/)
    assert.deepEqual(found[0].routes, [2])
})

test("several variants with the wrong ref are named together", () => {
    const [issue] = routeMasterIssues(MASTER, [
        route(1, { ref: "92" }),
        route(2, { ref: "93" }),
    ])

    assert.match(issue.message, /#1, #2/)
    assert.deepEqual(issue.routes, [1, 2])
})

test("a variant with no ref at all is its own thing to say", () => {
    const found = messages(MASTER, [route(1), route(2, { ref: "" })])

    assert.equal(found.length, 1)
    assert.match(found[0], /no ref/)
})

test("a variant of another kind is named", () => {
    const [issue] = routeMasterIssues(MASTER, [
        route(1),
        route(2, { route: "tram" }),
    ])

    assert.match(issue.message, /is not a bus route/)
    assert.deepEqual(issue.routes, [2])
})

// the type tag names the tag carrying the kind
test("a disused route's kind is read from its own tag", () => {
    const disused = {
        id: 2,
        tags: {
            type: "disused:route",
            "disused:route": "bus",
            ref: "9",
            from: "A",
            to: "B",
        },
    }

    assert.deepEqual(messages(MASTER, [route(1), disused]), [])
})

test("a variant that contradicts the master's operator is named", () => {
    const master = { ...MASTER, operator: "Alpha" }
    const [issue] = routeMasterIssues(master, [
        route(1, { operator: "Alpha" }),
        route(2, { operator: "Beta" }),
    ])

    assert.match(issue.message, /different operator from the master \(Alpha\)/)
    assert.deepEqual(issue.routes, [2])
})

// a master that says nothing about a tag is not something its variants can contradict
test("variants are only measured against what the master states", () => {
    assert.deepEqual(
        messages(MASTER, [
            route(1, { operator: "Alpha" }),
            route(2, { operator: "Alpha" }),
        ]),
        [],
    )
})

test("but variants disagreeing among themselves is worth saying", () => {
    const [issue] = routeMasterIssues(MASTER, [
        route(1, { operator: "Alpha" }),
        route(2, { operator: "Beta" }),
    ])

    assert.match(issue.message, /2 different values for operator/)
    assert.deepEqual(issue.routes, [1, 2])
})

test("one variant stating a tag the others leave out is not a disagreement", () => {
    assert.deepEqual(
        messages(MASTER, [route(1, { colour: "red" }), route(2)]),
        [],
    )
})

test("a variant missing where it starts or ends is named", () => {
    const found = messages(MASTER, [route(1), route(2, { to: "" })])

    assert.equal(found.length, 1)
    assert.match(found[0], /does not say where it starts or ends/)
})

test("nothing is claimed about a master that states nothing", () => {
    assert.deepEqual(messages({}, [route(1), route(2)]), [])
})

test("an empty master is nothing to report on", () => {
    assert.deepEqual(messages(MASTER, []), [])
})

test("every kind of disagreement is reported at once", () => {
    const found = messages({ ...MASTER, operator: "Alpha" }, [
        route(1, { ref: "92" }),
        route(2, { operator: "Beta", to: "" }),
    ])

    assert.equal(found.length, 3, found.join(" | "))
})

// A master may list a member OSM never described: one deleted since, one beyond what is
// worth expanding, or one whose lookup failed. Nothing is known about it either way.
test("a member the lookup did not describe is not a variant with faults", () => {
    const undescribed = { id: 3, tags: {}, described: false }

    assert.deepEqual(messages(MASTER, [route(1), undescribed]), [])
})

test("the described variants are still checked alongside it", () => {
    const undescribed = { id: 3, tags: {}, described: false }
    const found = messages(MASTER, [route(1), route(2, { ref: "92" }), undescribed])

    assert.equal(found.length, 1)
    assert.match(found[0], /#2/)
})
