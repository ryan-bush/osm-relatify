import {
    clearBusStopsPopup,
    showAllTagsForm,
    showContextMenu,
    showEditStopForm,
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
    editedTags,
    getDecision,
    getStopEdit,
    getTagAddition,
    hasUndecided,
    removeStopEdit,
    removeTagAddition,
    setDecision,
    setStopEdit,
    undecidedKeys,
} from "./naptanTagAdditions.js"
import {
    addStopArea,
    clearStopAreas,
    existingAreaFor,
    existingAreasFor,
    getPendingStopArea,
    growStopArea,
    groupMembers,
    reconcileStopAreas,
    removeStopArea,
    renameStopArea,
    renameExistingStopArea,
    setExistingStopAreas,
    stopAreaSignature,
    stopAreaUploaded,
    stopAreasKnown,
    unrenameStopArea,
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
import { effectiveName, placeKey } from "./stopNames.js"
import { relationTags } from "./relationTagEditor.js"
import { escapeHtml, getBusCollectionName, haversine_distance } from "./utils.js"
import { waysData, waysRBush } from "./waysLayer.js"
import { requestCalcBusRoute, routeData } from "./waysRoute.js"

export let busStopData = null

// stops in NaPTAN that OSM is missing; empty unless the server has NaPTAN enabled
let naptanStops = []

// tags NaPTAN has for stops already in OSM, keyed like the stops' type and id
let naptanTagSuggestions = new Map()

// how far apart the stops of one place can be, as the server grouped them by
let stopAreaReach = 150

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
        setExistingStopAreas(fetchData.stopAreas ?? null)
        stopAreaReach = fetchData.stopAreaSearchArea ?? stopAreaReach
        naptanStops = fetchData.naptanStops ?? []
        naptanTagSuggestions = new Map((fetchData.naptanTags ?? []).map((suggestion) => [stopKey(suggestion), suggestion]))
        // after the suggestions, as the groups go by the names stops are going to have
        reconcileGroups()

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

function refreshNewStops() {
    busStopData = busStopData.filter((entry) => !isNewStop(entry.platform)).concat(newStopCollections())
    applyPendingStopPositions()
}

function syncNewStops() {
    refreshNewStops()
    onBusStopDataChanged()
}

// A stop position the user added to a stop already in OSM lives only in the browser, so
// each fresh download has to be given it again.
function applyPendingStopPositions() {
    for (const entry of busStopData) {
        if (!entry.platform || isNewStop(entry.platform)) continue

        if (keepsItsPendingStopPosition(entry)) {
            // the platform decides whether the route calls here; the two never disagree
            const node = getStopPositionNode(entry.platform)
            node.member = true
            entry.stop = node
            continue
        }

        removeStopPosition(entry.platform)

        // nothing is creating the node any more, so the collection stops carrying it;
        // otherwise it would still be drawn, and still sent as a member of the route
        if (isNewStop(entry.stop)) entry.stop = null
    }
}

// Whether the stop position put on the road for `entry` is still one to create.
function keepsItsPendingStopPosition(entry) {
    if (!getStopPositionNode(entry.platform)) return false

    // OSM has gained one since, so ours is not needed after all
    if (entry.stop && !isNewStop(entry.stop)) return false

    // the stop is no longer in the route, so neither is its stop position
    return Boolean(entry.platform.member)
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
            naptanDifferencesAction(e, stop, suggestion, busStopData[i]),
            stopAreaAction(e, busStopData[i]),
            editStopAction(e, busStopData[i]),
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
                    const before = groupMembers(collectionsInGroup(collection))
                    setStopPosition(platform, placement, nameOf(platform), direction)
                    onStopPositionsChanged(collection, before)
                },
                onRemove: () => {
                    const before = groupMembers(collectionsInGroup(collection))
                    removeStopPosition(platform)
                    onStopPositionsChanged(collection, before)
                },
            }),
    }
}

