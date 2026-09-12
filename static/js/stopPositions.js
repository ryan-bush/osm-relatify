// Where on the road a new stop's bus actually halts. PTv2 wants a node on the way
// itself, tagged public_transport=stop_position, alongside the platform beside the road.
// Nothing here exists in OSM until upload: the node carries a negative id like the
// platform does, and the way only gains it in the browser until then.

// beyond this the platform is not really beside this road, so no stop position is offered
const MAX_ROAD_DISTANCE = 30 // meters
// never put a new node on top of one the way already has, which would be a duplicate
const MIN_NODE_GAP = 0.5 // meters

// flat enough over the few tens of metres that matter
const metreScales = (lat) => [111_320 * Math.cos((lat * Math.PI) / 180), 110_540]

// how far `latLng` is from the segment a->b, and how far along it the closest point sits
function projectOntoSegment(latLng, a, b) {
    const [xScale, yScale] = metreScales(latLng[0])
    const px = (a[1] - latLng[1]) * xScale
    const py = (a[0] - latLng[0]) * yScale
    const dx = (b[1] - a[1]) * xScale
    const dy = (b[0] - a[0]) * yScale

    const lengthSq = dx * dx + dy * dy
    if (!lengthSq) return null

    const length = Math.sqrt(lengthSq)
    // a stop right at an existing node would otherwise duplicate it
    if (length < MIN_NODE_GAP * 2) return null

    const gap = MIN_NODE_GAP / length
    const t = Math.max(gap, Math.min(1 - gap, -(px * dx + py * dy) / lengthSq))

    return { distance: Math.hypot(px + t * dx, py + t * dy), t: t }
}

const interpolate = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]

// Ways arrive cut up at every intersection, as `<id>_<n>_<of>`, which is how the route
// is worked out. The upload puts them back together again, so most of these are not
// ways the route really splits.
const idParts = (id) => {
    const [wayId, extraNum, maxNum] = id.split("_").map(Number)
    return { wayId: wayId, extraNum: extraNum ?? null, maxNum: maxNum ?? null }
}

// A way is only really split when the route does not use the whole of it, in which case
// the upload rewrites its nodes and a node inserted here would be lost in that.
function keepsItsNodesOnUpload(way, waysData) {
    const { wayId, maxNum } = idParts(way.id)
    if (maxNum === null) return true

    let members = 0
    for (const other of Object.values(waysData)) {
        if (other.member && idParts(other.id).wayId === wayId) members++
    }

    // every piece of the original way is used, so the upload merges them back into it
    return members === maxNum
}

// The spot on the route a stop at `latLng` halts at, or null when no member way is close
// enough to carry one.
export function planStopPosition(latLng, waysData) {
    if (!waysData) return null

    let best = null

    for (const way of Object.values(waysData)) {
        if (!way.member) continue

        for (let i = 0; i < way.latLngs.length - 1; i++) {
            const a = way.latLngs[i]
            const b = way.latLngs[i + 1]
            const hit = projectOntoSegment(latLng, a, b)

            if (!hit || hit.distance > MAX_ROAD_DISTANCE) continue
            if (best && hit.distance >= best.distance) continue
            // checked last, being much the most expensive of the three
            if (!keepsItsNodesOnUpload(way, waysData)) break

            best = {
                distance: hit.distance,
                // the node pair is adjacent in the whole way as well as in this piece of
                // it, so the upload works from the real way rather than the piece
                wayId: idParts(way.id).wayId,
                segmentId: way.id,
                afterNode: way.nodes[i],
                beforeNode: way.nodes[i + 1],
                latLng: interpolate(a, b, hit.t),
            }
        }
    }

    return best
}

