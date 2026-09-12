// Run with: node --test "tests/js/*.mjs"
// The Python suite cannot reach this: working out where on the road a new stop halts,
// and putting that point into the way, both happen in the browser.
import assert from "node:assert/strict"
import { test } from "node:test"

import { insertStopPositionsIntoWays, planStopPosition } from "../../static/js/stopPositions.js"

// a straight west-east road at lat 51.5, where 0.0007 deg of longitude is about 48 m
const road = (over = {}) => ({
    id: "201",
    member: true,
    nodes: [10, 11, 12],
    latLngs: [
        [51.5, 0],
        [51.5, 0.0007],
        [51.5, 0.0014],
    ],
    ...over,
})

const ways = (...list) => Object.fromEntries(list.map((way) => [way.id, way]))

const metresEast = (lon, from) => Math.abs(lon - from) * 111_320 * Math.cos((51.5 * Math.PI) / 180)

test("projects the stop onto the segment it sits beside", () => {
    const placement = planStopPosition([51.50009, 0.0003], ways(road()))

    assert.equal(placement.wayId, 201)
    assert.equal(placement.afterNode, 10)
    assert.equal(placement.beforeNode, 11)
    assert.ok(Math.abs(placement.latLng[0] - 51.5) < 1e-9, "sits on the road")
    assert.ok(Math.abs(placement.latLng[1] - 0.0003) < 1e-6, "level with the stop")
    assert.ok(placement.distance > 9 && placement.distance < 11, "about 10 m from the road")
})

test("picks the later segment for a stop further along", () => {
    const placement = planStopPosition([51.50009, 0.001], ways(road()))

    assert.equal(placement.afterNode, 11)
    assert.equal(placement.beforeNode, 12)
})

test("offers nothing when the road is too far away", () => {
    assert.equal(planStopPosition([51.5009, 0.0003], ways(road())), null)
})

test("ignores ways the route does not use", () => {
    assert.equal(planStopPosition([51.50009, 0.0003], ways(road({ member: false }))), null)
})

test("ignores ways the route splits, whose nodes are rebuilt on upload", () => {
    assert.equal(planStopPosition([51.50009, 0.0003], ways(road({ id: "201_1_2" }))), null)
})

test("keeps clear of a node the way already has", () => {
    // level with node 11, which the new node would otherwise land exactly on top of
    const placement = planStopPosition([51.50009, 0.0007], ways(road()))

    assert.ok(metresEast(placement.latLng[1], 0.0007) >= 0.49, "at least half a metre clear")
})

test("takes the nearer of two roads", () => {
    const far = {
        id: "202",
        member: true,
        nodes: [20, 21],
        latLngs: [
            [51.5002, 0],
            [51.5002, 0.0014],
        ],
    }

    assert.equal(planStopPosition([51.50009, 0.0003], ways(road(), far)).wayId, 201)
})

test("puts the stop position into the way sent for calculation", () => {
    const waysData = ways(road())
    const placement = planStopPosition([51.50009, 0.0003], waysData)
    const result = insertStopPositionsIntoWays(waysData, [placement])

    assert.equal(result["201"].latLngs.length, 4)
    assert.deepEqual(result["201"].latLngs[1], placement.latLng)
    // the originals are untouched, so later placements still see what OSM has
    assert.equal(waysData["201"].latLngs.length, 3)
})

test("orders two stop positions that share a segment", () => {
    const waysData = ways(road())
    const near = planStopPosition([51.50009, 0.0002], waysData)
    const far = planStopPosition([51.50009, 0.0005], waysData)
    const result = insertStopPositionsIntoWays(waysData, [far, near])

    assert.deepEqual(result["201"].latLngs[1], near.latLng)
    assert.deepEqual(result["201"].latLngs[2], far.latLng)
})

test("drops a stop position whose way is no longer there", () => {
    const placement = planStopPosition([51.50009, 0.0003], ways(road()))
    const result = insertStopPositionsIntoWays(ways(road({ id: "999" })), [placement])

    assert.equal(result["999"].latLngs.length, 3)
})

test("leaves the ways alone when no stop position is placed", () => {
    const waysData = ways(road())

    assert.equal(insertStopPositionsIntoWays(waysData, []), waysData)
})
