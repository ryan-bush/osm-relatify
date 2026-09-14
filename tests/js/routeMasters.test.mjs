// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

import {
    createRouteMaster,
    currentRouteMasters,
    defaultMasterTags,
    describeMaster,
    describeRoute,
    detachRouteMaster,
    detachingRouteMasters,
    editRouteMasterTags,
    isDetaching,
    linkRouteMaster,
    mismatchesOf,
    invalidateRouteMasterCandidates,
    pendingRouteMaster,
    refreshRouteMasters,
    routeMasterCandidates,
    routeMasterChangeCount,
    routeMasterPayload,
    routeMastersKnown,
    routeValue,
    setPendingRouteMasterTags,
    setRouteMasters,
    undetachRouteMaster,
    undescribedMemberCount,
} from "../../static/js/routeMasters.js"

const master = (over = {}) => ({
    id: 100,
    tags: {
        type: "route_master",
        route_master: "bus",
        ref: "71",
        name: "Bus 71",
    },
    members: ["relation/1", "relation/2"],
    routes: [
        { id: 1, ref: "71", name: "Bus 71: Oxford => Witney" },
        { id: 2, ref: "71", name: "Bus 71: Witney => Oxford" },
    ],
    ...over,
})

const routeTags = { type: "route", route: "bus", ref: "71" }

beforeEach(() => {
    setRouteMasters({ routeMasters: [], routeMasterCandidates: [] })
})

test("a download reports what the route is in and what it could join", () => {
    setRouteMasters({
        routeMasters: [master()],
        routeMasterCandidates: [master({ id: 101 })],
    })

    assert.equal(routeMastersKnown(), true)
    assert.deepEqual(
        currentRouteMasters().map((m) => m.id),
        [100],
    )
    assert.deepEqual(
        routeMasterCandidates().map((m) => m.id),
        [101],
    )
})

// An empty list would read as "not in a route master", which is what invites linking the
// route into a second one beside the master it already belongs to.
test("a lookup that could not answer is not an answer of none", () => {
    setRouteMasters({ routeMasters: null, routeMasterCandidates: null })

    assert.equal(routeMastersKnown(), false)
    assert.deepEqual(currentRouteMasters(), [])
    assert.deepEqual(routeMasterCandidates(), [])
})

test("unloading forgets what was found", () => {
    setRouteMasters({
        routeMasters: [master()],
        routeMasterCandidates: [master({ id: 101 })],
    })
    setRouteMasters(null)

    assert.deepEqual(currentRouteMasters(), [])
    assert.deepEqual(routeMasterCandidates(), [])
})

test("a master is named after its name, its ref, or neither", () => {
    assert.equal(describeMaster(master()), "Bus 71")
    assert.equal(
        describeMaster(master({ tags: { ref: "71" } })),
        "Route master 71",
    )
    assert.equal(describeMaster(master({ tags: {} })), "Unnamed route master")
    assert.equal(
        describeMaster(master({ tags: { name: "  Bus 71  " } })),
        "Bus 71",
    )
})

test("a variant is named by its ref and name together", () => {
    assert.equal(
        describeRoute({ id: 1, ref: "71", name: "Oxford => Witney" }),
        "71 Oxford => Witney",
    )
    assert.equal(
        describeRoute({ id: 1, ref: "", name: "Oxford => Witney" }),
        "Oxford => Witney",
    )
    assert.equal(describeRoute({ id: 1, ref: "", name: "" }), "Relation 1")
})

// the type tag names the tag carrying the kind, so a disused route reads its own
test("a route's kind is read from the tag its type names", () => {
    assert.equal(routeValue({ type: "route", route: "bus" }), "bus")
    assert.equal(
        routeValue({
            type: "disused:route",
            "disused:route": "tram",
            route: "bus",
        }),
        "tram",
    )
    assert.equal(routeValue({}), "")
})

