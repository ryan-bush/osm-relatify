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

// stops with any NaPTAN change to write, a fill or an accepted replacement
export const tagChangeCount = () => tagAdditionsPayload().length

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
        })
    }

    for (const entry of acceptedDecisions()) {
        const key = `${entry.type},${entry.id}`
        const stop = byStop.get(key) ?? { type: entry.type, id: entry.id, tags: {}, expected: {} }

        stop.tags[entry.tagKey] = entry.naptanValue
        stop.expected[entry.tagKey] = entry.osmValue
        byStop.set(key, stop)
    }

    return Array.from(byStop.values())
}