// Lets the mapper change the few fields of a stop that are theirs to change. Only for a
// stop already in OSM: one placed in this session is edited through its own form, which
// can still move and delete it.
function editStopAction(e, collection) {
    const platform = collection.platform
    if (!platform || isNewStop(platform)) return null

    const edit = getStopEdit(platform)

    return {
        label: edit ? "Edit ✓" : "Edit",
        edited: Boolean(edit),
        onClick: () =>
            showEditStopForm(e.latlng, {
                tags: editedTags(platform),
                edited: Boolean(edit),
                rename: {
                    follows: (name) => renameFollowers(collection, name).map((f) => f.label),
                },
                onSave: (tags, alsoRename) => {
                    setStopEdit(platform, tags)
                    // worked out afresh each time, so a rename queued by an earlier save
                    // does not outlive the name it was following
                    unfollowRename(collection)
                    if (alsoRename) for (const follower of renameFollowers(collection, tags.name)) follower.apply()
                    onStopEditsChanged()
                },
                onRevert: () => {
                    removeStopEdit(platform)
                    unfollowRename(collection)
                    onStopEditsChanged()
                },
            }),
    }
}

// What else carries this stop's name and would be left saying the old one: the stop
// position on the road, and the stop area grouping the place. A pending one renames
// itself from the stop, so only what is already in OSM is offered here.
function renameFollowers(collection, name) {
    const followers = []
    name = (name ?? "").trim()
    if (!name) return followers

    const stop = collection.stop
    if (stop && !isNewStop(stop) && (stop.tags?.name ?? "").trim() && (stop.tags.name ?? "").trim() !== name) {
        followers.push({
            kind: "stopPosition",
            label: "the stop position",
            apply: () => setStopEdit(stop, { name: name }),
        })
    }

    const members = groupMembers(collectionsInGroup(collection))
    const area = members.length >= 2 && stopAreasKnown() ? existingAreaFor(members) : null

    if (area && area.name && area.name !== name) {
        followers.push({
            kind: "stopArea",
            label: "the stop area",
            was: area.name,
            apply: () => renameExistingStopArea(members, area, name),
        })
    }

    return followers
}

// A NaPTAN name the mapper accepts renames what was carrying the stop's old name - the
// stop position on the road, and the stop area - as a rename typed into the stop form
// offers to. Worked out afresh from the decision, so taking it back undoes them.
function followNaptanRename(collection) {
    const platform = collection.platform
    // a name the mapper typed outranks NaPTAN's, and has its own say over the rest
    if (getStopEdit(platform)?.tags?.name !== undefined) return

    const oldName = (platform.tags?.name ?? "").trim()
    const name = nameOf(platform)
    const stop = collection.stop

    if (stop && !isNewStop(stop) && (stop.tags?.name ?? "").trim() === oldName) {
        if (name !== oldName) setStopEdit(stop, { name: name })
        else removeStopEdit(stop)
    }

    unfollowRename(collection)
    if (name === oldName) return

    for (const follower of renameFollowers(collection, name)) {
        if (follower.kind === "stopArea" && follower.was === oldName) follower.apply()
    }
}

// Drops a stop area rename that was following this stop, leaving a relation it is still
// missing members from queued for those alone.
function unfollowRename(collection) {
    if (!stopAreasKnown()) return

    const members = groupMembers(collectionsInGroup(collection))
    if (members.length < 2) return

    unrenameStopArea(members, existingAreaFor(members))
}

function onStopEditsChanged() {
    clearBusStopsPopup()
    refreshDerivedNames()
    updateBusStopsVisibility()
    // a tag-only change still has something to upload when the route itself is unchanged
    requestCalcBusRoute()
}

