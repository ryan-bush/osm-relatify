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

// The spot on the route a stop at `latLng` halts at, or null when no member way is close
// enough. Ways the route splits are left out: their node lists are rebuilt on upload,
// which a node inserted here would be lost in.
export function planStopPosition(latLng, waysData) {
    if (!waysData) return null

    let best = null

    for (const way of Object.values(waysData)) {
        if (!way.member || way.id.includes("_")) continue

        for (let i = 0; i < way.latLngs.length - 1; i++) {
            const a = way.latLngs[i]
            const b = way.latLngs[i + 1]
            const hit = projectOntoSegment(latLng, a, b)

            if (!hit || hit.distance > MAX_ROAD_DISTANCE) continue
            if (best && hit.distance >= best.distance) continue

            best = {
                distance: hit.distance,
                wayId: Number.parseInt(way.id, 10),
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
        if (!byWay.has(placement.wayId)) byWay.set(placement.wayId, [])
        byWay.get(placement.wayId).push(placement)
    }

    const result = { ...ways }

    for (const [wayId, wayPlacements] of byWay) {
        const way = result[`${wayId}`]
        // gone, or split, since the stop was placed; the stop position is dropped with it
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

        result[`${wayId}`] = { ...way, latLngs: latLngs }
    }

    return result
}

function distanceAlong(from, placement) {
    const [xScale, yScale] = metreScales(from[0])
    return Math.hypot((placement.latLng[1] - from[1]) * xScale, (placement.latLng[0] - from[0]) * yScale)
}
