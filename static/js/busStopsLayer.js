import {
    clearBusStopsPopup,
    showAllTagsForm,
    showContextMenu,
    showNaptanDifferencesForm,
    showNaptanTagsForm,
    showNewStopForm,
    showStopAreaForm,
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
import {
    addTagAddition,
    clearTagAdditions,
    getDecision,
    getTagAddition,
    hasUndecided,
    removeTagAddition,
    setDecision,
    undecidedKeys,
} from "./naptanTagAdditions.js"
import {
    addStopArea,
    clearStopAreas,
    existingAreaFor,
    getPendingStopArea,
    groupMembers,
    reconcileStopAreas,
    removeStopArea,
    renameStopArea,
    setExistingStopAreas,
    stopAreaSignature,
} from "./stopAreas.js"
import {
    getStopPositionNode,
    getStopPositionPlacement,
    hasStopPosition,
    planStopPosition,
    removeStopPosition,
    renameStopPosition,
    setStopPosition,
    setStopPositionDirection,
} from "./stopPositions.js"
import { effectiveName } from "./stopNames.js"
import { relationTags } from "./tagEditor.js"
import { escapeHtml, getBusCollectionName, haversine_distance } from "./utils.js"
import { waysData, waysRBush } from "./waysLayer.js"
import { requestCalcBusRoute, routeData } from "./waysRoute.js"

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
        setExistingStopAreas(fetchData.stopAreas)
        reconcileGroups()
        naptanStops = fetchData.naptanStops ?? []
        naptanTagSuggestions = new Map((fetchData.naptanTags ?? []).map((suggestion) => [stopKey(suggestion), suggestion]))

        // after the suggestions, which is where an accepted rename is looked up
        refreshDerivedNames()
    } else {
        busStopData = null
        naptanStops = []
        naptanTagSuggestions = new Map()
        setExistingStopAreas([])
        clearNewStops()
        clearTagAdditions()
        clearStopAreas()
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
    // only a stop the route calls at has to be decided before uploading
    const undecided = stop.member && hasUndecided(stop, suggestion?.differing)

    const marker = L.marker(stop.latLng, {
        icon: createBusStopIcon(
            `/static/img/bus_stop_${stop.member ? "on" : "off"}.webp`,
            stop.member ? 24 : 20,
            undecided
                ? "bus-stop-icon bus-stop-naptan-undecided"
                : addition
                  ? "bus-stop-icon bus-stop-tags-added"
                  : "bus-stop-icon",
        ),
        opacity: stop.member ? 1 : 0.8,
    }).addTo(addToLayer)

    const naptanNote = undecided
        ? "<br><small>NaPTAN disagrees — needs a decision</small>"
        : addition
          ? "<br><small>NaPTAN tags will be added</small>"
          : suggestion?.tags && Object.keys(suggestion.tags).length
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
            naptanDifferencesAction(e, stop, suggestion),
            stopAreaAction(e, busStopData[i]),
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

    const direction = placement && travelDirectionOn(placement.segmentId)

    return {
        label: added ? "Stop <b>position</b> ✓" : "Stop <b>position</b>",
        onClick: () =>
            showStopPositionForm(e.latlng, {
                added: added,
                distance: placement?.distance,
                tags: stopPositionTagsFor(platform, direction),
                onAdd: () => {
                    setStopPosition(platform, placement, nameOf(platform), direction)
                    onStopPositionsChanged()
                },
                onRemove: () => {
                    removeStopPosition(platform)
                    onStopPositionsChanged()
                },
            }),
    }
}

// the name a stop is going to have, once any NaPTAN rename accepted here is uploaded
function nameOf(stop) {
    if (!stop) return ""

    const naptanName = naptanTagSuggestions.get(stopKey(stop))?.differing?.name

    return effectiveName(stop, naptanName, naptanName && getDecision(stop, "name", naptanName))
}

// Which way along the road the buses calling here travel, as the wiki wants it on a stop
// position: relative to the way's own direction, not the compass. Taken from how the
// route actually runs over that way, so the two sides of a road are told apart even when
// both their stop positions sit on the one way.
function travelDirectionOn(segmentId) {
    const uses = routeData?.ways?.filter((routeWay) => routeWay.way.id === segmentId)
    if (!uses?.length) return null

    const reversed = new Set(uses.map((routeWay) => Boolean(routeWay.reversed_latLngs)))

    // the route runs over this way both ways round, so the node serves both
    if (reversed.size > 1) return "both"

    return reversed.has(true) ? "backward" : "forward"
}

