// A public_transport=stop_area relation groups the stops of one place: typically the two
// sides of a road, each platform with the stop position that serves it. The server works
// out which stops belong together and hands each collection a groupId; this turns those
// groups into relations to create, or members to add to a relation that already exists.

// stop_area relations the downloaded stops are already in, from the last download
let existingAreas = []

// The stop areas this session uploaded, which the download cannot see yet. Overpass is
// where a download learns about stop areas and it runs minutes behind OSM, so a relation
// created a moment ago is simply not in the next answer. The upload reads the real ids
// back out of the changeset and says what it created, and those are folded in here: the
// next variant of a line calls at the same places, and would otherwise be offered a
// second relation for stops that are already grouped.
const sessionAreas = []

// Whether that list is the whole truth. The lookup is a second Overpass query, and one
// that fails says nothing about what is out there: an empty list would read as "no stop
// area here", which is exactly what invites the mapper to create a second one beside the
// relation the stops are already in.
let existingAreasKnown = true

export function setExistingStopAreas(areas) {
    existingAreasKnown = areas != null
    existingAreas = mergeSessionAreas(areas ?? [])
}

// The downloaded answer, with what this session uploaded folded in. A relation in both is
// one relation: listed twice it would read as a place grouped twice over, which is a
// warning rather than something to add to. The member lists are unioned, as each knows of
// members the other does not — the download of anything added since, this session of what
// it added itself.
function mergeSessionAreas(areas) {
    if (!sessionAreas.length) return areas

    const result = areas.map((area) => {
        const session = sessionAreas.find((candidate) => candidate.id === area.id)
        if (!session) return area

        return { ...area, members: [...new Set([...area.members, ...session.members])] }
    })

    const known = new Set(areas.map((area) => area.id))

    return [...result, ...sessionAreas.filter((area) => !known.has(area.id))]
}

// What an upload created or completed, as the relations they now are. Applied to what is
// already loaded as well, so the route still open shows them without a download.
function noteSessionStopAreas(areas) {
    for (const area of areas) {
        const at = sessionAreas.findIndex((candidate) => candidate.id === area.id)

        if (at === -1) sessionAreas.push(area)
        else
            sessionAreas[at] = {
                ...sessionAreas[at],
                name: area.name,
                members: [...new Set([...sessionAreas[at].members, ...area.members])],
            }
    }

    if (existingAreasKnown) existingAreas = mergeSessionAreas(existingAreas)
}

export const stopAreasKnown = () => existingAreasKnown

// The stops this session has already put into a stop area, by element key.
//
// Which stop areas exist is answered by Overpass, and Overpass runs minutes behind OSM: a
// relation created a moment ago is simply not in the next download, so the same place
// looks ungrouped and is offered a relation of its own all over again. The upload refuses
// that as a conflict, but only once the work has been done twice, which is exactly what
// going through the variants of one route master does — the return direction calls at the
// same places as the outbound one.
const uploadedKeys = new Set()

/**
 * Records what an upload did to stop areas.
 *
 * `sent` is the payload that went up and `created` what the upload said it created, each
 * with the id OSM has now given it. The relations are remembered as relations, which is
 * what lets the next route complete them; the stops are remembered as well, so a place
 * this session grouped is known to have been grouped even when the upload could not say
 * which relation it became.
 *
 * Only the stops that were already in OSM: one created by the same changeset is named in
 * the payload by a placeholder id, which is handed out afresh for each route and so would
 * go on to name a different stop entirely. What became of those is in `created`, which
 * carries the real ids.
 */
export function noteUploadedStopAreas(sent, created = []) {
    const completed = []

    for (const area of sent) {
        const members = []

        for (const member of area.members) {
            if (member.id <= 0) continue

            const key = `${member.type}/${member.id}`
            uploadedKeys.add(key)
            members.push(key)
        }

        // a new one is only a relation once the upload says what id it was given
        if (area.id !== null && members.length) completed.push({ id: area.id, name: area.name, members: members })
    }

    for (const area of created) {
        for (const key of area.members) uploadedKeys.add(key)
    }

    noteSessionStopAreas([...completed, ...created])
}

// Whether this session has already grouped these stops. Only worth asking when the
// download found no stop area for them: once Overpass catches up, the relation is in the
// answer and there is nothing to remember. Kept for as long as the page is open, and not
// only while one route master is being worked through — a stop grouped in this session is
// grouped whichever route is loaded next, however it was reached.
export const stopAreaUploaded = (members) => members.some((member) => uploadedKeys.has(member.key))

const elementKey = (stop) => `${stop.type}/${stop.id.split("_")[0]}`

// Every stop of one group, as relation members. A platform takes the platform role and a
// stop position takes stop, which is what the wiki asks for.
export function groupMembers(collections) {
    const members = []
    const seen = new Set()

    for (const collection of collections) {
        for (const [stop, role] of [
            [collection.platform, "platform"],
            [collection.stop, "stop"],
        ]) {
            if (!stop) continue

            const key = elementKey(stop)
            if (seen.has(key)) continue
            seen.add(key)

            members.push({ key: key, type: stop.type, id: Number.parseInt(stop.id, 10), role: role, name: stop.name })
        }
    }

    return members
}

// Every stop area any of these stops is already in. More than one means the place has
// been grouped twice over, which is a thing to say rather than a thing to add to.
export function existingAreasFor(members) {
    const keys = new Set(members.map((member) => member.key))

    return existingAreas.filter((area) => area.members.some((key) => keys.has(key)))
}

