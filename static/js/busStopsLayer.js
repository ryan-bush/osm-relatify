import {
    clearBusStopsPopup,
    showAllTagsForm,
    showContextMenu,
    showNaptanTagsForm,
    showNewStopForm,
    showStopPositionForm,
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
import {
    getStopPositionNode,
    hasStopPosition,
    planStopPosition,
    removeStopPosition,
    setStopPosition,
} from "./stopPositions.js"
import { relationTags } from "./tagEditor.js"
import { escapeHtml, getBusCollectionName, haversine_distance } from "./utils.js"
import { waysData, waysRBush } from "./waysLayer.js"
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
        applyPendingStopPositions()

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
    applyPendingStopPositions()
    onBusStopDataChanged()
}

// A stop position the user added to a stop already in OSM lives only in the browser, so
// each fresh download has to be given it again.
function applyPendingStopPositions() {
    for (const entry of busStopData) {
        if (!entry.platform || isNewStop(entry.platform)) continue

        const node = getStopPositionNode(entry.platform)
        if (!node) continue

        // OSM has gained one since, so ours is not needed after all
        if (entry.stop && !isNewStop(entry.stop)) {
            removeStopPosition(entry.platform)
            continue
        }

        // the stop is no longer in the route, so neither is its stop position
        if (!entry.platform.member) {
            removeStopPosition(entry.platform)
            continue
        }

        // the platform decides whether the route calls here; the two never disagree
        node.member = entry.platform.member !== false
        entry.stop = node
    }
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

    // a stop position is only ever uploaded as part of the stop it serves
    if (!member && entry.platform && entry.stop && isNewStop(entry.stop)) {
        removeStopPosition(entry.platform)
        entry.stop = null
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
        showContextMenu(
            e,
            stop,
            naptanTagsAction(e, stop, suggestion, addition),
            () => showAllTagsForm(e.latlng, tagSections(busStopData[i])),
            stopPositionAction(e, busStopData[i]),
        ),
    )

    if (busStopData[i].stop && isNewStop(busStopData[i].stop)) {
        addStopPositionToLayer(stop.latLng, busStopData[i].stop, addToLayer)
    }
}

// Offers a stop position on the road for a stop already in OSM that has none, or takes
// back one that has not been uploaded yet.
function stopPositionAction(e, collection) {
    const platform = collection.platform
    if (!platform || !canAddStops()) return null

    // the node joins the relation along with the stop, so a stop the route does not call
    // at would leave it on the road as a member of nothing
    if (!platform.member) return null

    const added = hasStopPosition(platform)

    // one it already has, from OSM, is nothing for us to add to
    if (!added && collection.stop) return null

    const placement = added ? null : planStopPosition(platform.latLng, waysData)
    if (!added && !placement) return null

    return {
        label: added ? "Stop <b>position</b> added" : "Add stop <b>position</b>",
        onClick: () =>
            showStopPositionForm(e.latlng, {
                added: added,
                distance: placement?.distance,
                tags: stopPositionTagsFor(platform),
                onAdd: () => {
                    setStopPosition(platform, placement, platform.tags?.name ?? "")
                    onStopPositionsChanged()
                },
                onRemove: () => {
                    removeStopPosition(platform)
                    onStopPositionsChanged()
                },
            }),
    }
}

// mirrors make_stop_position_tags() in bus_stop_creation.py
function stopPositionTagsFor(platform) {
    const tags = { public_transport: "stop_position" }
    if (relationTags?.route) tags[relationTags.route] = "yes"
    const name = platform.tags?.name?.trim()
    if (name) tags.name = name
    return tags
}

function onStopPositionsChanged() {
    clearBusStopsPopup()
    syncNewStops()
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

// Where the stop position goes, or null when the user did not ask for one or no route
// road is close enough to carry it.
const stopPositionFor = (latLng, wanted) => (wanted ? planStopPosition(latLng, waysData) : null)

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
        routeType: relationTags?.route,
        stopPosition: { available: Boolean(planStopPosition(latLng, waysData)), checked: true },
        onSave: (tags, wantStopPosition) => {
            addNewStop(latLng, tags, stopPositionFor(latLng, wantStopPosition))
            syncNewStops()
        },
    })
})

function editNewStop(e, stop) {
    showNewStopForm(e.latlng, {
        stop: stop,
        nearby: findNearbyStop(stop.latLng, stop),
        routeType: relationTags?.route,
        stopPosition: {
            available: Boolean(planStopPosition(stop.latLng, waysData)),
            checked: Boolean(stop.stopPosition),
        },
        onSave: (tags, wantStopPosition) => {
            updateNewStop(stop, tags, stopPositionFor(stop.latLng, wantStopPosition))
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
                routeType: relationTags?.route,
                stopPosition: {
                    available: Boolean(planStopPosition(naptanStop.latLng, waysData)),
                    checked: true,
                },
                onSave: (tags, wantStopPosition) => {
                    const latLng = [...naptanStop.latLng]
                    addNewStop(latLng, tags, stopPositionFor(latLng, wantStopPosition))
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
        const latLng = [lat, lng]
        // a stop that had a stop position keeps one, worked out afresh for where it now is
        moveNewStop(stop, latLng, stop.stopPosition ? stopPositionFor(latLng, true) : null)
        syncNewStops()
    })

    marker.on("click", (e) => editNewStop(e, stop))
    marker.on("contextmenu", (e) => editNewStop(e, stop))

    const node = getStopPositionNode(stop)
    if (node) addStopPositionToLayer(stop.latLng, node, activeBusStopsLayer)
}

// the node that will go on the road, shown so its place along the route is obvious
function addStopPositionToLayer(platformLatLng, node, layer) {
    const latLng = node.latLng

    L.circleMarker(latLng, {
        radius: 5,
        weight: 2,
        color: "#e0a800",
        fillColor: "#fff",
        fillOpacity: 1,
    })
        .addTo(layer)
        .bindTooltip(`${escapeHtml(node.name)} <i>(new stop position)</i>`, {
            direction: "top",
            offset: [0, -8],
        })

    L.polyline([platformLatLng, latLng], {
        color: "#e0a800",
        weight: 2,
        dashArray: "3 3",
        interactive: false,
    }).addTo(layer)
}
