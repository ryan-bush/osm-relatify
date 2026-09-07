import { map } from "./map.js"

// Some roads are turned around at without being tagged highway=turning_circle,
// which is the only thing the router accepts as permission to make a U-turn.
// These overrides let the user grant that permission per way end, on top of
// whatever the OSM data already says. They live in the browser only: nothing is
// uploaded, and they are re-applied after every (re)download of the relation.
const overrides = new Set()

// Cached so the context menu can inspect the network and redraw markers without
// importing waysLayer, which imports the context menu in turn.
let currentWaysData = null

const overrideKey = (wayId, isStart) => `${wayId}|${isStart ? "start" : "end"}`

map.createPane("uTurnMarkers").style.zIndex = 400
const uTurnLayer = L.layerGroup().addTo(map)

export function clearUTurnOverrides() {
    overrides.clear()
}

// The OSM-derived flags are the baseline; an override can only add a U-turn,
// never remove one that a turning circle already allows.
export function applyUTurnOverrides(waysData) {
    currentWaysData = waysData
    if (!waysData) return

    for (const way of Object.values(waysData)) {
        if (overrides.has(overrideKey(way.id, true)))
            way.turn_in_place_start = true
        if (overrides.has(overrideKey(way.id, false)))
            way.turn_in_place_end = true
    }
}

export function isUTurnAllowed(way, isStart) {
    return isStart ? way.turn_in_place_start : way.turn_in_place_end
}

// A U-turn allowed by a turning circle in OSM is not ours to take away.
export function isUTurnFromOsm(way, isStart) {
    return (
        isUTurnAllowed(way, isStart) &&
        !overrides.has(overrideKey(way.id, isStart))
    )
}

export function toggleUTurn(way, isStart) {
    const key = overrideKey(way.id, isStart)

    if (overrides.has(key)) {
        overrides.delete(key)
        if (isStart) way.turn_in_place_start = false
        else way.turn_in_place_end = false
    } else {
        overrides.add(key)
        if (isStart) way.turn_in_place_start = true
        else way.turn_in_place_end = true
    }
}

export const wayEndLatLng = (way, isStart) =>
    isStart ? way.latLngs[0] : way.latLngs[way.latLngs.length - 1]

const sameLatLng = (a, b) => a[0] === b[0] && a[1] === b[1]

// build_graph() models a U-turn by wiring the way's far end back to whatever its
// near end connects to. At a dead end that is exactly "drive in, turn, drive out"
// and costs the search nothing, because the end had no neighbours to begin with.
// Anywhere else the same edge is a teleport between two distinct junctions, and
// the extra branching is enough to make the route search time out.
export function isDeadEnd(way, isStart) {
    if (!currentWaysData) return false

    const latLng = wayEndLatLng(way, isStart)

    for (const connectedId of way.connectedTo) {
        const other = currentWaysData[connectedId]
        if (!other) continue

        // as in find_connections_at(): a road continues here only if another way
        // starts or ends at this exact node
        if (sameLatLng(other.latLngs[0], latLng)) return false
        if (sameLatLng(other.latLngs[other.latLngs.length - 1], latLng))
            return false
    }

    return true
}

// Which end of the way the user actually right-clicked on.
export function nearestWayEnd(way, latlng) {
    const start = way.latLngs[0]
    const end = way.latLngs[way.latLngs.length - 1]

    const distanceSq = ([lat, lng]) =>
        (lat - latlng.lat) ** 2 + (lng - latlng.lng) ** 2

    return distanceSq(start) <= distanceSq(end)
}

export const refreshUTurnMarkers = () => updateUTurnMarkers(currentWaysData)

// Without a marker the override is invisible state: the route silently changes
// and there is no way to find what was toggled, or to toggle it back.
export function updateUTurnMarkers(waysData) {
    currentWaysData = waysData
    uTurnLayer.clearLayers()

    if (!waysData) return

    for (const way of Object.values(waysData)) {
        if (!way.member) continue

        for (const isStart of [true, false]) {
            if (!overrides.has(overrideKey(way.id, isStart))) continue

            L.circleMarker(wayEndLatLng(way, isStart), {
                pane: "uTurnMarkers",
                radius: 7,
                weight: 2,
                color: "#7b2ff7",
                fillColor: "#ffffff",
                fillOpacity: 1,
                interactive: false,
            }).addTo(uTurnLayer)
        }
    }
}
