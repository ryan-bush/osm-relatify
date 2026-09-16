// The direction the mapper says buses use a road in, where the data would let the route
// go either way - most often to settle which way round a loop is driven. Like a U-turn
// override it lives in the browser only: nothing is uploaded.
import { map } from "./map.js"

// by the OSM way and the run of its nodes the direction was set on, as the pieces a
// way is cut into are renumbered by every download
const overrides = []

let currentWaysData = null

map.createPane("travelMarkers").style.zIndex = 410
const travelLayer = L.layerGroup().addTo(map)

const baseId = (id) => Number.parseInt(id.split("_")[0], 10)

// the override covering this piece of a way: one set on a run of nodes it lies within
function overrideFor(way) {
    return overrides.find((override) => {
        if (override.wayId !== baseId(way.id)) return false
        const at = override.nodes.indexOf(way.nodes[0])
        return at >= 0 && override.nodes[at + 1] === way.nodes[1]
    })
}

export const getTravel = (way) => overrideFor(way)?.travel ?? null

// `travel` is "forward" or "backward", along the way's own direction, or null for either
export function setTravel(way, travel) {
    const existing = overrideFor(way)
    if (existing) overrides.splice(overrides.indexOf(existing), 1)
    if (travel) overrides.push({ wayId: baseId(way.id), nodes: [...way.nodes], travel: travel })
    applyTravelOverrides(currentWaysData)
}

export function clearTravelOverrides() {
    overrides.length = 0
}

export function applyTravelOverrides(waysData) {
    currentWaysData = waysData
    if (!waysData) return

    for (const way of Object.values(waysData)) {
        const travel = getTravel(way)
        if (travel) way.travel = travel
        else delete way.travel
    }
}

// degrees clockwise from north, of a way driven forwards near `latlng`
export function headingAt(way, latlng) {
    let best = null

    for (let i = 0; i < way.latLngs.length - 1; i++) {
        const [latA, lngA] = way.latLngs[i]
        const [latB, lngB] = way.latLngs[i + 1]
        const midLat = (latA + latB) / 2
        const midLng = (lngA + lngB) / 2
        const distance = (midLat - latlng.lat) ** 2 + (midLng - latlng.lng) ** 2

        if (!best || distance < best.distance) {
            const dx = (lngB - lngA) * Math.cos((latA * Math.PI) / 180)
            best = { distance: distance, heading: (Math.atan2(dx, latB - latA) * 180) / Math.PI }
        }
    }

    return best ? (best.heading + 360) % 360 : 0
}

export const arrowSvg = (heading, size = 18) => `
    <svg width="${size}" height="${size}" viewBox="0 0 24 24" style="transform: rotate(${heading}deg)"
         aria-hidden="true">
        <path d="M12 2 L20 20 L12 15 L4 20 Z" fill="currentColor"/>
    </svg>`

// Without a marker the override is invisible state, as with a U-turn.
export function updateTravelMarkers(waysData) {
    currentWaysData = waysData
    travelLayer.clearLayers()
    if (!waysData) return

    for (const way of Object.values(waysData)) {
        const travel = way.member && getTravel(way)
        if (!travel || !way.midpoint) continue

        const heading = (headingAt(way, L.latLng(way.midpoint)) + (travel === "backward" ? 180 : 0)) % 360

        L.marker(way.midpoint, {
            pane: "travelMarkers",
            interactive: false,
            icon: L.divIcon({
                className: "travel-marker",
                html: arrowSvg(heading),
                iconSize: [18, 18],
                iconAnchor: [9, 9],
            }),
        }).addTo(travelLayer)
    }
}

export const refreshTravelMarkers = () => updateTravelMarkers(currentWaysData)
