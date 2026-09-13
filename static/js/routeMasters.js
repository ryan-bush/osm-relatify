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

export function setRouteMasters(data) {
    mastersKnown = data?.routeMasters != null
    currentMasters = data?.routeMasters ?? []
    candidateMasters = data?.routeMasterCandidates ?? []
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