test("a master that agrees with its route has nothing to say", () => {
    assert.deepEqual(mismatchesOf(master(), routeTags), [])
})

test("a master with a different ref is worth pointing out", () => {
    const found = mismatchesOf(
        master({ tags: { route_master: "bus", ref: "72" } }),
        routeTags,
    )

    assert.equal(found.length, 1)
    assert.match(found[0], /72/)
    assert.match(found[0], /71/)
})

test("a master of another kind of route is worth pointing out", () => {
    const found = mismatchesOf(
        master({ tags: { route_master: "tram", ref: "71" } }),
        routeTags,
    )

    assert.equal(found.length, 1)
    assert.match(found[0], /tram/)
})

// a trolleybus route belongs in a trolleybus master, not a bus one
test("a trolleybus route is not mistaken for a bus one", () => {
    const trolleybus = { type: "route", route: "trolleybus", ref: "71" }

    assert.deepEqual(
        mismatchesOf(
            master({ tags: { route_master: "trolleybus", ref: "71" } }),
            trolleybus,
        ),
        [],
    )
    assert.equal(mismatchesOf(master(), trolleybus).length, 1)
})

test("nothing is claimed about a master or route that does not say", () => {
    assert.deepEqual(mismatchesOf(master({ tags: {} }), routeTags), [])
    assert.deepEqual(mismatchesOf(master(), { type: "route" }), [])
})

test("members that were not described are counted, not invented", () => {
    assert.equal(undescribedMemberCount(master()), 0)
    // a master holding a way as well as its two routes
    assert.equal(
        undescribedMemberCount(
            master({ members: ["relation/1", "relation/2", "way/9"] }),
        ),
        1,
    )
    // one too large to have been expanded at all
    assert.equal(undescribedMemberCount(master({ routes: [] })), 2)
})

// --- queueing a change -------------------------------------------------------

const fullRouteTags = { ...routeTags, network: "Oxfordshire", operator: "Stagecoach" }

const download = (over = {}) => ({ tags: fullRouteTags, routeMasters: [], routeMasterCandidates: [], ...over })

test("nothing is queued until something is asked for", () => {
    setRouteMasters(download({ routeMasters: [master()] }))

    assert.equal(pendingRouteMaster(), null)
    assert.deepEqual(detachingRouteMasters(), [])
    assert.equal(routeMasterChangeCount(), 0)
})

test("linking queues an existing master by id", () => {
    setRouteMasters(download())
    linkRouteMaster(master({ id: 101 }))

    assert.equal(pendingRouteMaster().id, 101)
    // its tags are left alone until they are opened, which is what gives a baseline
    assert.equal(pendingRouteMaster().tagsOriginal, null)
    assert.deepEqual(routeMasterPayload().routeMaster.tagsOriginal, null)
})

test("creating queues a master with no id and tags from the route", () => {
    setRouteMasters(download())
    createRouteMaster(fullRouteTags)

    const queued = pendingRouteMaster()

    assert.equal(queued.id, null)
    assert.equal(queued.tags.name, "Bus 71")
    assert.equal(queued.tags.ref, "71")
    assert.equal(queued.tags.route_master, "bus")
    assert.equal(queued.tags.network, "Oxfordshire")
})

test("a new master is named for the kind of route it holds", () => {
    assert.equal(defaultMasterTags({ type: "route", route: "tram", ref: "3" }).name, "Tram 3")
    assert.equal(defaultMasterTags({ type: "route", route: "trolleybus", ref: "3" }).name, "Trolleybus 3")
})

test("a route with no ref gives a master no ref to be named after", () => {
    const tags = defaultMasterTags({ type: "route", route: "bus" })

    assert.equal("ref" in tags, false)
    assert.equal("name" in tags, false)
    assert.equal(tags.route_master, "bus")
})

test("only the tags the route actually has are copied to a new master", () => {
    const tags = defaultMasterTags({ type: "route", route: "bus", ref: "71", operator: "  " })

    assert.equal("operator" in tags, false)
    assert.equal("colour" in tags, false)
})

