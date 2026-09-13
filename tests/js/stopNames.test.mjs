// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { test } from "node:test"

import { effectiveName } from "../../static/js/stopNames.js"

const stop = (name) => ({ tags: name === undefined ? {} : { name: name } })

test("a stop with no NaPTAN disagreement keeps its own name", () => {
    assert.equal(effectiveName(stop("Wharf Road"), undefined, undefined), "Wharf Road")
})

test("an undecided disagreement leaves the name alone", () => {
    // nothing is being written yet, so the stop is still called what it is called
    assert.equal(effectiveName(stop("Wharf Road"), "The Orchards", undefined), "Wharf Road")
})

test("keeping the stop's value leaves the name alone", () => {
    assert.equal(effectiveName(stop("Wharf Road"), "The Orchards", "osm"), "Wharf Road")
})

test("an accepted rename is the name anything made from the stop must use", () => {
    // the changeset renames the stop, so a stop position or stop area built from it
    // has to carry the new name, not the one about to be replaced
    assert.equal(effectiveName(stop("Wharf Road"), "The Orchards", "naptan"), "The Orchards")
})

test("a stop with no name at all", () => {
    assert.equal(effectiveName(stop(), undefined, undefined), "")
    assert.equal(effectiveName(undefined, undefined, undefined), "")
})

test("surrounding whitespace is trimmed", () => {
    assert.equal(effectiveName(stop("  Wharf Road  "), undefined, undefined), "Wharf Road")
})

test("an accepted rename wins even over a blank name", () => {
    assert.equal(effectiveName(stop(), "The Orchards", "naptan"), "The Orchards")
})
