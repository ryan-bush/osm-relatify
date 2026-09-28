// NaPTAN tags the user chose to write to stops already in OSM. Nothing is written until
// upload, where each stop is fetched again and checked against what was shown here.
//
// Two kinds of change end up in the same place: tags the stop simply lacks, which are
// added, and tags NaPTAN disagrees with the stop about, which the mapper has to decide
// on one way or the other before the route can be uploaded.
const additions = new Map()

const additionKey = (stop) => `${stop.type},${stop.id}`

export const getTagAddition = (stop) => additions.get(additionKey(stop))

export function addTagAddition(stop, tags) {
    additions.set(additionKey(stop), {
        type: stop.type,
        id: Number.parseInt(stop.id, 10),
        tags: tags,
    })
}

export const removeTagAddition = (stop) => additions.delete(additionKey(stop))

export const tagAdditionCount = () => additions.size

// Tags the mapper typed into the stop form for a stop already in OSM, against what it
// was showing them at the time. Kept apart from the NaPTAN fills above because the two
// are allowed to write different things: the mapper's shelter and bench are nothing
// NaPTAN knows about.
const edits = new Map()

export const getStopEdit = (stop) => edits.get(additionKey(stop))

// `tags` is every field the form offers, an empty value meaning the tag should go.
// Anything that already says what the stop says is dropped, so a form saved untouched
// leaves nothing behind.
export function setStopEdit(stop, tags) {
    const key = additionKey(stop)
    const current = stop.tags ?? {}

    const changed = {}
    const expected = {}

    for (const [tagKey, value] of Object.entries(tags)) {
        const was = (current[tagKey] ?? "").trim()
        if (value.trim() === was) continue

        changed[tagKey] = value
        // absent from `expected` would mean "believed to have no such tag", which is
        // only true when it really has none
        if (was) expected[tagKey] = was
    }

    if (!Object.keys(changed).length) {
        edits.delete(key)
        return false
    }

    edits.set(key, {
        type: stop.type,
        id: Number.parseInt(stop.id, 10),
        tags: changed,
        expected: expected,
    })
    return true
}

export const removeStopEdit = (stop) => edits.delete(additionKey(stop))

export const stopEditCount = () => edits.size

// what the stop will say once everything queued against it is uploaded
export function editedTags(stop) {
    const edit = getStopEdit(stop)
    if (!edit) return stop.tags ?? {}

    const result = { ...(stop.tags ?? {}) }

    for (const [key, value] of Object.entries(edit.tags)) {
        if (value.trim()) result[key] = value
        else delete result[key]
    }

    return result
}

// The tags every bus stop platform should carry, filled in where the mapper chose to on a
// stop lacking any of them. Mirrors PLATFORM_TAGS in naptan_tags.py, which lets each key
// write this value and no other.
export const PLATFORM_TAGS = { highway: "bus_stop", public_transport: "platform", bus: "yes" }

const platformFills = new Map()

// The platform tags the stop has no value for, or null when it needs none. A key holding
// something else, such as highway=platform on a platform way, is the mapper's to judge.
export function missingPlatformTags(stop) {
    const tags = stop.tags ?? {}
    const missing = Object.fromEntries(Object.entries(PLATFORM_TAGS).filter(([key]) => !(tags[key] ?? "").trim()))
    return Object.keys(missing).length ? missing : null
}

export const getPlatformFill = (stop) => platformFills.get(additionKey(stop))

// `tags` is what was missing when it was offered, so a stop is only given those
export function addPlatformFill(stop, tags) {
    platformFills.set(additionKey(stop), { type: stop.type, id: Number.parseInt(stop.id, 10), tags: tags })
}

export const removePlatformFill = (stop) => platformFills.delete(additionKey(stop))

// stops with any tag change to write: a fill, an accepted replacement or a hand edit
export const tagChangeCount = () => tagAdditionsPayload().length

