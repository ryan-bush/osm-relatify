// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    addStopArea,
    clearStopAreas,
    completedStopAreaCount,
    existingAreaFor,
    existingAreasFor,
    getPendingStopArea,
    groupMembers,
    growStopArea,
    newStopAreaCount,
    reconcileStopAreas,
    removeStopArea,
    renameStopArea,
    renameExistingStopArea,
    setExistingStopAreas,
    stopAreaSignature,
    stopAreasPayload,
    unrenameStopArea,
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

test("a stop renamed after the area was queued renames the area too", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "Wharf Road", null)

    assert.equal(renameStopArea(members, "The Orchards"), true)
    assert.equal(stopAreasPayload()[0].name, "The Orchards")
})

test("an area already in OSM keeps the name it has there", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "Wharf Road", { id: 99, name: "Station Approach", members: [] })

    assert.equal(renameStopArea(members, "The Orchards"), false)
    assert.equal(stopAreasPayload()[0].name, "Station Approach")
})

test("an empty name never replaces a real one", () => {
    const members = groupMembers([northbound, southbound])
    addStopArea(members, "Wharf Road", null)

    assert.equal(renameStopArea(members, ""), false)
    assert.equal(stopAreasPayload()[0].name, "Wharf Road")
})

test("every area the stops are spread over is reported", () => {
    setExistingStopAreas([
        { id: 1, name: "The Station", members: ["node/1", "node/2"] },
        { id: 2, name: "The Station", members: ["node/3"] },
        { id: 3, name: "Somewhere else", members: ["node/9"] },
    ])

    const found = existingAreasFor(groupMembers([northbound, southbound]))
    assert.deepEqual(
        found.map((area) => area.id),
        [1, 2],
    )
})

test("no single area is claimed when the place is grouped twice over", () => {
    setExistingStopAreas([
        { id: 1, name: "The Station", members: ["node/1", "node/2"] },
        { id: 2, name: "The Station", members: ["node/3"] },
    ])

    // which of them should hold the rest is not ours to guess at
    assert.equal(existingAreaFor(groupMembers([northbound, southbound])), null)
})

const AREA = { id: 99, name: "The Station", members: ["node/1", "node/2", "node/3", "node/4"] }

test("a rename follows the stop it is named after", () => {
    setExistingStopAreas([AREA])
    const members = groupMembers([northbound, southbound])
    renameExistingStopArea(members, AREA, "Market Square")

    assert.deepEqual(stopAreasPayload(), [
        {
            id: 99,
            name: "Market Square",
            expectedName: "The Station",
            members: members.map((m) => ({ type: m.type, id: m.id, role: m.role })),
        },
    ])
})

test("taking the rename back leaves nothing to upload", () => {
    setExistingStopAreas([AREA])
    const members = groupMembers([northbound, southbound])
    renameExistingStopArea(members, AREA, "Market Square")

    assert.equal(unrenameStopArea(members, AREA), true)
    assert.deepEqual(stopAreasPayload(), [])
})

test("taking the rename back keeps the members the area is still missing", () => {
    const partial = { id: 99, name: "The Station", members: ["node/1"] }
    setExistingStopAreas([partial])
    const members = groupMembers([northbound, southbound])
    renameExistingStopArea(members, partial, "Market Square")

    assert.equal(unrenameStopArea(members, partial), true)

    const [queued] = stopAreasPayload()
    assert.equal(queued.id, 99)
    assert.equal(queued.name, "The Station", "back to the name it has in OSM")
    assert.equal(queued.expectedName, null, "no longer a rename")
    assert.equal(queued.members.length, 4)
})

test("a new area never carries a name to replace", () => {
    setExistingStopAreas([])
    addStopArea(groupMembers([northbound, southbound]), "The Station", null)

    assert.equal(stopAreasPayload()[0].expectedName, null)
})

// Llys Watling on route 4: an area was queued for one side, then the other side was
// renamed to match, bringing its existing area into the group
test("an area queued for part of a group grows into the group's existing relation", () => {
    const watlingCourt = { platform: stop(3, "Watling Court"), stop: stop(4, "Watling Court") }
    setExistingStopAreas([{ id: 77, name: "Watling Court", members: ["node/3", "node/4"] }])

    const part = groupMembers([northbound])
    addStopArea(part, "Llys Watling", null)

    const whole = groupMembers([northbound, watlingCourt])
    assert.equal(growStopArea(whole, existingAreaFor(whole)), true)

    assert.equal(getPendingStopArea(part), null)
    assert.deepEqual(
        stopAreasPayload().map((area) => [area.id, area.name, area.members.length]),
        [[77, "Watling Court", 4]],
    )
})

test("a part already covered by an area queued for the whole group is dropped", () => {
    const part = groupMembers([northbound])
    const whole = groupMembers([northbound, southbound])
    addStopArea(part, "The Station", null)
    addStopArea(whole, "The Station", null)

    assert.equal(growStopArea(whole, null), true)
    assert.equal(stopAreasPayload().length, 1)
    assert.equal(stopAreasPayload()[0].members.length, 4)
})

test("an area for a different group is left alone", () => {
    const other = groupMembers([{ platform: stop(8, "Elsewhere"), stop: null }, { platform: stop(9, "Elsewhere") }])
    addStopArea(other, "Elsewhere", null)

    assert.equal(growStopArea(groupMembers([northbound, southbound]), null), false)
    assert.ok(getPendingStopArea(other))
})

// "A stop area refers to node/-17, which is not being created": the far side's new stop
// position is not uploaded once the route stops calling there, but the area still had it
test("a new stop the upload does not create is left out of its area", () => {
    const newNorth = { platform: stop(-1, "The Station"), stop: stop(-2, "The Station") }
    addStopArea(groupMembers([newNorth, southbound]), "The Station", null)

    const [payload] = stopAreasPayload(new Set([-1]))

    assert.deepEqual(
        payload.members.map((member) => member.id),
        [-1, 3, 4],
    )
})

test("a new area left with a single member is not sent", () => {
    const newNorth = { platform: stop(-1, "The Station"), stop: null }
    const newSouth = { platform: stop(-3, "The Station"), stop: null }
    addStopArea(groupMembers([newNorth, newSouth]), "The Station", null)

    assert.deepEqual(stopAreasPayload(new Set([-1])), [])
})

test("an existing area left with nothing to add is not sent", () => {
    setExistingStopAreas([{ id: 77, name: "The Station", members: ["node/1", "node/2"] }])
    const newStop = { platform: null, stop: stop(-2, "The Station") }
    const members = groupMembers([northbound, newStop])
    addStopArea(members, "The Station", existingAreaFor(members))

    assert.equal(stopAreasPayload(new Set()).length, 0)
    assert.equal(stopAreasPayload(new Set([-2])).length, 1)
})
