// The order the mapper arranged the route's stops in, and what each visit is for.
//
// The route still decides the order: a relation lists its stops the way the bus drives
// past them. Where the ways leave a choice - a lane served on the way into a village or
// on the way out - the route takes the one that keeps to this plan. Until the mapper
// arranges anything there is no plan, and the route picks as it always has.
//
// Each entry is one visit: { id, role }, where id is the stop's platform, or its stop
// position without one, and role is "auto", "both", "entry_only" or "exit_only". A stop
// served twice is in the plan twice.

export const ROLE_LABELS = {
    both: "Pick up and set down",
    entry_only: "Pick up only",
    exit_only: "Set down only",
}

let stopPlan = null

export const isStopPlanSet = () => stopPlan !== null

export const stopPlanPayload = () => stopPlan ?? []

export function setStopPlan(entries) {
    stopPlan = entries
}

export function resetStopPlan() {
    stopPlan = null
}

export const visitId = (collection) => (collection.platform ?? collection.stop).id

// The route's stops as a plan, keeping the role the mapper set for each visit: the nth
// visit to a stop has the role of the nth time the plan has it.
export function planFromRoute(busStops, plan = stopPlan) {
    const planned = new Map()
    for (const entry of plan ?? []) {
        if (!planned.has(entry.id)) planned.set(entry.id, [])
        planned.get(entry.id).push(entry.role)
    }

    const seen = new Map()
    return busStops.map((collection) => {
        const id = visitId(collection)
        const visit = seen.get(id) ?? 0
        seen.set(id, visit + 1)
        return { id, role: planned.get(id)?.[visit] ?? "auto" }
    })
}

export function moveVisit(entries, from, to) {
    const result = [...entries]
    const [moved] = result.splice(from, 1)
    result.splice(to, 0, moved)
    return result
}

// The route lists the extra visit wherever it next serves the stop; until then it goes
// right after the first.
export function serveAgain(entries, index) {
    const result = [...entries]
    result.splice(index + 1, 0, { id: entries[index].id, role: "auto" })
    return result
}

export function removeVisit(entries, index) {
    return entries.filter((_, i) => i !== index)
}

export function setRole(entries, index, role) {
    return entries.map((entry, i) => (i === index ? { ...entry, role } : entry))
}

// How many of the planned visits the route could not make where the plan has them.
// Stops no longer on the route are left out: they have no place to be put in.
export function visitsOutOfPlace(plan, listedIds) {
    const listed = new Set(listedIds)
    const wanted = plan.map((entry) => entry.id).filter((id) => listed.has(id))

    // the longest common subsequence of the two
    const row = new Array(wanted.length + 1).fill(0)
    for (const id of listedIds) {
        let diagonal = 0
        for (let j = 1; j <= wanted.length; j++) {
            const above = row[j]
            if (wanted[j - 1] === id) row[j] = diagonal + 1
            else if (row[j - 1] > above) row[j] = row[j - 1]
            diagonal = above
        }
    }

    return wanted.length - row[wanted.length]
}