// the name a stop is going to have, once any NaPTAN rename accepted here is uploaded
function nameOf(stop) {
    if (!stop) return ""

    const naptanName = naptanTagSuggestions.get(stopKey(stop))?.differing?.name
    // a name NaPTAN fills in only ever goes on a stop that has none, so it stands in for
    // one the mapper typed
    const edited = getStopEdit(stop)?.tags?.name ?? getTagAddition(stop)?.tags?.name

    return effectiveName(stop, naptanName, naptanName && getDecision(stop, "name", naptanName), edited)
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
    const index = placeIndex()

    for (const entry of busStopData) {
        const platform = entry.platform
        if (!platform || isNewStop(platform)) continue

        if (hasStopPosition(platform)) {
            changed = renameStopPosition(platform, nameOf(platform)) || changed
        }

        const collections = collectionsInGroup(entry, index)
        const members = groupMembers(collections)
        if (members.length >= 2) {
            // a rename can bring two groups together after an area was queued for one
            if (stopAreasKnown() && existingAreasFor(members).length < 2) {
                changed = growStopArea(members, existingAreaFor(members)) || changed
            }
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

function onStopPositionsChanged(collection, previousMembers) {
    clearBusStopsPopup()
    // before the redraw, so the stop area is settled by the time the menu offers it again
    refreshNewStops()
    followStopAreaMembers(collection, previousMembers)
    onBusStopDataChanged()
}

// A stop position added to a stop that is already grouped belongs in that group's stop
// area as well, so it is queued here rather than left for the mapper to ask for a second
// time. A queued area is keyed by exactly the stops it covers, so one that was waiting
// has to be moved over to the group the new node has joined, or it would be dropped.
function followStopAreaMembers(collection, previousMembers) {
    const collections = collectionsInGroup(collection)
    const members = groupMembers(collections)
    const queued = getPendingStopArea(previousMembers)

    if (queued) removeStopArea(previousMembers)

    // a single element is not a group, as in stopAreaAction
    if (members.length < 2) return

    const existing = existingAreaFor(members)
    const missing = existing ? members.filter((member) => !existing.members.includes(member.key)) : members

    // the relation would have nothing to add, and an upload has no use for that
    if (!missing.length) return

    // one the mapper asked for follows the group it was queued against
    if (queued && !queued.automatic) {
        addStopArea(members, queued.name, existing)
        return
    }

    // Only a stop this change brought into the group is queued on its own account: a
    // group with no area at all is a relation to create, which is the mapper's call, and
    // stops left out of an existing one before now are not this change's doing. Taking
    // the stop position back again therefore leaves nothing behind, as what was queued
    // automatically is worked out afresh rather than carried over.
    if (!existing) return
    if (!missing.some((member) => !previousMembers.some((was) => was.key === member.key))) return

    addStopArea(members, groupName(collections), existing, { automatic: true })
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
function naptanDifferencesAction(e, stop, suggestion, collection) {
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
                    if (tagKey === "name" && collection.platform === stop) followNaptanRename(collection)
                    // the popup stays up so the rest of the stop's tags can be decided too
                    onTagAdditionsChanged({ keepPopup: true })
                },
            }),
    }
}

// The stops of one place: the group the server worked out, plus any stop the user has
// placed here since, which no download knows about yet.
function collectionsInGroup(collection, index = placeIndex()) {
    const group = []

    const add = (entry) => {
        const together = entry.groupId >= 0 ? index.byGroup.get(entry.groupId) : [entry]
        for (const member of together) if (!group.includes(member)) group.push(member)
    }

    add(collection)

    // The server grouped the stops by the names they had when downloaded. One renamed in
    // this session, or placed in it, belongs with the stops nearby that share the name it
    // is going to have. A stop renamed away keeps its old group, whose stop area follows
    // the rename. Stops added along the way are visited too, as the array iterator
    // reaches whatever is appended before it gets there.
    for (const entry of group) {
        const latLng = placeLatLng(entry)

        for (const other of index.byKey.get(index.keyOf.get(entry)) ?? []) {
            if (group.includes(other)) continue
            if (haversine_distance(latLng, placeLatLng(other)) <= stopAreaReach) add(other)
        }
    }

    // in download order, so every stop of the group agrees on which name it takes
    return group.sort((a, b) => index.order.get(a) - index.order.get(b))
}

const placeLatLng = (entry) => (entry.platform ?? entry.stop).latLng

