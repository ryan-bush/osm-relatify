// Run with: node --test "tests/js/*.mjs"
//
// What this session has already grouped. The record is module state that is meant to last
// as long as the page, so there is nothing to reset between these: they are written in
// order, over stops that do not overlap.
import assert from "node:assert/strict"
import { test } from "node:test"

import { noteUploadedStopAreas, stopAreaUploaded } from "../../static/js/stopAreas.js"

const member = (id, role = "platform") => ({ key: `node/${id}`, type: "node", id: id, role: role })

test("nothing is grouped before anything is uploaded", () => {
    assert.equal(stopAreaUploaded([member(1), member(2)]), false)
})

test("a stop an upload grouped is remembered", () => {
    noteUploadedStopAreas([{ id: null, name: "The Station", members: [member(1), member(2)] }])

    assert.equal(stopAreaUploaded([member(1), member(2)]), true)
})

test("one stop of the group is enough, as the other side may be new to this route", () => {
    assert.equal(stopAreaUploaded([member(2), member(50)]), true)
})

test("stops of another place are not", () => {
    assert.equal(stopAreaUploaded([member(60), member(61)]), false)
})

test("members added to an area already in OSM count the same", () => {
    noteUploadedStopAreas([{ id: 99, name: "The Market", members: [member(10), member(11)] }])

    assert.equal(stopAreaUploaded([member(11)]), true)
})

test("a stop the changeset created is not, its id being a placeholder others reuse", () => {
    noteUploadedStopAreas([{ id: null, name: "The New Stop", members: [member(-1), member(20)] }])

    assert.equal(stopAreaUploaded([member(-1)]), false)
    assert.equal(stopAreaUploaded([member(20)]), true)
})