// The one stop area these stops are already in. A group whose stops sit in different
// relations is left alone: picking one of them is not ours to guess at.
export function existingAreaFor(members) {
    const found = existingAreasFor(members)

    return found.length === 1 ? found[0] : null
}

const signatureOf = (members) =>
    members
        .map((member) => member.key)
        .sort()
        .join(";")

// pending changes, keyed by exactly the stops they cover, so a group that has since
// changed is not uploaded against a set the user never saw
const pending = new Map()

export const getPendingStopArea = (members) => pending.get(signatureOf(members)) ?? null

// `automatic` marks one the application queued by itself, following a stop position into
// the area its stop already belongs to, rather than one the mapper asked for. Only the
// mapper's own survives the group changing again under it.
export function addStopArea(members, name, existingArea, { automatic = false } = {}) {
    pending.set(signatureOf(members), {
        id: existingArea?.id ?? null,
        name: existingArea?.name || name,
        members: members,
        automatic: automatic,
    })
}

export const removeStopArea = (members) => pending.delete(signatureOf(members))

// Renames a stop area that is already in OSM, to follow the stop it is named after. The
// members ride along as they always do, so a rename and a completion are one change to
// one relation; `expectedName` is what it was called when the mapper was shown it, so a
// name changed since is a conflict rather than an overwrite.
export function renameExistingStopArea(members, existingArea, name) {
    const key = signatureOf(members)
    const queued = pending.get(key)

    pending.set(key, {
        id: existingArea.id,
        name: name,
        expectedName: existingArea.name,
        members: members,
        automatic: queued?.automatic ?? true,
    })
}

// As for a stop position: a stop renamed after the area was queued renames the area too.
// One that already exists in OSM keeps the name it has there.
export function renameStopArea(members, name) {
    const area = pending.get(signatureOf(members))
    if (!area || area.id !== null || !name || area.name === name) return false

    area.name = name
    return true
}

// A group can grow after an area was queued for part of it, when a stop is renamed to
// the name its neighbours have. What was queued for the part is moved over to the whole
// group, which takes the existing relation's id and name if it has one; one already
// queued for the whole group covers the part's members as it is.
export function growStopArea(members, existingArea) {
    const keys = new Set(members.map((member) => member.key))
    const target = signatureOf(members)
    let changed = false

    for (const [key, area] of pending) {
        if (key === target || area.members.length >= members.length) continue
        if (!area.members.every((member) => keys.has(member.key))) continue
        // queued against a relation other than the one the group is in
        if (area.id !== null && area.id !== existingArea?.id) continue

        pending.delete(key)
        changed = true

        if (pending.has(target)) continue

        pending.set(target, {
            ...area,
            id: existingArea?.id ?? null,
            name: existingArea?.name || area.name,
            expectedName: area.id !== null ? area.expectedName : null,
            members: members,
        })
    }

    return changed
}

// The area a stop is queued to join, if any, going by the element keys it is made of.
export function pendingStopAreaFor(keys) {
    for (const area of pending.values()) {
        if (area.members.some((member) => keys.includes(member.key))) return area
    }
    return null
}

export const clearStopAreas = () => pending.clear()

export const stopAreaCount = () => pending.size

// told apart for the changeset comment: one is a new relation, the other is members
// added to a relation that was already there
export const newStopAreaCount = () => Array.from(pending.values()).filter((area) => area.id === null).length

export const completedStopAreaCount = () => stopAreaCount() - newStopAreaCount()

// Drops anything whose group no longer looks the way it did, which a download can change.
export function reconcileStopAreas(signatures) {
    const live = new Set(signatures)

    for (const key of pending.keys()) {
        if (!live.has(key)) pending.delete(key)
    }
}

export const stopAreaSignature = signatureOf

// Takes back a rename that was following a stop's name, when the stop no longer has that
// name to follow. Anything else queued against the same relation is left alone: members
// it is still missing are a change of their own.
export function unrenameStopArea(members, existingArea) {
    const key = signatureOf(members)
    const queued = pending.get(key)
    if (!queued?.expectedName) return false

    const missing = existingArea ? members.filter((member) => !existingArea.members.includes(member.key)) : members

    if (!missing.length) {
        pending.delete(key)
        return true
    }

    pending.set(key, { ...queued, name: existingArea.name, expectedName: null })
    return true
}

// `created` holds the placeholder ids this upload creates. A new stop left out of it -
// deleted, or a stop position whose stop the route no longer calls at - is left out of
// its area too, as OSM refuses a member nothing creates. An area with nothing left to do
// is left out altogether.
export function stopAreasPayload(created = null) {
    const result = []

    for (const area of pending.values()) {
        const members = area.members.filter((member) => member.id > 0 || !created || created.has(member.id))
        const existing = area.id !== null ? existingAreas.find((known) => known.id === area.id) : null

        if (area.id === null && members.length < 2) continue
        if (
            existing &&
            !area.expectedName &&
            members.every((member) => existing.members.includes(member.key ?? `${member.type}/${member.id}`))
        ) {
            continue
        }

        result.push({
            id: area.id,
            name: area.name,
            // only sent for one already in OSM, where it says the name may be replaced; a
            // new relation has no name to disagree with
            expectedName: area.id !== null ? (area.expectedName ?? null) : null,
            members: members.map((member) => ({ type: member.type, id: member.id, role: member.role })),
        })
    }

    return result
}