// mirrors make_comment() in main.py, which counts one stop under each kind it carries
export function tagChangeCounts() {
    const counts = { edited: 0, naptan: 0, platform: 0 }

    for (const stop of tagAdditionsPayload()) {
        const filled = Object.keys(stop.tags).filter((key) => !stop.byHand.includes(key))
        if (stop.byHand.length) counts.edited++
        if (filled.some((key) => !(key in PLATFORM_TAGS))) counts.naptan++
        if (filled.some((key) => key in PLATFORM_TAGS)) counts.platform++
    }

    return counts
}

// A decision is remembered against the value NaPTAN gave at the time, so a stop whose
// NaPTAN record changes afterwards comes back for a fresh decision rather than keeping
// an answer to a question nobody was asked.
const decisions = new Map()

const decisionKey = (stop, tagKey, naptanValue) => `${additionKey(stop)}|${tagKey}|${naptanValue}`

export const getDecision = (stop, tagKey, naptanValue) =>
    decisions.get(decisionKey(stop, tagKey, naptanValue))?.decision

// `decision` is "naptan" to take NaPTAN's value, or "osm" to keep the one already there.
export function setDecision(stop, tagKey, naptanValue, decision) {
    decisions.set(decisionKey(stop, tagKey, naptanValue), {
        decision: decision,
        type: stop.type,
        id: Number.parseInt(stop.id, 10),
        tagKey: tagKey,
        naptanValue: naptanValue,
        // what the stop says now, so the upload can tell a value changed since
        osmValue: stop.tags?.[tagKey] ?? "",
    })
}

// The keys of `differing` this stop still has no answer for.
export const undecidedKeys = (stop, differing) =>
    Object.entries(differing ?? {})
        .filter(([tagKey, value]) => !getDecision(stop, tagKey, value))
        .map(([tagKey]) => tagKey)

export const hasUndecided = (stop, differing) => undecidedKeys(stop, differing).length > 0

const acceptedDecisions = () => Array.from(decisions.values()).filter((entry) => entry.decision === "naptan")

export const acceptedCount = () => acceptedDecisions().length

export function clearTagAdditions() {
    additions.clear()
    decisions.clear()
    edits.clear()
    platformFills.clear()
}

// Fills and accepted replacements for the same stop travel together, as one change to
// one element. `expected` carries what each replaced value was, so the upload refuses a
// value that has changed since rather than overwriting it.
export function tagAdditionsPayload() {
    const byStop = new Map()

    for (const addition of additions.values()) {
        byStop.set(`${addition.type},${addition.id}`, {
            type: addition.type,
            id: addition.id,
            tags: { ...addition.tags },
            expected: {},
            byHand: [],
        })
    }

    for (const entry of acceptedDecisions()) {
        const key = `${entry.type},${entry.id}`
        const stop = byStop.get(key) ?? {
            type: entry.type,
            id: entry.id,
            tags: {},
            expected: {},
            byHand: [],
        }

        stop.tags[entry.tagKey] = entry.naptanValue
        stop.expected[entry.tagKey] = entry.osmValue
        byStop.set(key, stop)
    }

    // neither NaPTAN's nor typed, and only ever a key the stop has none of
    for (const fill of platformFills.values()) {
        const key = `${fill.type},${fill.id}`
        const stop = byStop.get(key) ?? {
            type: fill.type,
            id: fill.id,
            tags: {},
            expected: {},
            byHand: [],
        }

        Object.assign(stop.tags, fill.tags)
        byStop.set(key, stop)
    }

    // last, so a value the mapper typed wins over the one NaPTAN offered for the same key
    for (const edit of edits.values()) {
        const key = `${edit.type},${edit.id}`
        const stop = byStop.get(key) ?? {
            type: edit.type,
            id: edit.id,
            tags: {},
            expected: {},
            byHand: [],
        }

        for (const [tagKey, value] of Object.entries(edit.tags)) {
            stop.tags[tagKey] = value
            stop.byHand.push(tagKey)

            if (edit.expected[tagKey]) stop.expected[tagKey] = edit.expected[tagKey]
            else delete stop.expected[tagKey]
        }

        byStop.set(key, stop)
    }

    return Array.from(byStop.values())
}
