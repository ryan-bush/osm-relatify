// A type=route_master relation holds every variant of one line: the outbound route, the
// return, the school-day one. PTv2 asks that every route belong to exactly one of them.
// This is what is known about the masters of the loaded route; routeMastersView.js shows it.

// route masters the loaded route is already a member of
let currentMasters = []
// masters that other routes with the same ref belong to, which this one could join
let candidateMasters = []

// Whether those lists are the whole truth. Membership is asked of the OSM API and the
// candidates of Overpass; a lookup that fails says nothing about what is out there, and
// an empty list would read as "this route is in no master" — which is exactly what would
// invite putting it in a second one beside the master it already belongs to.
let mastersKnown = true

// A download replaces what is known and drops any change queued against the old answer,
// which may have been about masters that are no longer what they were.
export function setRouteMasters(data) {
    clearRouteMasterChanges()
    refreshRouteMasters(data)
}

// The same, for an answer to the tags the mapper has since typed rather than to a fresh
// download. A choice they made by hand is theirs and survives; one made for them was
// derived from the ref, and is derived again from the new one.
export function refreshRouteMasters(data) {
    mastersKnown = data?.routeMasters != null
    currentMasters = data?.routeMasters ?? []
    candidateMasters = data?.routeMasterCandidates ?? []

    if (pending?.automatic === false) return

    pending = null

    // PTv2 asks that every route be in a master, so the one this is plainly a variant of
    // is queued without being asked for. It says so, and can be undone.
    if (mastersKnown && currentMasters.length === 0) {
        const found = unambiguousCandidate(candidateMasters, data?.tags ?? {})
        if (found) linkRouteMaster(found, { automatic: true })
    }
}

export const routeMastersKnown = () => mastersKnown
export const currentRouteMasters = () => currentMasters
export const routeMasterCandidates = () => candidateMasters

// What to call a master. Its name is the usual answer, and a master with neither name nor
// ref is still worth showing rather than leaving blank.
export const describeMaster = (master) => {
    const name = master.tags?.name?.trim()
    if (name) return name

    const ref = master.tags?.ref?.trim()
    return ref ? `Route master ${ref}` : "Unnamed route master"
}

// What to call one of its variants, which is how a mapper tells the directions apart.
export const describeRoute = (route) =>
    [route.ref, route.name]
        .map((part) => part?.trim())
        .filter(Boolean)
        .join(" ") || `Relation ${route.id}`

// The route's own kind as tagged: the type tag names the tag that carries it, so a
// disused:route reads its kind from disused:route rather than from route.
export const routeValue = (tags) => tags?.[tags?.type] ?? ""

// Ways a master disagrees with the route in it. Neither is fixed from here; a master that
// says something different about the same line is a thing the mapper should see.
export function mismatchesOf(master, tags) {
    const result = []

    const ref = tags?.ref?.trim() ?? ""
    const masterRef = master.tags?.ref?.trim() ?? ""
    if (ref && masterRef && ref !== masterRef)
        result.push(`Its ref is ${masterRef}, but this route's is ${ref}.`)

    const kind = routeValue(tags).trim()
    const masterKind = master.tags?.route_master?.trim() ?? ""
    if (kind && masterKind && kind !== masterKind)
        result.push(
            `It is a master of ${masterKind} routes, but this is a ${kind} route.`,
        )

    return result
}

// Members the lookup did not describe: a master holding something that is not a route
// relation, or one too large to have been expanded.
export const undescribedMemberCount = (master) =>
    master.members.length - master.routes.length

// The change queued for upload: the master to put this route in, either one already in
// OSM (by id) or one to create (tags only). Null while the route's membership is left
// exactly as it is.
let pending = null
// ids of masters to take this route out of
const detaching = new Set()

export const pendingRouteMaster = () => pending
export const detachingRouteMasters = () => [...detaching]

// Puts the route in a master already in OSM. Its tags are left alone until the mapper
// opens them, which is what gives the upload a baseline to diff against.
export function linkRouteMaster(master, { automatic = false } = {}) {
    seededTags = null
    pending = {
        id: master.id,
        tags: { ...master.tags },
        tagsOriginal: null,
        automatic,
    }
}

// What a master queued for creation was last given from the route. Kept so that a tag the
// mapper has typed over can be told from one that is only following the route along.
let seededTags = null

// Creates a master for this route, seeded from the route's own tags.
export function createRouteMaster(routeTags) {
    seededTags = defaultMasterTags(routeTags)
    pending = {
        id: null,
        tags: { ...seededTags },
        tagsOriginal: null,
        automatic: false,
    }
}