test("opening an existing master's tags records what they were", () => {
    setRouteMasters(download({ routeMasters: [master()] }))
    editRouteMasterTags(master())

    assert.deepEqual(pendingRouteMaster().tagsOriginal, master().tags)
})

test("edited tags reach the payload", () => {
    setRouteMasters(download({ routeMasters: [master()] }))
    editRouteMasterTags(master())
    setPendingRouteMasterTags({ ...master().tags, operator: "Stagecoach" })

    assert.equal(routeMasterPayload().routeMaster.tags.operator, "Stagecoach")
})

test("detaching queues the master the route leaves", () => {
    setRouteMasters(download({ routeMasters: [master()] }))
    detachRouteMaster(100)

    assert.equal(isDetaching(100), true)
    assert.deepEqual(routeMasterPayload().routeMasterDetach, [100])

    undetachRouteMaster(100)
    assert.deepEqual(routeMasterPayload().routeMasterDetach, [])
})

// the server refuses this outright, and there is no point building it here either
test("leaving a master drops any plan to join it", () => {
    setRouteMasters(download())
    linkRouteMaster(master({ id: 101 }))
    detachRouteMaster(101)

    assert.equal(pendingRouteMaster(), null)
    assert.deepEqual(routeMasterPayload().routeMasterDetach, [101])
})

test("every queued change counts toward the changeset having something to say", () => {
    setRouteMasters(download())
    assert.equal(routeMasterChangeCount(), 0)

    linkRouteMaster(master({ id: 101 }))
    assert.equal(routeMasterChangeCount(), 1)

    detachRouteMaster(102)
    assert.equal(routeMasterChangeCount(), 2)
})

test("a payload with nothing queued asks for nothing", () => {
    setRouteMasters(download())

    assert.deepEqual(routeMasterPayload(), { routeMaster: null, routeMasterDetach: [] })
})

// --- linking without being asked ---------------------------------------------

const candidate = (over = {}) =>
    master({ id: 101, tags: { type: "route_master", route_master: "bus", ref: "71", network: "Oxfordshire" }, ...over })

test("the one candidate matching ref and network is queued by itself", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate()] }))

    assert.equal(pendingRouteMaster().id, 101)
    assert.equal(pendingRouteMaster().automatic, true)
})

test("a candidate whose network differs is left for the mapper", () => {
    const elsewhere = candidate({ tags: { type: "route_master", route_master: "bus", ref: "71", network: "Kent" } })

    setRouteMasters(download({ routeMasterCandidates: [elsewhere] }))

    assert.equal(pendingRouteMaster(), null)
})

test("two candidates that both match are not chosen between", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate(), candidate({ id: 102 })] }))

    assert.equal(pendingRouteMaster(), null)
})

test("a route already in a master is not queued into another", () => {
    setRouteMasters(download({ routeMasters: [master()], routeMasterCandidates: [candidate()] }))

    assert.equal(pendingRouteMaster(), null)
})

test("nothing is queued when the lookup could not answer", () => {
    setRouteMasters({ tags: fullRouteTags, routeMasters: null, routeMasterCandidates: null })

    assert.equal(pendingRouteMaster(), null)
})

test("a route with no ref matches nothing", () => {
    setRouteMasters({ tags: { type: "route", route: "bus" }, routeMasters: [], routeMasterCandidates: [candidate()] })

    assert.equal(pendingRouteMaster(), null)
})

test("a candidate matches when neither it nor the route names a network", () => {
    const plain = candidate({ tags: { type: "route_master", route_master: "bus", ref: "71" } })

    setRouteMasters({
        tags: { type: "route", route: "bus", ref: "71" },
        routeMasters: [],
        routeMasterCandidates: [plain],
    })

    assert.equal(pendingRouteMaster().id, 101)
})

