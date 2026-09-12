// Run with: node --test "tests/js/*.mjs"
// The Python suite cannot reach this: working out where on the road a new stop halts,
// and putting that point into the way, both happen in the browser.
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    clearStopPositions,
    getStopPositionNode,
    hasStopPosition,
    insertStopPositionsIntoWays,
    planStopPosition,
    removeStopPosition,
    renameStopPosition,
    setStopPosition,
    stopPositionCount,
    stopPositionsPayload,
} from "../../static/js/stopPositions.js"

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

// Ways come cut up at every intersection, so a plain id is the exception, not the rule.
// The second piece of the road, carrying on east from node 12.
const secondPiece = (over = {}) => ({
    id: "201_2_2",
    member: true,
    nodes: [12, 13],
    latLngs: [
        [51.5, 0.0014],
        [51.5, 0.0021],
    ],
    ...over,
})

test("places on a piece of a way the route uses the whole of", () => {
    const placement = planStopPosition([51.50009, 0.0003], ways(road({ id: "201_1_2" }), secondPiece()))

    // the upload merges the pieces back, so the node goes into the real way
    assert.equal(placement.wayId, 201)
    assert.equal(placement.segmentId, "201_1_2")
    assert.equal(placement.afterNode, 10)
    assert.equal(placement.beforeNode, 11)
})

test("refuses a way the route really splits, whose nodes are rebuilt on upload", () => {
    // the route leaves the road part way along, so only the first piece is a member
    const waysData = ways(road({ id: "201_1_2" }), secondPiece({ member: false }))

    assert.equal(planStopPosition([51.50009, 0.0003], waysData), null)
})

test("refuses a way whose other pieces were never downloaded", () => {
    // one piece of three, so the upload cannot put the way back together
    assert.equal(planStopPosition([51.50009, 0.0003], ways(road({ id: "201_1_3" }))), null)
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

test("puts the stop position into the piece sent for calculation", () => {
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

test("puts the vertex into the right piece of a cut-up way", () => {
    const waysData = ways(road({ id: "201_1_2" }), secondPiece())
    const placement = planStopPosition([51.50009, 0.0003], waysData)
    const result = insertStopPositionsIntoWays(waysData, [placement])

    assert.equal(result["201_1_2"].latLngs.length, 4)
    assert.deepEqual(result["201_1_2"].latLngs[1], placement.latLng)
    assert.equal(result["201_2_2"].latLngs.length, 2, "the other piece is untouched")
})

test("leaves the ways alone when no stop position is placed", () => {
    const waysData = ways(road())

    assert.equal(insertStopPositionsIntoWays(waysData, []), waysData)
})

// A stop already in OSM can be given a stop position too, so the store is keyed by the
// platform rather than living on a new stop.
const platform = (over = {}) => ({ type: "node", id: "123456", member: true, tags: { name: "The Station" }, ...over })

const placementFor = (latLng = [51.50009, 0.0003]) => planStopPosition(latLng, ways(road()))

beforeEach(clearStopPositions)

test("gives a stop already in OSM a stop position of its own", () => {
    const osmStop = platform()
    const node = setStopPosition(osmStop, placementFor(), "The Station")

    assert.ok(Number.parseInt(node.id, 10) < 0, "a placeholder until upload")
    assert.equal(node.public_transport, "stop_position")
    assert.equal(node.name, "The Station")
    assert.equal(getStopPositionNode(osmStop), node)
    assert.ok(hasStopPosition(osmStop))
})

test("sends the stop's name with it, for the node's own name tag", () => {
    setStopPosition(platform(), placementFor(), "The Station")

    const [position] = stopPositionsPayload()
    assert.equal(position.name, "The Station")
    assert.equal(position.wayId, 201)
})

test("taking it back out leaves nothing to upload", () => {
    const osmStop = platform()
    setStopPosition(osmStop, placementFor(), "The Station")
    removeStopPosition(osmStop)

    assert.equal(getStopPositionNode(osmStop), null)
    assert.deepEqual(stopPositionsPayload(), [])
})

test("two stops get their own node ids", () => {
    setStopPosition(platform({ id: "1" }), placementFor(), "One")
    setStopPosition(platform({ id: "2" }), placementFor([51.50009, 0.001]), "Two")

    const ids = stopPositionsPayload().map((position) => position.id)
    assert.equal(new Set(ids).size, 2)
})

test("the node follows the platform in and out of the route", () => {
    const offRoute = platform({ member: false })
    assert.equal(setStopPosition(offRoute, placementFor(), "The Station").member, false)
})

test("setting it again keeps the id, so the route still refers to it", () => {
    const osmStop = platform()
    const first = setStopPosition(osmStop, placementFor(), "The Station")
    const second = setStopPosition(osmStop, placementFor([51.50009, 0.001]), "The Station")

    assert.equal(first.id, second.id)
})

test("a stop position for a stop the route does not call at is never uploaded", () => {
    // it would otherwise be created on the road as a member of nothing
    setStopPosition(platform({ member: false }), placementFor(), "The Station")

    assert.deepEqual(stopPositionsPayload(), [])
    assert.equal(stopPositionCount(), 0)
})

test("a stop leaving the route takes its stop position out of the upload", () => {
    const osmStop = platform()
    const node = setStopPosition(osmStop, placementFor(), "The Station")
    assert.equal(stopPositionsPayload().length, 1)

    // as setMemberState() does when the stop is clicked off the route
    node.member = false

    assert.deepEqual(stopPositionsPayload(), [])
})

test("a stop renamed after the node was placed renames the node too", () => {
    // the mapper accepts a NaPTAN rename after queueing the stop position
    const osmStop = platform()
    const node = setStopPosition(osmStop, placementFor(), "Wharf Road")

    assert.equal(renameStopPosition(osmStop, "The Orchards"), true)
    assert.equal(node.name, "The Orchards")
    assert.equal(stopPositionsPayload()[0].name, "The Orchards")
})

test("renaming to the name it already has changes nothing", () => {
    const osmStop = platform()
    setStopPosition(osmStop, placementFor(), "The Orchards")

    assert.equal(renameStopPosition(osmStop, "The Orchards"), false)
})

test("renaming a stop with no stop position is harmless", () => {
    assert.equal(renameStopPosition(platform(), "The Orchards"), false)
})
