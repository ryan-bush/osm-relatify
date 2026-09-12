// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    addNewStop,
    clearNewStops,
    moveNewStop,
    newStopCollections,
    newStopPlacements,
    newStopsPayload,
    updateNewStop,
} from "../../static/js/busStopsNew.js"
import { insertStopPositionsIntoWays, planStopPosition } from "../../static/js/stopPositions.js"

const WAYS = {
    "201": {
        id: "201",
        member: true,
        nodes: [10, 11, 12],
        latLngs: [
            [51.5, 0],
            [51.5, 0.0007],
            [51.5, 0.0014],
        ],
    },
}

const BESIDE_THE_ROAD = [51.50009, 0.0003]

const addStop = (latLng = BESIDE_THE_ROAD, tags = { name: "High Street" }) =>
    addNewStop(latLng, tags, planStopPosition(latLng, WAYS))

beforeEach(clearNewStops)

test("the point put into the way is the one the stop position node has", () => {
    addStop()

    const { stop } = newStopCollections()[0]
    const result = insertStopPositionsIntoWays(WAYS, newStopPlacements())

    // the route calculation keeps a stop position only when it finds it on the route,
    // which it does by looking for this exact point
    assert.deepEqual(result["201"].latLngs[1], stop.latLng)
})

test("the platform and the stop position get their own placeholder ids", () => {
    addStop()

    const { platform, stop } = newStopCollections()[0]
    assert.notEqual(platform.id, stop.id)
    assert.ok(Number.parseInt(stop.id, 10) < 0)
})

test("a stop placed away from the route has no stop position", () => {
    addNewStop(BESIDE_THE_ROAD, { name: "High Street" }, null)

    assert.equal(newStopCollections()[0].stop, null)
    assert.equal(newStopsPayload()[0].stopPosition, null)
})

test("the payload names the way and the two nodes to go between", () => {
    addStop()

    const { stopPosition } = newStopsPayload()[0]
    assert.equal(stopPosition.wayId, 201)
    assert.equal(stopPosition.afterNode, 10)
    assert.equal(stopPosition.beforeNode, 11)
    assert.ok(stopPosition.id < 0)
})

test("dragging the stop moves its stop position without changing its id", () => {
    const stop = addStop()
    const before = newStopCollections()[0].stop.id

    const moved = [51.50009, 0.001]
    moveNewStop(stop, moved, planStopPosition(moved, WAYS))

    assert.equal(newStopCollections()[0].stop.id, before, "the route still refers to it")
    assert.equal(newStopsPayload()[0].stopPosition.afterNode, 11, "and it followed along the road")
})

test("unticking the box takes the stop position away again", () => {
    const stop = addStop()
    updateNewStop(stop, { name: "High Street" }, null)

    assert.equal(newStopCollections()[0].stop, null)
})

test("renaming the stop renames its stop position too", () => {
    const stop = addStop()
    updateNewStop(stop, { name: "Low Street" }, planStopPosition(BESIDE_THE_ROAD, WAYS))

    assert.equal(newStopCollections()[0].stop.name, "Low Street")
})