// a change queued against one download is not a change to what the next one found
test("a fresh download drops what was queued against the last one", () => {
    setRouteMasters(download({ routeMasters: [master()] }))
    detachRouteMaster(100)
    editRouteMasterTags(master())

    setRouteMasters(download({ routeMasters: [master()] }))

    assert.equal(pendingRouteMaster(), null)
    assert.deepEqual(detachingRouteMasters(), [])
})

// The tag table is unloaded whenever it stops belonging to anything, and says so by
// reporting null. Taking that as an edit emptied the tags of a change nobody had touched.
test("putting the tag table away is not an edit to what is queued", () => {
    setRouteMasters(download())
    linkRouteMaster(master({ id: 101 }))

    setPendingRouteMasterTags(null)

    assert.deepEqual(pendingRouteMaster().tags, master().tags)
})

// --- asking again once the mapper has typed a ref -----------------------------

test("a later answer replaces what the download found", () => {
    setRouteMasters(download())
    assert.equal(pendingRouteMaster(), null)

    refreshRouteMasters({ tags: fullRouteTags, routeMasters: [], routeMasterCandidates: [candidate()] })

    assert.equal(pendingRouteMaster().id, 101)
    assert.equal(pendingRouteMaster().automatic, true)
})

// the mapper chose; a ref typed afterwards is no reason to undo it for them
test("a choice made by hand survives a later answer", () => {
    setRouteMasters(download())
    linkRouteMaster(master({ id: 999 }))

    refreshRouteMasters({ tags: fullRouteTags, routeMasters: [], routeMasterCandidates: [candidate()] })

    assert.equal(pendingRouteMaster().id, 999)
})

// it was derived from the old ref, so it is derived again from the new one
test("a choice made automatically is reconsidered", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate()] }))
    assert.equal(pendingRouteMaster().id, 101)

    refreshRouteMasters({ tags: fullRouteTags, routeMasters: [], routeMasterCandidates: [candidate({ id: 102 })] })

    assert.equal(pendingRouteMaster().id, 102)
})

test("an answer with nothing to join drops an automatic choice", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate()] }))

    refreshRouteMasters({ tags: fullRouteTags, routeMasters: [], routeMasterCandidates: [] })

    assert.equal(pendingRouteMaster(), null)
})

test("asking again does not forget which masters are being left", () => {
    setRouteMasters(download({ routeMasters: [master()] }))
    detachRouteMaster(100)

    refreshRouteMasters({ tags: fullRouteTags, routeMasters: [master()], routeMasterCandidates: [] })

    assert.deepEqual(detachingRouteMasters(), [100])
})

// --- the ref changing under an answer ----------------------------------------

test("what was found for the old ref is no longer offered", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate(), candidate({ id: 102 })] }))

    invalidateRouteMasterCandidates()

    assert.deepEqual(routeMasterCandidates(), [])
})

// it came from the ref, and goes with it
test("a link made automatically goes when the ref it came from does", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate()] }))
    assert.equal(pendingRouteMaster().id, 101)

    invalidateRouteMasterCandidates()

    assert.equal(pendingRouteMaster(), null)
})

test("a link the mapper made by hand is still theirs", () => {
    setRouteMasters(download({ routeMasterCandidates: [candidate()] }))
    linkRouteMaster(master({ id: 999 }))

    invalidateRouteMasterCandidates()

    assert.equal(pendingRouteMaster().id, 999)
})

test("a master the mapper chose to create is still theirs", () => {
    setRouteMasters(download())
    createRouteMaster(fullRouteTags)

    invalidateRouteMasterCandidates()

    assert.equal(pendingRouteMaster().id, null)
})

test("masters being left are not forgotten when the ref changes", () => {
    setRouteMasters(download({ routeMasters: [master()] }))
    detachRouteMaster(100)

    invalidateRouteMasterCandidates()

    assert.deepEqual(detachingRouteMasters(), [100])
})
