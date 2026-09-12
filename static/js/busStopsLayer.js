import {
    clearBusStopsPopup,
    showAllTagsForm,
    showContextMenu,
    showNaptanTagsForm,
    showNewStopForm,
} from "./busStopsContext.js"
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
import { addTagAddition, clearTagAdditions, getTagAddition, removeTagAddition } from "./naptanTagAdditions.js"
import { relationTags } from "./tagEditor.js"
import { escapeHtml, getBusCollectionName, haversine_distance } from "./utils.js"
import { waysRBush } from "./waysLayer.js"
import { requestCalcBusRoute } from "./waysRoute.js"

export let busStopData = null

// stops in NaPTAN that OSM is missing; empty unless the server has NaPTAN enabled
let naptanStops = []

// tags NaPTAN has for stops already in OSM, keyed like the stops' type and id
let naptanTagSuggestions = new Map()

const stopKey = (stop) => `${stop.type},${stop.id}`

const naptanStopsLayer = L.layerGroup().addTo(map)
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

        // worked out afresh for the whole downloaded area, so replaced rather than merged
        naptanStops = fetchData.naptanStops ?? []
        naptanTagSuggestions = new Map((fetchData.naptanTags ?? []).map((suggestion) => [stopKey(suggestion), suggestion]))
    } else {
        busStopData = null
        naptanStops = []
        naptanTagSuggestions = new Map()
        clearNewStops()
        clearTagAdditions()
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
    naptanStopsLayer.clearLayers()

    if (!(busStopData && waysRBush)) return

    addNaptanStopsToLayer()

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

const isNearRoute = (latLng) =>
    waysRBush.search({
        minX: latLng[0],
        minY: latLng[1],
        maxX: latLng[0],
        maxY: latLng[1],
    }).length > 0

function addBusStopToLayer(i, stop, name, role) {
    if (!stop.member && !isNearRoute(stop.latLng)) return

    const addToLayer = stop.member ? activeBusStopsLayer : inactiveBusStopsLayer

    const suggestion = naptanTagSuggestions.get(stopKey(stop))
    const addition = getTagAddition(stop)

    const marker = L.marker(stop.latLng, {
        icon: createBusStopIcon(
            `/static/img/bus_stop_${stop.member ? "on" : "off"}.webp`,
            stop.member ? 24 : 20,
            addition ? "bus-stop-icon bus-stop-tags-added" : "bus-stop-icon",
        ),
        opacity: stop.member ? 1 : 0.8,
    }).addTo(addToLayer)

    const naptanNote = addition
        ? "<br><small>NaPTAN tags will be added</small>"
        : suggestion
          ? "<br><small>Missing tags NaPTAN has</small>"
          : ""

    marker.bindTooltip(name + naptanNote, {
        direction: "top",
        offset: [0, -10],
    })

    marker.on("click", () => setMemberState(i, !stop.member))
    marker.on("contextmenu", (e) =>
        showContextMenu(e, stop, naptanTagsAction(e, stop, suggestion, addition), () =>
            showAllTagsForm(e.latlng, tagSections(busStopData[i])),
        ),
    )
}

// the platform and the stop position are separate elements, each with its own tags
function tagSections(collection) {
    const sections = []

    for (const [label, stop] of [
        ["Platform", collection.platform],
        ["Stop position", collection.stop],
    ]) {
        if (!stop) continue
        // a way the route splits carries a suffixed id, which OSM knows nothing about
        sections.push({ label: `${label} · ${stop.type}/${stop.id.split("_")[0]}`, tags: stop.tags })
    }

    return sections
}

function onTagAdditionsChanged() {
    clearBusStopsPopup()
    updateBusStopsVisibility()
    // a tag-only change still has something to upload when the route itself is unchanged
    requestCalcBusRoute()
}

function naptanTagsAction(e, stop, suggestion, addition) {
    if (!suggestion && !addition) return null

    return {
        label: addition ? "NaPTAN <b>tags</b> added" : "Add NaPTAN <b>tags</b>",
        onClick: () =>
            showNaptanTagsForm(e.latlng, {
                tags: addition?.tags ?? suggestion.tags,
                added: Boolean(addition),
                onAdd: () => {
                    addTagAddition(stop, suggestion.tags)
                    onTagAdditionsChanged()
                },
                onRemove: () => {
                    removeTagAddition(stop)
                    onTagAdditionsChanged()
                },
            }),
    }
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

function addNaptanStopsToLayer() {
    // a stop already added from a suggestion takes its place on the map
    const added = new Set(
        newStopCollections()
            .map((entry) => entry.platform.tags["naptan:AtcoCode"])
            .filter(Boolean),
    )

    for (const naptanStop of naptanStops) {
        if (added.has(naptanStop.atcoCode) || !isNearRoute(naptanStop.latLng)) continue

        const marker = L.marker(naptanStop.latLng, {
            icon: createBusStopIcon("/static/img/bus_stop_off.webp", 20, "bus-stop-icon naptan-stop-icon"),
        }).addTo(naptanStopsLayer)

        const indicator = naptanStop.indicator ? ` <i>${escapeHtml(naptanStop.indicator)}</i>` : ""
        marker.bindTooltip(`${escapeHtml(naptanStop.name)}${indicator}<br><small>In NaPTAN, missing from OSM</small>`, {
            direction: "top",
            offset: [0, -10],
        })

        const suggest = () => {
            showNewStopForm(L.latLng(naptanStop.latLng), {
                tags: naptanStop.tags,
                nearby: findNearbyStop(naptanStop.latLng, null),
                onSave: (tags) => {
                    addNewStop([...naptanStop.latLng], tags)
                    syncNewStops()
                },
            })
        }

        marker.on("click", suggest)
        marker.on("contextmenu", suggest)
    }
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
