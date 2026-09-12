// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    addTagAddition,
    clearTagAdditions,
    getDecision,
    hasUndecided,
    setDecision,
    tagAdditionsPayload,
    tagChangeCount,
    undecidedKeys,
} from "../../static/js/naptanTagAdditions.js"

const stop = (over = {}) => ({
    type: "node",
    id: "42",
    tags: { name: "High St", naptan: "x" },
    ...over,
})

const DIFFERING = { name: "High Street" }

beforeEach(clearTagAdditions)

test("a disagreement is undecided until it is answered", () => {
    const osmStop = stop()

    assert.deepEqual(undecidedKeys(osmStop, DIFFERING), ["name"])
    assert.ok(hasUndecided(osmStop, DIFFERING))

    setDecision(osmStop, "name", "High Street", "naptan")

    assert.deepEqual(undecidedKeys(osmStop, DIFFERING), [])
    assert.equal(hasUndecided(osmStop, DIFFERING), false)
})

test("a stop with nothing to decide is never undecided", () => {
    assert.equal(hasUndecided(stop(), {}), false)
    assert.equal(hasUndecided(stop(), undefined), false)
})

test("taking NaPTAN's value sends it, with the value it replaces", () => {
    setDecision(stop(), "name", "High Street", "naptan")

    assert.deepEqual(tagAdditionsPayload(), [
        { type: "node", id: 42, tags: { name: "High Street" }, expected: { name: "High St" } },
    ])
})

test("keeping the stop's value changes nothing in OSM", () => {
    const osmStop = stop()
    setDecision(osmStop, "name", "High Street", "osm")

    assert.equal(getDecision(osmStop, "name", "High Street"), "osm")
    assert.equal(hasUndecided(osmStop, DIFFERING), false)
    assert.deepEqual(tagAdditionsPayload(), [], "decided, but nothing to write")
})

test("a decision does not carry over to a different NaPTAN value", () => {
    const osmStop = stop()
    setDecision(osmStop, "name", "High Street", "osm")

    // NaPTAN changed its mind, so the mapper is asked again
    assert.ok(hasUndecided(osmStop, { name: "Market Square" }))
})

test("a fill and an accepted replacement are one change to one stop", () => {
    const osmStop = stop()
    addTagAddition(osmStop, { "naptan:Bearing": "NE" })
    setDecision(osmStop, "name", "High Street", "naptan")

    const payload = tagAdditionsPayload()
    assert.equal(payload.length, 1)
    assert.deepEqual(payload[0].tags, { "naptan:Bearing": "NE", name: "High Street" })
    // the fill expects no value to be there; the replacement expects the old one
    assert.deepEqual(payload[0].expected, { name: "High St" })
    assert.equal(tagChangeCount(), 1)
})

test("two stops are counted and sent separately", () => {
    setDecision(stop({ id: "42" }), "name", "High Street", "naptan")
    setDecision(stop({ id: "43" }), "name", "Low Street", "naptan")

    assert.equal(tagChangeCount(), 2)
})

test("a decision on a stop with no such tag expects nothing", () => {
    setDecision(stop({ tags: {} }), "name", "High Street", "naptan")

    assert.deepEqual(tagAdditionsPayload()[0].expected, { name: "" })
})
