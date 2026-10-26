// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { test } from "node:test"

import {
    moveVisit,
    planFromRoute,
    removeVisit,
    serveAgain,
    setRole,
    visitsOutOfPlace,
} from "../../static/js/stopPlan.js"

const platform = (id) => ({ platform: { id }, stop: null })
const stopPosition = (id) => ({ platform: null, stop: { id } })

test("a route nobody has arranged plans every visit automatically", () => {
    assert.deepEqual(planFromRoute([platform("1"), stopPosition("2")], null), [
        { id: "1", role: "auto" },
        { id: "2", role: "auto" },
    ])
})

test("each visit to a stop served twice keeps its own role", () => {
    const plan = [
        { id: "1", role: "entry_only" },
        { id: "2", role: "auto" },
        { id: "1", role: "exit_only" },
    ]

    // the route may have put them in another order, but the nth visit is still the nth
    assert.deepEqual(planFromRoute([platform("1"), platform("1"), platform("2")], plan), [
        { id: "1", role: "entry_only" },
        { id: "1", role: "exit_only" },
        { id: "2", role: "auto" },
    ])
})

test("a stop the plan has no role for yet is automatic", () => {
    assert.deepEqual(planFromRoute([platform("1"), platform("3")], [{ id: "1", role: "both" }]), [
        { id: "1", role: "both" },
        { id: "3", role: "auto" },
    ])
})

const plan = ["a", "b", "c", "d"].map((id) => ({ id, role: "auto" }))
const ids = (entries) => entries.map((entry) => entry.id).join("")

test("moving a visit puts it at the new position", () => {
    assert.equal(ids(moveVisit(plan, 3, 1)), "adbc")
    assert.equal(ids(moveVisit(plan, 0, 2)), "bcad")
})

test("serving a stop again adds a visit right after it", () => {
    assert.equal(ids(serveAgain(plan, 1)), "abbcd")
})

test("removing a visit leaves the others", () => {
    assert.equal(ids(removeVisit(serveAgain(plan, 1), 2)), "abcd")
})

test("setting a role changes only that visit", () => {
    assert.deepEqual(
        setRole(plan, 2, "exit_only").map((entry) => entry.role),
        ["auto", "auto", "exit_only", "auto"],
    )
})

test("a route in the planned order has nothing out of place", () => {
    assert.equal(visitsOutOfPlace(plan, ["a", "b", "c", "d"]), 0)
})

test("a stop the route could not move is out of place", () => {
    assert.equal(visitsOutOfPlace(plan, ["b", "a", "c", "d"]), 1)
})

test("a visit the route could not make again is out of place", () => {
    assert.equal(visitsOutOfPlace(serveAgain(plan, 1), ["a", "b", "c", "d"]), 1)
})

test("a stop taken off the route is not counted", () => {
    assert.equal(visitsOutOfPlace(plan, ["a", "c", "d"]), 0)
})