/**
 * Follows the route's tags, for a master that does not exist yet.
 *
 * It was named after the route's ref, so a ref corrected before uploading would otherwise
 * leave the master carrying the old one. Only what the mapper has not touched follows
 * along; anything they typed into it is theirs to keep.
 *
 * Returns the keys that changed, so the tag table can be told what to show.
 */
export function followRouteTags(routeTags) {
    if (pending === null || pending.id !== null || seededTags === null)
        return []

    const next = defaultMasterTags(routeTags)
    const tags = { ...pending.tags }
    const changed = []

    for (const key of new Set([
        ...Object.keys(seededTags),
        ...Object.keys(next),
    ])) {
        // the mapper put something of their own here, so it is not ours to replace
        if ((tags[key] ?? "") !== (seededTags[key] ?? "")) continue

        if (next[key] === undefined) {
            if (key in tags) {
                delete tags[key]
                changed.push(key)
            }
        } else if (tags[key] !== next[key]) {
            tags[key] = next[key]
            changed.push(key)
        }
    }

    seededTags = next

    if (changed.length) pending.tags = tags

    return changed
}

// Opens an existing master's tags for editing. What it is called now becomes the baseline
// the edits are diffed against, so a tag someone else changes meanwhile is a conflict
// rather than something quietly overwritten.
export function editRouteMasterTags(master) {
    seededTags = null
    pending = {
        id: master.id,
        tags: { ...master.tags },
        tagsOriginal: { ...master.tags },
        automatic: false,
    }
}

export const setPendingRouteMasterTags = (tags) => {
    // The editor reports null as it is unloaded, which happens whenever the tag table
    // stops belonging to anything — not only when a master's tags are put away, but when
    // the queued change moves to one whose tags are not open. That is not an edit, and
    // taking it as one emptied the tags of a change nobody had touched.
    if (pending && tags !== null) pending.tags = tags
}

export const clearPendingRouteMaster = () => {
    seededTags = null
    pending = null
}

export function detachRouteMaster(id) {
    detaching.add(id)
    // leaving a master and joining it in the same breath is not a thing to send
    if (pending?.id === id) {
        seededTags = null
        pending = null
    }
}

export const undetachRouteMaster = (id) => detaching.delete(id)
export const isDetaching = (id) => detaching.has(id)

export function clearRouteMasterChanges() {
    seededTags = null
    pending = null
    detaching.clear()
}

// The tags a new master starts with, taken from the route it is being made for. The
// mapper can change any of them before uploading; what makes it a master is set server
// side either way.
export function defaultMasterTags(routeTags) {
    const kind = routeValue(routeTags).trim()
    const tags = { type: "route_master", route_master: kind }
    const ref = routeTags?.ref?.trim() ?? ""

    if (ref) {
        tags.ref = ref
        // the wiki's convention: the kind of route, then the line it runs
        if (kind) tags.name = `${kind[0].toUpperCase()}${kind.slice(1)} ${ref}`
    }

    for (const key of ["network", "operator", "colour"]) {
        const value = routeTags?.[key]?.trim()
        if (value) tags[key] = value
    }

    return tags
}

// The one candidate that is unmistakably this route's master: same ref, same network.
// Anything less certain is left for the mapper to pick, rather than guessed at.
export function unambiguousCandidate(candidates, routeTags) {
    const ref = routeTags?.ref?.trim() ?? ""
    if (!ref) return null

    const network = routeTags?.network?.trim() ?? ""
    const matches = candidates.filter(
        (master) =>
            (master.tags?.ref?.trim() ?? "") === ref &&
            (master.tags?.network?.trim() ?? "") === network,
    )

    return matches.length === 1 ? matches[0] : null
}

// The ref changed, so what was found for the old one is not an answer about the new one.
// A choice the mapper made by hand is still theirs; one made for them came from the ref,
// and goes with it.
export function invalidateRouteMasterCandidates() {
    candidateMasters = []
    if (pending?.automatic) {
        seededTags = null
        pending = null
    }
}

export const routeMasterPayload = () => ({
    routeMaster: pending && {
        id: pending.id,
        tags: pending.tags,
        tagsOriginal: pending.tagsOriginal,
    },
    routeMasterDetach: [...detaching],
})

// How many changes to this route's masters the changeset carries, which is what makes a
// changeset that leaves the route relation itself untouched still worth uploading.
export const routeMasterChangeCount = () =>
    (pending === null ? 0 : 1) + detaching.size
