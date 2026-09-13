// A public_transport=stop_area relation groups the stops of one place: typically the two
// sides of a road, each platform with the stop position that serves it. The server works
// out which stops belong together and hands each collection a groupId; this turns those
// groups into relations to create, or members to add to a relation that already exists.

// stop_area relations the downloaded stops are already in, from the last download
let existingAreas = []

// Whether that list is the whole truth. The lookup is a second Overpass query, and one
// that fails says nothing about what is out there: an empty list would read as "no stop
// area here", which is exactly what invites the mapper to create a second one beside the
// relation the stops are already in.
let existingAreasKnown = true

export function setExistingStopAreas(areas) {
    existingAreasKnown = areas != null
    existingAreas = areas ?? []
}

export const stopAreasKnown = () => existingAreasKnown

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

// As for a stop position: a stop renamed after the area was queued renames the area too.
// One that already exists in OSM keeps the name it has there.
export function renameStopArea(members, name) {
    const area = pending.get(signatureOf(members))
    if (!area || area.id !== null || !name || area.name === name) return false

    area.name = name
    return true
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

export const stopAreasPayload = () =>
    Array.from(pending.values(), (area) => ({
        id: area.id,
        name: area.name,
        members: area.members.map((member) => ({ type: member.type, id: member.id, role: member.role })),
    }))
