// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    addNewStop,
    clearNewStops,
    newStopCollections,
    newStopsPayload,
    moveNewStop,
    updateNewStop,
} from "../../static/js/busStopsNew.js"
import {
    insertStopPositionsIntoWays,
    planStopPosition,
    stopPositionPlacements,
    stopPositionsPayload,
} from "../../static/js/stopPositions.js"

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
    const result = insertStopPositionsIntoWays(WAYS, stopPositionPlacements())

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
    assert.deepEqual(stopPositionsPayload(), [])
})

test("the payload names the way and the two nodes to go between", () => {
    addStop()

    const [position] = stopPositionsPayload()
    assert.equal(position.wayId, 201)
    assert.equal(position.afterNode, 10)
    assert.equal(position.beforeNode, 11)
    assert.equal(position.name, "High Street")
    assert.ok(position.id < 0)
})

test("the platform payload no longer carries the stop position", () => {
    addStop()

    // stop positions are sent on their own, as a stop already in OSM can have one too
    assert.equal(newStopsPayload()[0].stopPosition, undefined)
})

test("dragging the stop moves its stop position without changing its id", () => {
    const stop = addStop()
    const before = newStopCollections()[0].stop.id

    const moved = [51.50009, 0.001]
    moveNewStop(stop, moved, planStopPosition(moved, WAYS))

    assert.equal(newStopCollections()[0].stop.id, before, "the route still refers to it")
    assert.equal(stopPositionsPayload()[0].afterNode, 11, "and it followed along the road")
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