// What collectionsInGroup() looks stops up by, worked out once for a pass over them all.
function placeIndex() {
    const byGroup = new Map()
    const byKey = new Map()
    const keyOf = new Map()
    const order = new Map()

    busStopData.forEach((entry, i) => {
        order.set(entry, i)

        if (entry.groupId >= 0) {
            if (!byGroup.has(entry.groupId)) byGroup.set(entry.groupId, [])
            byGroup.get(entry.groupId).push(entry)
        }

        // as the server does: the platform's name, or the stop position's without one
        const key = placeKey(nameOf(entry.platform) || nameOf(entry.stop))
        if (!key) return

        keyOf.set(entry, key)
        if (!byKey.has(key)) byKey.set(key, [])
        byKey.get(key).push(entry)
    })

    return { byGroup, byKey, keyOf, order }
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

    // without knowing which stop areas are already out there, the only thing on offer
    // would be creating one that may well exist already
    if (!stopAreasKnown()) return null

    const collections = collectionsInGroup(collection)
    const members = groupMembers(collections)

    // a single element is not a group; there is nothing for a relation to bring together
    if (members.length < 2) return null

    const found = existingAreasFor(members)
    const pending = getPendingStopArea(members)

    // The place is grouped twice over already. Creating a third is the one thing that
    // would certainly be wrong, and picking one of them to complete is not ours to
    // guess at, so this says what is there and leaves it to the mapper.
    if (found.length > 1) {
        return {
            label: "Stop <b>area</b> ⚠",
            onClick: () => showStopAreaForm(e.latlng, { members: members, several: found }),
        }
    }

    // Nothing out there holds these stops, but this session put them in a stop area a
    // moment ago and the download has not caught up. Offering a relation of their own
    // again would be offering a duplicate, which is what the upload refuses.
    if (!found.length && !pending && stopAreaUploaded(members)) {
        return {
            label: "Stop <b>area</b> ⏳",
            onClick: () => showStopAreaForm(e.latlng, { members: members, uploaded: true }),
        }
    }

    const existing = found[0] ?? null
    const missing = existing ? members.filter((member) => !existing.members.includes(member.key)) : members

    // already all in one, and nothing queued: nothing to offer
    if (!pending && !missing.length) return null

    const name = groupName(collections)
    if (!existing && !name) return null

    return {
        label: pending ? "Stop <b>area</b> ✓" : "Stop <b>area</b>",
        queued: Boolean(pending),
        onClick: () => {
            const shown = shownNames(members, collections)
            const missingKeys = new Set(missing.map((member) => member.key))

            showStopAreaForm(e.latlng, {
                name: existing?.name || name,
                existing: existing,
                members: shown,
                missing: shown.filter((member) => missingKeys.has(member.key)),
                queued: Boolean(pending),
                onAdd: () => {
                    addStopArea(members, name, existing)
                    onStopAreasChanged()
                },
                onRemove: () => {
                    removeStopArea(members)
                    onStopAreasChanged()
                },
            })
        },
    }
}

// The members as the form lists them: a stop being renamed by this changeset is shown by
// the name it is going to have, which is the one the area takes.
function shownNames(members, collections) {
    const renamed = new Map()

    for (const entry of collections) {
        for (const stop of [entry.platform, entry.stop]) {
            if (!stop) continue
            const name = nameOf(stop)
            if (name && name !== (stop.tags?.name ?? "").trim()) renamed.set(`${stop.type}/${stop.id.split("_")[0]}`, name)
        }
    }

    return members.map((member) => (renamed.has(member.key) ? { ...member, name: renamed.get(member.key) } : member))
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
    const index = placeIndex()

    for (const entry of busStopData) {
        const members = groupMembers(collectionsInGroup(entry, index))
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
        const unmarked = naptanStop.tags["naptan:BusStopType"] === "CUS" ? " (unmarked stop)" : ""
        marker.bindTooltip(`${escapeHtml(naptanStop.name)}${indicator}<br><small>In NaPTAN, missing from OSM${unmarked}</small>`, {
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
