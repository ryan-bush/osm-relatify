// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { test } from "node:test"

import {
    buildEntries,
    isDuplicate,
    isModified,
    tagsFromEntries,
    visibleEntries,
} from "../../static/js/tagEntries.js"

const FEATURED = ["name", "ref", "roundtrip"]

test("featured keys are always offered, in the order given", () => {
    const entries = buildEntries({ ref: "71" }, FEATURED)

    assert.deepEqual(entries, [
        { key: "name", value: "" },
        { key: "ref", value: "71" },
        { key: "roundtrip", value: "" },
    ])
})

test("the rest follow the featured ones, sorted", () => {
    const entries = buildEntries(
        { type: "route", ref: "71", colour: "red" },
        FEATURED,
    )

    assert.deepEqual(
        entries.map((entry) => entry.key),
        ["name", "ref", "roundtrip", "colour", "type"],
    )
})

test("a relation with no tags still offers every featured key", () => {
    assert.deepEqual(
        buildEntries({}, FEATURED).map((entry) => entry.value),
        ["", "", ""],
    )
})

test("the working copy is what the rows amount to, trimmed", () => {
    const tags = tagsFromEntries([
        { key: " name ", value: " Bus 71 " },
        { key: "ref", value: "71" },
    ])

    assert.deepEqual(tags, { name: "Bus 71", ref: "71" })
})

// an emptied-out field is how the editor says "delete this tag"
test("a row with no value is not a tag", () => {
    assert.deepEqual(
        tagsFromEntries([
            { key: "name", value: "" },
            { key: "ref", value: "  " },
        ]),
        {},
    )
})

test("a row with no key yet is not a tag", () => {
    assert.deepEqual(tagsFromEntries([{ key: "", value: "something" }]), {})
})

test("a value differing from what was loaded is modified", () => {
    const original = { name: "Bus 71", ref: "71" }

    assert.equal(isModified({ key: "name", value: "Bus 72" }, original), true)
    assert.equal(isModified({ key: "name", value: "Bus 71" }, original), false)
    // whitespace alone is not an edit, being trimmed before it is submitted
    assert.equal(
        isModified({ key: "name", value: " Bus 71 " }, original),
        false,
    )
})

test("filling in a key the relation never had is modified", () => {
    assert.equal(isModified({ key: "colour", value: "red" }, {}), true)
    assert.equal(isModified({ key: "colour", value: "" }, {}), false)
})

test("clearing a value the relation had is modified", () => {
    assert.equal(
        isModified({ key: "name", value: "" }, { name: "Bus 71" }),
        true,
    )
})

// a row whose key is still being typed has nothing to compare against
test("a keyless row counts as modified once something is typed into it", () => {
    assert.equal(isModified({ key: "", value: "" }, {}), false)
    assert.equal(isModified({ key: "", value: "red" }, {}), true)
})

// a key typed into two rows at once would silently lose one of them
test("a key used by two rows is a duplicate in both", () => {
    const entries = [
        { key: "colour", value: "red" },
        { key: "colour", value: "blue" },
        { key: "ref", value: "71" },
    ]

    assert.equal(isDuplicate(entries[0], entries), true)
    assert.equal(isDuplicate(entries[1], entries), true)
    assert.equal(isDuplicate(entries[2], entries), false)
})

test("a key is a duplicate however it is spaced", () => {
    const entries = [
        { key: "colour", value: "red" },
        { key: " colour ", value: "blue" },
    ]

    assert.equal(isDuplicate(entries[0], entries), true)
})

test("rows without a key yet are not duplicates of each other", () => {
    const entries = [
        { key: "", value: "" },
        { key: "", value: "" },
    ]

    assert.equal(isDuplicate(entries[0], entries), false)
})

test("only the featured rows are shown until every tag is asked for", () => {
    const entries = buildEntries({ ref: "71", colour: "red" }, FEATURED)

    assert.deepEqual(
        visibleEntries(entries, FEATURED, false).map((entry) => entry.key),
        ["name", "ref", "roundtrip"],
    )
    assert.equal(visibleEntries(entries, FEATURED, true).length, 4)
})

// buildEntries is what setTag looks a key up in, so a featured key is always there to
// find even when the relation does not have it
test("a featured key is present to be set even when the relation lacks it", () => {
    const entries = buildEntries({ name: "Bus 9" }, FEATURED)

    assert.ok(
        entries.some((entry) => entry.key === "ref" && entry.value === ""),
    )
})
