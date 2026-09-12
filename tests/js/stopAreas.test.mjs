// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    addStopArea,
    clearStopAreas,
    completedStopAreaCount,
    existingAreaFor,
    getPendingStopArea,
    groupMembers,
    newStopAreaCount,
    reconcileStopAreas,
    removeStopArea,
    setExistingStopAreas,
    stopAreaSignature,
    stopAreasPayload,
} from "../../static/js/stopAreas.js"

const stop = (id, name, over = {}) => ({ type: "node", id: `${id}`, name: name, tags: { name: name }, ...over })

// the canonical case: the same stop either side of a road, each with a stop position
const northbound = { platform: stop(1, "The Station"), stop: stop(2, "The Station") }
const southbound = { platform: stop(3, "The Station"), stop: stop(4, "The Station") }

beforeEach(() => {
    clearStopAreas()
    setExistingStopAreas([])
})

test("both sides of the road become the members of one area", () => {
    const members = groupMembers([northbound, southbound])

    assert.deepEqual(
        members.map((m) => [m.key, m.role]),
        [
            ["node/1", "platform"],
            ["node/2", "stop"],
            ["node/3", "platform"],
            ["node/4", "stop"],
        ],
    )
})

test("a stop counted once even when it stands for both roles", () => {
    const shared = stop(1, "The Station")
    assert.equal(groupMembers([{ platform: shared, stop: shared }]).length, 1)
})

test("a way the route splits is named by its real id", () => {
    const platform = { type: "way", id: "55_1_2", name: "The Station", tags: { name: "The Station" } }
    assert.equal(groupMembers([{ platform: platform, stop: null }])[0].key, "way/55")
})

test("finds the area these stops are already in", () => {
    setExistingStopAreas([{ id: 99, name: "The Station", members: ["node/1", "node/2"] }])

    const area = existingAreaFor(groupMembers([northbound, southbound]))
    assert.equal(area.id, 99)
})

test("leaves a group alone when its stops sit in different areas", () => {
    // picking one of them is not ours to guess at
    setExistingStopAreas([
        { id: 98, name: "A", members: ["node/1"] },
        { id: 99, name: "B", members: ["node/3"] },
    ])

    assert.equal(existingAreaFor(groupMembers([northbound, southbound])), null)
})

test("a new area is sent with its name and every member", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "The Station", null)

    const [payload] = stopAreasPayload()
    assert.equal(payload.id, null)
    assert.equal(payload.name, "The Station")
    assert.equal(payload.members.length, 4)
    assert.deepEqual(payload.members[0], { type: "node", id: 1, role: "platform" })
    assert.equal(newStopAreaCount(), 1)
})

test("completing an existing area sends its id and keeps its name", () => {
    const existing = { id: 99, name: "Station Approach", members: ["node/1"] }
    addStopArea(groupMembers([northbound, southbound]), "The Station", existing)

    const [payload] = stopAreasPayload()
    assert.equal(payload.id, 99)
    assert.equal(payload.name, "Station Approach", "the relation keeps the name it has")
    assert.equal(completedStopAreaCount(), 1)
})

test("taking it back leaves nothing to upload", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "The Station", null)
    removeStopArea(members)

    assert.equal(getPendingStopArea(members), null)
    assert.deepEqual(stopAreasPayload(), [])
})

test("a group that changed since is dropped rather than uploaded", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "The Station", null)

    // a download turned up another stop here, so the queued set is not what the user saw
    reconcileStopAreas([stopAreaSignature(groupMembers([northbound]))])

    assert.deepEqual(stopAreasPayload(), [])
})

test("a group that still looks the same survives a download", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "The Station", null)

    reconcileStopAreas([stopAreaSignature(members)])

    assert.equal(stopAreasPayload().length, 1)
})
