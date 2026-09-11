import { clearBusStopsPopup, showContextMenu, showNewStopForm } from "./busStopsContext.js"
import {
    addNewStop,
    clearNewStops,
    isNewStop,
    moveNewStop,
    newStopCollections,
    removeNewStop,
    updateNewStop,
} from "./busStopsNew.js"
import { map } from "./map.js"
import { relationTags } from "./tagEditor.js"
import { escapeHtml, getBusCollectionName, haversine_distance } from "./utils.js"
import { waysRBush } from "./waysLayer.js"
import { requestCalcBusRoute } from "./waysRoute.js"

export let busStopData = null

const inactiveBusStopsLayer = L.layerGroup().addTo(map)
const activeBusStopsLayer = L.layerGroup().addTo(map)

// the radius bus_collection_builder.py groups stops within (BUS_COLLECTION_SEARCH_AREA);
// a new stop closer than this to another is most likely a duplicate of it
const DUPLICATE_STOP_DISTANCE = 50

export function processBusStopData(fetchData) {
    if (fetchData) {
        if (fetchData.fetchMerge) {
            const memberSet = new Set()

            if (busStopData) {
                for (const entry of busStopData) {
                    if (entry.platform) {
                        if (entry.platform.member) {
                            memberSet.add(`${entry.platform.type},${entry.platform.id}`)
                        }
                    } else if (entry.stop) {
                        if (entry.stop.member) {
                            memberSet.add(`${entry.stop.type},${entry.stop.id}`)
                        }
                    }
                }
            }

            busStopData = fetchData.busStops

            for (const entry of busStopData) {
                if (entry.platform) {
                    const member = memberSet.has(`${entry.platform.type},${entry.platform.id}`)

                    entry.platform.member = member

                    if (entry.stop) entry.stop.member = member
                } else if (entry.stop) {
                    const member = memberSet.has(`${entry.stop.type},${entry.stop.id}`)

                    if (entry.platform) entry.platform.member = member

                    entry.stop.member = member
                }
            }
        } else {
            busStopData = fetchData.busStops
        }

        // stops the user placed are not in OSM yet, so no download brings them back
        busStopData.push(...newStopCollections())
    } else {
        busStopData = null
        clearNewStops()
    }

    onBusStopDataChanged()
}

function onBusStopDataChanged() {
    clearBusStopsPopup()
    updateBusStopsVisibility()
    requestCalcBusRoute()
}

function syncNewStops() {
    busStopData = busStopData.filter((entry) => !isNewStop(entry.platform)).concat(newStopCollections())
    onBusStopDataChanged()
}

export function updateBusStopsVisibility() {
    activeBusStopsLayer.clearLayers()
    inactiveBusStopsLayer.clearLayers()

    if (!(busStopData && waysRBush)) return

    for (const [i, busStopCollection] of busStopData.entries()) {
        const name = getBusCollectionName(busStopCollection)

        if (isNewStop(busStopCollection.platform)) {
            addNewStopToLayer(busStopCollection.platform)
        } else if (busStopCollection.platform) {
            addBusStopToLayer(i, busStopCollection.platform, name, "platform")
        } else if (busStopCollection.stop) {
            addBusStopToLayer(i, busStopCollection.stop, name, "stop")
        }
    }
}

const setMemberState = (i, member) => {
    const entry = busStopData[i]

    if (entry.platform) {
        entry.platform.member = member
    }

    if (entry.stop) {
        entry.stop.member = member
    }

    onBusStopDataChanged()
}

function createBusStopIcon(iconUrl, size, className = "bus-stop-icon") {
    return L.icon({
        className: className,
        iconUrl: iconUrl,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
    })
}

function addBusStopToLayer(i, stop, name, role) {
    if (!stop.member) {
        const nearby = waysRBush.search({
            minX: stop.latLng[0],
            minY: stop.latLng[1],
            maxX: stop.latLng[0],
            maxY: stop.latLng[1],
        })

        if (nearby.length === 0) return
    }

    const addToLayer = stop.member ? activeBusStopsLayer : inactiveBusStopsLayer

    const marker = L.marker(stop.latLng, {
        icon: createBusStopIcon(`/static/img/bus_stop_${stop.member ? "on" : "off"}.webp`, stop.member ? 24 : 20),
        opacity: stop.member ? 1 : 0.8,
    }).addTo(addToLayer)

    marker.bindTooltip(name, {
        direction: "top",
        offset: [0, -10],
    })

    marker.on("click", () => setMemberState(i, !stop.member))
    marker.on("contextmenu", (e) => showContextMenu(e, stop))
}

// new stops are bus platforms; tram stops are tagged differently and sit on the track
const canAddStops = () => busStopData !== null && ["bus", "trolleybus"].includes(relationTags?.route)

function findNearbyStop(latLng, except) {
    let nearest = null

    for (const entry of busStopData) {
        const stop = entry.platform ?? entry.stop
        if (stop === except) continue

        const distance = haversine_distance(latLng, stop.latLng)
        if (distance > DUPLICATE_STOP_DISTANCE || (nearest && distance >= nearest.distance)) continue

        nearest = { name: entry.platform?.name || entry.stop?.name || "", distance }
    }

    return nearest
}

map.on("contextmenu", (e) => {
    if (!canAddStops()) return

    const latLng = [e.latlng.lat, e.latlng.lng]

    showNewStopForm(e.latlng, {
        nearby: findNearbyStop(latLng, null),
        onSave: (tags) => {
            addNewStop(latLng, tags)
            syncNewStops()
        },
    })
})

function editNewStop(e, stop) {
    showNewStopForm(e.latlng, {
        stop: stop,
        nearby: findNearbyStop(stop.latLng, stop),
        onSave: (tags) => {
            updateNewStop(stop, tags)
            syncNewStops()
        },
        onDelete: () => {
            removeNewStop(stop)
            syncNewStops()
        },
    })
}

function addNewStopToLayer(stop) {
    const marker = L.marker(stop.latLng, {
        icon: createBusStopIcon("/static/img/bus_stop_on.webp", 24, "bus-stop-icon bus-stop-new"),
        draggable: true,
        autoPan: true,
    }).addTo(activeBusStopsLayer)

    marker.bindTooltip(`${escapeHtml(stop.name)} <i>(new)</i>`, {
        direction: "top",
        offset: [0, -10],
    })

    marker.on("dragend", () => {
        const { lat, lng } = marker.getLatLng()
        moveNewStop(stop, [lat, lng])
        syncNewStops()
    })

    marker.on("click", (e) => editNewStop(e, stop))
    marker.on("contextmenu", (e) => editNewStop(e, stop))
}