// mirrors make_stop_position_tags() in bus_stop_creation.py
function stopPositionTagsFor(platform, direction) {
    const tags = { public_transport: "stop_position" }
    if (relationTags?.route) tags[relationTags.route] = "yes"
    const name = nameOf(platform)
    if (name) tags.name = name
    if (direction) tags.direction = direction
    return tags
}

// A decision made after a stop position or stop area was queued has to reach it, so the
// names in one changeset agree with each other.
function refreshDerivedNames() {
    if (!busStopData) return

    let changed = false

    for (const entry of busStopData) {
        const platform = entry.platform
        if (!platform || isNewStop(platform)) continue

        if (hasStopPosition(platform)) {
            changed = renameStopPosition(platform, nameOf(platform)) || changed
        }

        const collections = collectionsInGroup(entry)
        const members = groupMembers(collections)
        if (members.length >= 2) {
            changed = renameStopArea(members, groupName(collections)) || changed
        }
    }

    return changed
}

// The route decides which way the buses at a stop position travel, so every calculation
// can change it. Nothing is recalculated from here, which would loop.
export function refreshStopPositionDirections() {
    if (!busStopData) return

    for (const entry of busStopData) {
        const platform = entry.platform
        if (!platform) continue

        const placement = getStopPositionPlacement(platform)
        if (placement) setStopPositionDirection(platform, travelDirectionOn(placement.segmentId))
    }
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

// `keepPopup` leaves an open popup alone, for one that redraws itself as it is used
function onTagAdditionsChanged({ keepPopup = false } = {}) {
    if (!keepPopup) clearBusStopsPopup()
    refreshDerivedNames()
    updateBusStopsVisibility()
    // a tag-only change still has something to upload when the route itself is unchanged
    requestCalcBusRoute()
}

function naptanTagsAction(e, stop, suggestion, addition) {
    const fillable = suggestion?.tags && Object.keys(suggestion.tags).length
    if (!fillable && !addition) return null

    return {
        label: addition ? "NaPTAN <b>tags</b> ✓" : "NaPTAN <b>tags</b>",
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

const directionFor = (placement) => (placement ? travelDirectionOn(placement.segmentId) : null)

// Where NaPTAN and the stop hold different values for a tag, the mapper decides which
// one is right. Until every one is decided the route cannot be uploaded.
function naptanDifferencesAction(e, stop, suggestion) {
    const differing = suggestion?.differing
    if (!differing || !Object.keys(differing).length) return null

    const outstanding = undecidedKeys(stop, differing).length

    return {
        label: outstanding ? `NaPTAN <b>differs</b> (${outstanding})` : "NaPTAN <b>differs</b>",
        undecided: outstanding > 0,
        onClick: () =>
            showNaptanDifferencesForm(e.latlng, {
                rows: Object.entries(differing).map(([tagKey, naptanValue]) => ({
                    tagKey: tagKey,
                    osmValue: stop.tags?.[tagKey] ?? "",
                    naptanValue: naptanValue,
                    decision: getDecision(stop, tagKey, naptanValue) ?? null,
                })),
                onDecide: (tagKey, naptanValue, decision) => {
                    setDecision(stop, tagKey, naptanValue, decision)
                    // the popup stays up so the rest of the stop's tags can be decided too
                    onTagAdditionsChanged({ keepPopup: true })
                },
            }),
    }
}

// The stops of one place: the group the server worked out, plus any stop the user has
// placed here since, which no download knows about yet.
function collectionsInGroup(collection) {
    const inGroup =
        collection.groupId >= 0 ? busStopData.filter((entry) => entry.groupId === collection.groupId) : [collection]

    const groupName = inGroup.map((entry) => (entry.platform ?? entry.stop)?.groupName).find(Boolean) ?? ""

    for (const entry of busStopData) {
        if (inGroup.includes(entry) || !isNewStop(entry.platform)) continue
        if (groupName && entry.platform.groupName === groupName) inGroup.push(entry)
    }

    return inGroup
}

// what the relation should be called: the stops' own name, without the stop letter that
// tells one side of the road from the other
function groupName(collections) {
    for (const entry of collections) {
        const name = nameOf(entry.platform ?? entry.stop)
        if (name) return name
    }
    return ""
}

// Offers a stop area for the stops of one place, or takes back one not yet uploaded.
function stopAreaAction(e, collection) {
    if (!busStopData || !collection) return null

    const collections = collectionsInGroup(collection)
    const members = groupMembers(collections)

    // a single element is not a group; there is nothing for a relation to bring together
    if (members.length < 2) return null

    const existing = existingAreaFor(members)
    const missing = existing ? members.filter((member) => !existing.members.includes(member.key)) : members
    const pending = getPendingStopArea(members)

    // already all in one, and nothing queued: nothing to offer
    if (!pending && !missing.length) return null

    const name = groupName(collections)
    if (!existing && !name) return null

    return {
        label: pending ? "Stop <b>area</b> ✓" : "Stop <b>area</b>",
        queued: Boolean(pending),
        onClick: () =>
            showStopAreaForm(e.latlng, {
                name: existing?.name || name,
                existing: existing,
                members: members,
                missing: missing,
                queued: Boolean(pending),
                onAdd: () => {
                    addStopArea(members, name, existing)
                    onStopAreasChanged()
                },
                onRemove: () => {
                    removeStopArea(members)
                    onStopAreasChanged()
                },
            }),
    }
}

function onStopAreasChanged() {
    clearBusStopsPopup()
    updateBusStopsVisibility()
    // a stop area is a change of its own, even when the route itself is untouched
    requestCalcBusRoute()
}

// A download reshapes the groups, so anything queued against a group that no longer
// looks the same is dropped rather than uploaded against stops the user never saw.
function reconcileGroups() {
    if (!busStopData) return

    const seen = new Set()

    for (const entry of busStopData) {
        const members = groupMembers(collectionsInGroup(entry))
        if (members.length >= 2) seen.add(stopAreaSignature(members))
    }

    reconcileStopAreas(seen)
}

// The stops the route calls at that are still waiting on a NaPTAN decision.
export function undecidedDisagreementStops() {
    const result = []

    for (const entry of busStopData ?? []) {
        const platform = entry.platform
        if (!platform || isNewStop(platform) || !platform.member) continue

        const suggestion = naptanTagSuggestions.get(stopKey(platform))
        if (hasUndecided(platform, suggestion?.differing)) result.push(platform)
    }

    return result
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

    const placement = planStopPosition(latLng, waysData)

    showNewStopForm(e.latlng, {
        nearby: findNearbyStop(latLng, null),
        routeType: relationTags?.route,
        stopPosition: {
            available: Boolean(placement),
            checked: true,
            direction: placement && travelDirectionOn(placement.segmentId),
        },
        onSave: (tags, wantStopPosition) => {
            const chosen = stopPositionFor(latLng, wantStopPosition)
            addNewStop(latLng, tags, chosen, directionFor(chosen))
            syncNewStops()
        },
    })
})

function editNewStop(e, stop) {
    const editPlacement = planStopPosition(stop.latLng, waysData)

    showNewStopForm(e.latlng, {
        stop: stop,
        nearby: findNearbyStop(stop.latLng, stop),
        routeType: relationTags?.route,
        stopPosition: {
            available: Boolean(editPlacement),
            // asked of the store: a new stop does not carry its stop position itself
            checked: hasStopPosition(stop),
            direction: editPlacement && travelDirectionOn(editPlacement.segmentId),
        },
        onSave: (tags, wantStopPosition) => {
            const chosen = stopPositionFor(stop.latLng, wantStopPosition)
            updateNewStop(stop, tags, chosen, directionFor(chosen))
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
            const naptanPlacement = planStopPosition(naptanStop.latLng, waysData)

            showNewStopForm(L.latLng(naptanStop.latLng), {
                tags: naptanStop.tags,
                nearby: findNearbyStop(naptanStop.latLng, null),
                routeType: relationTags?.route,
                stopPosition: {
                    available: Boolean(naptanPlacement),
                    checked: true,
                    direction: naptanPlacement && travelDirectionOn(naptanPlacement.segmentId),
                },
                onSave: (tags, wantStopPosition) => {
                    const latLng = [...naptanStop.latLng]
                    const chosen = stopPositionFor(latLng, wantStopPosition)
                    addNewStop(latLng, tags, chosen, directionFor(chosen))
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
        const moved = hasStopPosition(stop) ? stopPositionFor(latLng, true) : null
        moveNewStop(stop, latLng, moved, directionFor(moved))
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
