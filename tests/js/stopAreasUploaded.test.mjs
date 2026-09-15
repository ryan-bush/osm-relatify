// Run with: node --test "tests/js/*.mjs"
//
// What an upload leaves behind. Overpass is where a download learns about stop areas and
// it runs minutes behind OSM, so what this session uploaded is remembered rather than
// asked for again. The record is module state meant to last as long as the page, so there
// is nothing to reset between these: they are written in order, over stops and relations
// that do not overlap.
import assert from "node:assert/strict"
import { test } from "node:test"

import {
    existingAreaFor,
    existingAreasFor,
    noteUploadedStopAreas,
    setExistingStopAreas,
    stopAreaUploaded,
} from "../../static/js/stopAreas.js"

const member = (id, role = "platform") => ({ key: `node/${id}`, type: "node", id: id, role: role })

// as stopAreasPayload() builds it: members without a key, and a null id for a new one
const sent = (id, name, ids) => ({
    id: id,
    name: name,
    members: ids.map((memberId) => ({ type: "node", id: memberId, role: "platform" })),
})

test("nothing is grouped before anything is uploaded", () => {
    assert.equal(stopAreaUploaded([member(1), member(2)]), false)
})

test("a stop an upload grouped is remembered", () => {
    noteUploadedStopAreas(
        [sent(null, "The Station", [1, 2])],
        [{ id: 501, name: "The Station", members: ["node/1", "node/2"] }],
    )

    assert.equal(stopAreaUploaded([member(1), member(2)]), true)
})

test("the relation it became is there to be completed, not only known of", () => {
    setExistingStopAreas([])

    const [area] = existingAreasFor([member(1), member(2)])

    assert.equal(area.id, 501)
    assert.deepEqual(area.members, ["node/1", "node/2"])
})

test("a stop the area is still missing is missing from it", () => {
    const area = existingAreaFor([member(1), member(50)])

    assert.equal(area.id, 501)
    assert.equal(area.members.includes("node/50"), false)
})

test("one stop of the group is enough, as the other side may be new to this route", () => {
    assert.equal(stopAreaUploaded([member(2), member(50)]), true)
})

test("stops of another place are not", () => {
    assert.equal(stopAreaUploaded([member(60), member(61)]), false)
    assert.deepEqual(existingAreasFor([member(60)]), [])
})

test("a download that knows the relation is not told of it twice", () => {
    // listed twice it would read as a place grouped twice over, which is a warning
    setExistingStopAreas([{ id: 501, name: "The Station", members: ["node/1", "node/2", "node/3"] }])

    const found = existingAreasFor([member(1), member(3)])

    assert.equal(found.length, 1)
    assert.deepEqual(found[0].members, ["node/1", "node/2", "node/3"])
})

test("members are unioned, each side knowing of some the other does not", () => {
    noteUploadedStopAreas([sent(501, "The Station", [4])], [])
    setExistingStopAreas([{ id: 501, name: "The Station", members: ["node/1", "node/2"] }])

    const [area] = existingAreasFor([member(1)])

    assert.deepEqual(area.members, ["node/1", "node/2", "node/4"])
})

test("a stop the changeset created is not remembered by its placeholder, which others reuse", () => {
    noteUploadedStopAreas([sent(null, "The New Stop", [-1, 20])], [])

    assert.equal(stopAreaUploaded([member(-1)]), false)
    assert.equal(stopAreaUploaded([member(20)]), true)
})

test("an upload that could not say what it created still leaves the stops known", () => {
    // the relation is out there, so there is nothing to offer for these stops either way
    noteUploadedStopAreas([sent(null, "The Market", [30, 31])], [])

    assert.equal(stopAreaUploaded([member(30)]), true)
    assert.deepEqual(existingAreasFor([member(30)]), [])
})

test("a created stop reaches the record by the real id the upload read back", () => {
    noteUploadedStopAreas([sent(null, "The College", [-1])], [{ id: 502, name: "The College", members: ["node/901"] }])

    assert.equal(stopAreaUploaded([member(901)]), true)
})