// The ways to send for the route calculation, with each new stop position put in as a
// vertex. Without it the calculation drops a stop position it cannot find on the route.
// The originals are left alone, so planning stays based on what OSM actually has.
export function insertStopPositionsIntoWays(ways, placements) {
    if (!placements.length) return ways

    const byWay = new Map()
    for (const placement of placements) {
        if (!byWay.has(placement.segmentId)) byWay.set(placement.segmentId, [])
        byWay.get(placement.segmentId).push(placement)
    }

    const result = { ...ways }

    for (const [segmentId, wayPlacements] of byWay) {
        const way = result[segmentId]
        // gone since the stop was placed; the stop position is dropped along with it
        if (!way) continue

        const latLngs = []

        for (let i = 0; i < way.latLngs.length; i++) {
            latLngs.push(way.latLngs[i])

            const here = wayPlacements
                .filter((p) => way.nodes[i] === p.afterNode && way.nodes[i + 1] === p.beforeNode)
                // several stops in one segment keep the order they sit in along it
                .sort((p, q) => distanceAlong(way.latLngs[i], p) - distanceAlong(way.latLngs[i], q))

            for (const placement of here) latLngs.push(placement.latLng)
        }

        result[segmentId] = { ...way, latLngs: latLngs }
    }

    return result
}

function distanceAlong(from, placement) {
    const [xScale, yScale] = metreScales(from[0])
    return Math.hypot((placement.latLng[1] - from[1]) * xScale, (placement.latLng[0] - from[0]) * yScale)
}

// Pending stop positions, keyed by the platform they serve. A platform may be one being
// created by this changeset or one that has been in OSM all along; either way the node
// on the road is new, so it carries a negative id until upload.
const pending = new Map()
let nextId = -1

export const nextPlaceholderId = () => nextId--

export const platformKey = (platform) => `${platform.type},${platform.id}`

// Puts a stop position on the road for `platform`, or takes it away again when
// `placement` is null. The node keeps its id while only being moved, so anything already
// referring to it still does.
export function setStopPosition(platform, placement, name = "") {
    const key = platformKey(platform)

    if (!placement) {
        pending.delete(key)
        return null
    }

    const node = pending.get(key)?.node ?? {
        id: `${nextPlaceholderId()}`,
        type: "node",
        member: true,
        tags: { public_transport: "stop_position" },
        highway: null,
        public_transport: "stop_position",
    }

    node.latLng = placement.latLng
    node.name = name
    node.groupName = name.toLowerCase()
    // the platform decides whether the route calls here; the two never disagree
    node.member = platform.member !== false

    pending.set(key, { node: node, placement: placement, name: name })
    return node
}

export const getStopPositionNode = (platform) => pending.get(platformKey(platform))?.node ?? null

// The stop the node serves may be renamed after the node was placed, by a NaPTAN
// disagreement the mapper settled later; the node carries the name it will end up with.
export function renameStopPosition(platform, name) {
    const entry = pending.get(platformKey(platform))
    if (!entry || entry.name === name) return false

    entry.name = name
    entry.node.name = name
    entry.node.groupName = name.toLowerCase()
    return true
}

export const hasStopPosition = (platform) => pending.has(platformKey(platform))

export const removeStopPosition = (platform) => pending.delete(platformKey(platform))

export function clearStopPositions() {
    pending.clear()
    nextId = -1
}

// A stop position belongs to the route through the stop it serves. One whose stop has
// left the route would be created on the road as a member of nothing, so it is never
// uploaded; the caller drops it, and this makes sure of it.
const uploadable = () => Array.from(pending.values()).filter(({ node }) => node.member !== false)

export const stopPositionCount = () => uploadable().length

// where each one goes, for the route calculation to allow for
export const stopPositionPlacements = () => Array.from(pending.values(), (entry) => entry.placement)

export const stopPositionsPayload = () =>
    uploadable().map(({ node, placement, name }) => ({
        id: Number.parseInt(node.id, 10),
        lat: node.latLng[0],
        lon: node.latLng[1],
        wayId: placement.wayId,
        afterNode: placement.afterNode,
        beforeNode: placement.beforeNode,
        name: name,
    }))
