import { busStopData, processBusStopData, undecidedDisagreementStops } from "./busStopsLayer.js"
import { isNewStop, newStopCount, newStopsPayload } from "./busStopsNew.js"
import {
    completedStopAreaCount,
    newStopAreaCount,
    noteUploadedStopAreas,
    stopAreaCount,
    stopAreasKnown,
    stopAreasPayload,
} from "./stopAreas.js"
import { stopPositionCount, stopPositionsPayload } from "./stopPositions.js"
import { tagAdditionsPayload, tagChangeCount } from "./naptanTagAdditions.js"
import {
    downloadHistoryData,
    processRelationDownloadTriggers,
} from "./downloadTriggers.js"
import {
    markRouteUploaded,
    renderMasterPicker,
    routeMasterTagsEdited,
    routeMasterTagsPayload,
    routeMasterViewId,
    setRouteEditHandler,
    setRouteMasterView,
} from "./masterPicker.js"
import { hideDownloadBar, map, showDownloadBar } from "./map.js"
import {
    detachingRouteMasters,
    pendingRouteMaster,
    routeMasterChangeCount,
    routeMasterPayload,
} from "./routeMasters.js"
import {
    noteRouteTags,
    processRouteMasters,
    setRouteMasterChangeHandler,
    setRouteNavigationHandlers,
} from "./routeMastersView.js"
import { showMessage } from "./messageBox.js"
import {
    processRelationTags,
    relationTags,
    relationTagsOriginal,
    setRecalcHandler,
    setTagsChangedHandler,
    unloadRelationTags,
} from "./relationTagEditor.js"
import {
    createElementFromHTML,
    deflateCompress,
    getBusCollectionName,
    osmIsLive,
    osmUrl,
} from "./utils.js"
import { processRelationEndpointData } from "./waysEndpoint.js"
import {
    processRelationWaysData,
    removeMembersList,
    waysData,
} from "./waysLayer.js"
import { clearRouteData, requestCalcBusRoute, routeData } from "./waysRoute.js"

const busAnimationElement = document.getElementById("bus-animation")
const loadRelationForm = document.getElementById("load-relation-form")
const loadRelationBtn = loadRelationForm.querySelector("button[type=submit]")
const relationIdInput = loadRelationForm.querySelector(
    "input[name=relation-id]",
)
const createRelationForm = document.getElementById("create-relation-form")
const createRelationBtn = createRelationForm.querySelector(
    "button[type=submit]",
)
const createRouteType = document.getElementById("create-route-type")
const relationIdElements = document.querySelectorAll(".view .relation-id")
const relationUrlElements = document.querySelectorAll(".view .relation-url")
const editingLabel = document.querySelector("#view-edit .editing-label")
const editBackBtn = document.querySelector("#view-edit .btn-back")
const editReloadBtn = document.querySelector("#view-edit .btn-reload")
const editWarnings = document.getElementById("edit-warnings")
const editSubmitBtn = document.querySelector("#view-edit .btn-next")
const sumitBackBtn = document.querySelector("#view-submit .btn-back")
const routeSummary = document.getElementById("route-summary")
const submitUploadBtn = document.querySelector("#view-submit .btn-upload")
const submitDownloadBtn = document.querySelector("#view-submit .btn-download")
const submitComment = document.getElementById("submit-comment")

export let relationId = null

// a relation being created has no id until OSM assigns one on upload, so relationId
// stays null all the way through editing; this distinguishes that from "nothing loaded"
export let isCreating = false

// with no relation to read tags from, the server needs telling what kind of route this
// is on every /query, not just the first
export let newRouteType = null

// relationTagEditor.js cannot import the route module directly without closing an import cycle,
// so the dependency is registered from here instead. The call is wrapped rather than
// passed by reference so the binding is only read once the modules have finished loading.
setRecalcHandler(() => requestCalcBusRoute())

// A route master queued or undone is a change to the changeset without being a change to
// the route, so the warnings are rebuilt from the calculation already in hand rather than
// asking for another one.
// a route's siblings are found by its ref, so an edit to it makes whatever was found for
// the old one no longer an answer; looking again is the mapper's to ask for
setTagsChangedHandler((tags) => noteRouteTags(tags))

// a variant listed beside the route being edited is as often the next thing to edit as
// it is something to go and look at
setRouteNavigationHandlers({
    editRoute: (id) => editRoute(id),
    showMaster: (id) => showMaster(id),
})

setRouteMasterChangeHandler(() => {
    if (routeData !== null) processRouteWarnings(routeData)
})

let activeView = "load"

const showRelationIdentity = () => {
    const known = relationId !== null

    editingLabel.innerText = known ? "Editing relation" : "Creating relation"

    for (const element of relationIdElements) {
        element.innerText = known ? `${relationId}` : ""
        element.parentElement.classList.toggle("d-none", !known)
    }

    for (const element of relationUrlElements) {
        element.href = known
            ? `${osmUrl}/relation/${relationId}`
            : "#"
        element.classList.toggle("d-none", !known)
    }
}

const switchView = (name) => {
    const className = `view-${name}`

    for (const view of document.querySelectorAll(".view")) {
        if (view.id === className) view.classList.remove("d-none")
        else view.classList.add("d-none")
    }

    activeView = name
}

relationIdInput.focus()

relationIdInput.addEventListener("input", (e) => {
    const match = relationIdInput.value.match(/\d+/)
    e.target.value = match !== null ? match[0] : ""
})

// Loads a relation by id. A route is opened for editing; a route master is not the thing
// that gets edited, so its variants are listed for one to be picked instead.
const loadRelation = (id, setBusy) => {
    relationId = id
    isCreating = false
    newRouteType = null
    showRelationIdentity()
    setBusy(true)
    // a download takes seconds, and the button that started it is not always in view
    showDownloadBar(`Loading relation #${id}...`)

    return fetch("/query", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            relationId: relationId,
        }),
    })
        .then(async (resp) => {
            if (!resp.ok) {
                showMessage(
                    "danger",
                    `❌ Relation load failed - ${resp.status}`,
                    await resp.text(),
                )
                return
            }

            return resp.json()
        })
        .then((data) => {
            if (!data) return

            if (data.kind === "route_master") {
                // nothing is loaded for a master itself, so the id it was given is not
                // the relation being edited
                relationId = null
                showRelationIdentity()
                setRouteMasterView(data)
                showMasterPicker()
                return
            }

            processFetchRelationData(data)
        })
        .catch((error) => {
            console.error(error)
            showMessage("danger", "❌ Relation load failed", error)
        })
        .finally(() => {
            hideDownloadBar()
            setBusy(false)
        })
}

const setLoadButtonBusy = (busy) => {
    relationIdInput.disabled = busy
    loadRelationBtn.classList.toggle("btn-secondary", busy)
    loadRelationBtn.classList.toggle("is-loading", busy)
    loadRelationBtn.innerHTML = busy ? busAnimationElement.innerHTML : "Load"
}

loadRelationForm.addEventListener("submit", (e) => {
    e.preventDefault()

    if (loadRelationBtn.classList.contains("is-loading")) return

    loadRelation(Number.parseInt(relationIdInput.value), setLoadButtonBusy)
})

const showMasterPicker = () => {
    renderMasterPicker()
    switchView("master")
}

// Everything queued for the route being edited, which leaving it would throw away. The
// picker makes hopping between variants easy, and losing an afternoon's work to a stray
// click with it.
const hasPendingChanges = () =>
    newStopCount() > 0 ||
    stopPositionCount() > 0 ||
    tagChangeCount() > 0 ||
    stopAreaCount() > 0 ||
    routeMasterChangeCount() > 0 ||
    relationTagsEdited() ||
    routeMembersEdited()

const relationTagsEdited = () => {
    if (relationTags === null || relationTagsOriginal === null) return false

    const keys = new Set([...Object.keys(relationTags), ...Object.keys(relationTagsOriginal)])
    return [...keys].some((key) => (relationTags[key] ?? "") !== (relationTagsOriginal[key] ?? ""))
}

// The calculation says so itself: a route whose members match the relation warns that
// nothing about it changed, and one that has been edited does not.
const routeMembersEdited = () => {
    if (routeData === null) return false
    if (isCreating) return true

    return !routeData.warnings.some((warning) => warning.severity === 10)
}

const confirmLeavingRoute = () =>
    !hasPendingChanges() ||
    window.confirm("This route has changes that have not been uploaded. Leave and lose them?")


createRelationForm.addEventListener("submit", (e) => {
    e.preventDefault()

    if (createRelationBtn.classList.contains("is-loading")) return

    // there is no relation to seed a download area from, so the visible map is it
    const bounds = map.getBounds()

    relationId = null
    isCreating = true
    newRouteType = createRouteType.value
    showRelationIdentity()

    createRouteType.disabled = true
    createRelationBtn.classList.add("is-loading")
    const defaultInnerText = createRelationBtn.innerText
    createRelationBtn.innerText = "Creating..."
    showDownloadBar("Downloading map data...")

    fetch("/query", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            relationId: null,
            routeType: newRouteType,
            bounds: [
                bounds.getSouth(),
                bounds.getWest(),
                bounds.getNorth(),
                bounds.getEast(),
            ],
        }),
    })
        .then(async (resp) => {
            if (!resp.ok) {
                isCreating = false
                showMessage(
                    "danger",
                    `❌ Could not start a new relation - ${resp.status}`,
                    await resp.text(),
                )
                return
            }

            return resp.json()
        })
        .then((data) => {
            if (!data) return

            processFetchRelationData(data)
            showMessage(
                "info",
                "🆕 New route started",
                "Click the ways the route follows, then right-click one to set <b>START</b> and another to set <b>END</b>. " +
                    "Fill in <b>name</b>, <b>ref</b>, <b>from</b> and <b>to</b> in the tag table before uploading." +
                    (newRouteType === "bus"
                        ? "<br><br>A stop missing from the map? Right-click where it is to add it."
                        : ""),
            )
        })
        .catch((error) => {
            isCreating = false
            console.error(error)
            showMessage("danger", "❌ Could not start a new relation", error)
        })
        .finally(() => {
            hideDownloadBar()
            createRouteType.disabled = false
            createRelationBtn.classList.remove("is-loading")
            createRelationBtn.innerText = defaultInnerText
        })
})

export const processFetchRelationData = (data) => {
    processRelationTags(data)
    switchView("edit")

    // order is important here
    processRelationEndpointData(data)
    processRelationWaysData(data)

    // order is not important here
    processRelationDownloadTriggers(data)
    processBusStopData(data)
    processRouteMasters(data, { relationId, isCreating })
}

export const processRouteWarnings = (data) => {
    if (activeView === "submit") switchView("edit")

    editSubmitBtn.classList.add("d-none")

    editWarnings.innerHTML = ""
    let highestSeverityLevel = 0

    for (const warning of data.warnings) {
        // the relation is untouched, but the changeset still has stop tags to add, or a
        // route master to put it in
        if (warning.severity === 10 && (tagChangeCount() > 0 || stopAreaCount() > 0 || routeMasterChangeCount() > 0))
            continue

        const severityLevel = warning.severity
        const severityText = {
            0: "LOW",
            1: "HIGH",
            10: "UNCHANGED",
        }[severityLevel]

        highestSeverityLevel = Math.max(highestSeverityLevel, severityLevel)

        if (warning.message === "Some ways are not used") {
            const child = createElementFromHTML(`
            <div class="warning warning-${severityText}">
                <div class="warning-message">${warning.message}</div>
                <div class="btn-group-vertical ms-2">
                    <button class="btn primary btn-primary">Show me</button>
                    <button class="btn secondary btn-outline-danger">Deselect all</button>
                </div>
            </div>`)

            child.querySelector("button.primary").onclick = () => {
                // show me
                const wayId = warning.extra[0]
                const way = waysData[wayId]
                map.setView(way.midpoint, 19)
            }

            child.querySelector("button.secondary").onclick = () => {
                // deselect all
                removeMembersList(warning.extra)
            }

            editWarnings.appendChild(child)
        } else if (
            warning.message === "Some stops are far away" ||
            warning.message === "Some stops are not reached" ||
            warning.message === "Some stops are inactive in NaPTAN" ||
            warning.message === "Some stops serve the other direction"
        ) {
            const child = createElementFromHTML(`
            <div class="warning warning-${severityText}">
                <div class="warning-message">${warning.message}</div>
                <div class="btn-group-vertical ms-2">
                    <button class="btn primary btn-primary">Show me</button>
                </div>
            </div>`)

            child.querySelector("button.primary").onclick = () => {
                // show me
                const stopId = warning.extra[0]
                const stop = busStopData.find(
                    (c) =>
                        (c.platform && c.platform.id === stopId) ||
                        (c.stop && c.stop.id === stopId),
                )

                if (stop)
                    if (stop.platform) map.setView(stop.platform.latLng, 19)
                    else map.setView(stop.stop.latLng, 19)
            }

            editWarnings.appendChild(child)
        } else {
            editWarnings.appendChild(
                createElementFromHTML(`
            <div class="warning warning-${severityText}">
                <div class="warning-message">${warning.message}</div>
            </div>`),
            )
        }
    }

    // A stop NaPTAN disagrees with is a decision for the mapper, not something to guess
    // at, so it holds the upload until every one has been answered one way or the other.
    const undecided = undecidedDisagreementStops()

    if (undecided.length) {
        highestSeverityLevel = Math.max(highestSeverityLevel, 1)

        const undecidedMessage =
            undecided.length === 1
                ? "A stop disagrees with NaPTAN"
                : `${undecided.length} stops disagree with NaPTAN`

        const child = createElementFromHTML(`
        <div class="warning warning-HIGH">
            <div class="warning-message">${undecidedMessage}</div>
            <div class="btn-group-vertical ms-2">
                <button class="btn primary btn-primary">Show me</button>
            </div>
        </div>`)

        child.querySelector("button.primary").onclick = () => map.setView(undecided[0].latLng, 19)

        editWarnings.appendChild(child)
    }

    // Stop areas are looked up by a second Overpass query, and one that fails leaves the
    // application unable to tell a place that has no stop area from one it simply could
    // not ask about. It stops offering them rather than offer to create a second
    // relation beside the one the stops are already in, and says so here.
    const areasUnknown = busStopData !== null && !stopAreasKnown()

    if (areasUnknown) {
        editWarnings.appendChild(
            createElementFromHTML(`
        <div class="warning warning-LOW">
            <div class="warning-message">
                Overpass could not say which stop areas these stops are already in, so none are
                offered. Reload the relation to try again.
            </div>
        </div>`),
        )
    }

    editSubmitBtn.classList.toggle(
        "mt-2",
        data.warnings.length > 0 || undecided.length > 0 || areasUnknown,
    )

    if (highestSeverityLevel === 0) editSubmitBtn.classList.remove("d-none")
}

// Everything belonging to the route being edited. The master it was picked from is not
// part of that: going back to the list is not leaving it.
const unloadRoute = () => {
    processRelationEndpointData(null)
    processRelationWaysData(null)
    processRelationDownloadTriggers(null)
    processBusStopData(null)
    processRouteMasters(null)
    unloadRelationTags()
    submitComment.value = ""

    relationId = null
    isCreating = false
    newRouteType = null
    clearRouteData()
}

const unload = () => {
    unloadRoute()
    setRouteMasterView(null)
    switchView("load")
}

editBackBtn.onclick = () => {
    if (!confirmLeavingRoute()) return

    // back where the route was picked, when it was picked rather than typed in
    if (routeMasterViewId() !== null) {
        unloadRoute()
        showMasterPicker()
        return
    }

    unload()
}

const masterBackBtn = document.querySelector("#view-master .btn-master-back")
const masterReloadBtn = document.querySelector("#view-master .btn-master-reload")

masterBackBtn.onclick = () => {
    if (!confirmLeavingMaster()) return

    setRouteMasterView(null)
    unload()
}

const masterComment = document.getElementById("master-comment")
const masterUploadBtn = document.getElementById("master-upload")
const masterDownloadBtn = document.getElementById("master-download")

const confirmLeavingMaster = () =>
    !routeMasterTagsEdited() ||
    window.confirm("This route master has tag changes that have not been uploaded. Leave and lose them?")

// The master's own tags go up as a changeset of their own: edited from the list of a
// line's variants, there is no route being edited to carry them along.
const sendRouteMasterTags = (path, onDone) => {
    const payload = routeMasterTagsPayload()
    if (payload === null) return

    masterUploadBtn.disabled = true
    masterDownloadBtn.disabled = true

    fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, comment: masterComment.value }),
    })
        .then(async (resp) => {
            if (!resp.ok) {
                showMessage("danger", `❌ Upload failed - ${resp.status}`, await resp.text())
                return
            }

            return onDone(resp)
        })
        .catch((error) => {
            console.error(error)
            showMessage("danger", "❌ Upload failed", error)
        })
        .finally(() => {
            masterUploadBtn.disabled = false
            masterDownloadBtn.disabled = false
        })
}

masterUploadBtn.onclick = () =>
    sendRouteMasterTags("/upload_route_master", async (resp) => {
        const data = await resp.json()

        if (!data.ok) {
            showMessage("danger", `❌ Upload failed - ${data.error_code}`, data.error_message)
            return
        }

        showMessage(
            "success",
            "✅ Upload successful",
            `The changeset <a href="${osmUrl}/changeset/${data.changeset_id}" target="_blank">${data.changeset_id}</a> has been uploaded.`,
        )

        // loaded again so the list shows what was just uploaded, not what it replaced
        masterComment.value = ""
        masterReloadBtn.click()
    })

masterDownloadBtn.onclick = () =>
    sendRouteMasterTags("/download_route_master_change", async (resp) => {
        const a = document.createElement("a")
        a.href = URL.createObjectURL(await resp.blob())
        a.download = `relatify_master_${routeMasterViewId()}_${new Date().toISOString().replace(/:/g, "_")}.osc`
        a.click()
    })

masterReloadBtn.onclick = () => {
    const id = routeMasterViewId()
    if (id === null) return

    loadRelation(id, (busy) => {
        masterBackBtn.disabled = busy
        masterReloadBtn.disabled = busy
        masterReloadBtn.innerText = busy ? "Reloading..." : "↻ Reload"
    })
}

// picking a variant loads it the way any route is loaded
setRouteEditHandler((id) => editRoute(id))

// Loading one relation in place of another, from wherever it was named: a variant picked
// out of the master's list, one listed beside the route being edited, or the master of
// the route being edited.
function editRoute(id) {
    if (!confirmLeavingRoute() || !confirmLeavingMaster()) return

    loadRelation(id, (busy) => {
        for (const button of document.querySelectorAll("#master-routes button, .route-master-routes button"))
            button.disabled = busy
    })
}

function showMaster(id) {
    if (!confirmLeavingRoute()) return

    loadRelation(id, (busy) => {
        for (const button of document.querySelectorAll(".route-master-actions button")) button.disabled = busy
    })
}

editReloadBtn.onclick = async () => {
    editBackBtn.disabled = true
    editReloadBtn.disabled = true

    const defaultInnerText = editReloadBtn.innerText
    editReloadBtn.innerText = "Reloading..."
    showDownloadBar("Reloading map data...")

    fetch("/query", {
        method: "POST",
        headers: {
            "Content-Encoding": "deflate",
            "Content-Type": "application/json",
        },
        body: await deflateCompress({
            relationId: relationId,
            routeType: newRouteType,
            downloadHistory: downloadHistoryData,
            downloadTargets: [],
            reload: true,
        }),
    })
        .then(async (resp) => {
            if (!resp.ok) {
                showMessage(
                    "danger",
                    `❌ Relation reload failed - ${resp.status}`,
                    await resp.text(),
                )
                return
            }

            return resp.json()
        })
        .then((data) => {
            processFetchRelationData(data)
        })
        .catch((error) => {
            console.error(error)
            showMessage("danger", "❌ Relation reload failed", error)
        })
        .finally(() => {
            hideDownloadBar()
            editReloadBtn.innerText = defaultInnerText

            editBackBtn.disabled = false
            editReloadBtn.disabled = false
        })
}

// mirrors make_comment() in main.py purely to show what will be used when the field is
// left blank; the server generates the comment it actually uploads
const makeDefaultComment = () => {
    const plural = (count) => (count !== 1 ? "s" : "")
    const stopCount = newStopCount()
    const positionCount = stopPositionCount()
    const taggedCount = tagChangeCount()
    const added = stopCount ? `; added ${stopCount} bus stop${plural(stopCount)}` : ""
    const positions = positionCount
        ? `; added ${positionCount} stop position${plural(positionCount)}`
        : ""
    const newAreas = newStopAreaCount()
    const doneAreas = completedStopAreaCount()
    const areas =
        (newAreas ? `; added ${newAreas} stop area${plural(newAreas)}` : "") +
        (doneAreas ? `; completed ${doneAreas} stop area${plural(doneAreas)}` : "")
    const tagged = taggedCount
        ? `; added NaPTAN tags to ${taggedCount} bus stop${plural(taggedCount)}`
        : ""
    const pendingMaster = pendingRouteMaster()
    const master = !pendingMaster
        ? ""
        : pendingMaster.id === null
          ? "; created route master"
          : `; added to route master #${pendingMaster.id}`
    const detachedCount = detachingRouteMasters().length
    const detached = detachedCount
        ? `; removed from ${detachedCount} route master${plural(detachedCount)}`
        : ""
    return makeRouteComment() + added + positions + areas + tagged + master + detached
}

const makeRouteComment = () => {
    const name = (relationTags.name ?? "").trim()
    let ref = (relationTags.ref ?? "").trim()

    // only include ref if it's not already in the name
    if (ref && name.includes(ref)) ref = ""

    const verb = relationId !== null ? "Updated" : "Created"
    const described = name && ref ? `${ref} ${name}` : name || ref

    if (relationId === null)
        return described ? `${verb} route: ${described}` : `${verb} route`
    if (described) return `${verb} route: ${described}, #${relationId}`
    return `${verb} route #${relationId}`
}

editSubmitBtn.onclick = () => {
    // tags may have changed since the last visit to this view
    submitComment.placeholder = makeDefaultComment()
    switchView("submit")
}

const styleStopName = (name) => {
    return name.replace(/^(?:\d+)|(?:\d+)$/, (match) => {
        return match
            .split("")
            .map((d) => `<span class="digit digit-${d}">${d}</span>`)
            .join("")
    })
}

export const processRouteStops = (data) => {
    routeSummary.innerHTML = ""

    for (const collection of data.busStops) {
        const isPlatform = collection.platform != null
        const isStop = collection.stop != null
        // not in OSM until upload, so there is nothing to link to yet
        const isNew = isNewStop(collection.platform)

        routeSummary.appendChild(
            createElementFromHTML(`
        <div class="route-summary-item">
            <img class="stop-icon" src="/static/img/bus_stop.webp" alt="Bus stop icon" height="28">
            <div class="stop-name">${styleStopName(getBusCollectionName(collection))}${
                isNew ? ' <span class="badge text-bg-warning stop-new-badge">new</span>' : ""
            }</div>
            <div class="stop-info">
                ${
                    isNew
                        ? `<span class="stop-info-platform" title="This stop is created when you upload">P</span>`
                        : isPlatform
                        ? `<a class="stop-info-platform link-underline link-underline-opacity-0 link-underline-opacity-100-hover"
                    title="This stop has a platform"
                    href="https://www.openstreetmap.org/${collection.platform.type}/${collection.platform.id}"
                    target="_blank">P</a>`
                        : ""
                }<!--
                -->${
                    isStop
                        ? `<a class="stop-info-stop link-underline link-underline-opacity-0 link-underline-opacity-100-hover"
                    title="This stop has a stopping position"
                    href="https://www.openstreetmap.org/${collection.stop.type}/${collection.stop.id}"
                    target="_blank">S</a>`
                        : ""
                }
            </div>
        </div>`),
        )
    }

    const allItems = Array.from(
        routeSummary.querySelectorAll(".route-summary-item"),
    )
    const allIcons = allItems.map((item) => item.querySelector(".stop-icon"))

    for (const [outerIndex, outerItem] of allItems.entries()) {
        outerItem.onclick = (e) => {
            e.stopPropagation()

            if (e.target.tagName === "A") return

            for (const [index, item] of allItems.entries()) {
                if (index <= outerIndex) {
                    item.classList.add("route-summary-item-checked")
                    allIcons[index].src = "/static/img/bus_stop_check.webp"
                } else {
                    item.classList.remove("route-summary-item-checked")
                    allIcons[index].src = "/static/img/bus_stop.webp"
                }
            }
        }
    }
}

sumitBackBtn.onclick = () => {
    switchView("edit")
}

submitUploadBtn.onclick = async () => {
    submitUploadBtn.disabled = true

    fetch("/upload_osm", {
        method: "POST",
        headers: {
            "Content-Encoding": "deflate",
            "Content-Type": "application/json",
        },
        body: await deflateCompress({
            relationId: relationId,
            route: routeData,
            tags: relationTags,
            tagsOriginal: relationTagsOriginal,
            comment: submitComment.value,
            newStops: newStopsPayload(),
            newStopPositions: stopPositionsPayload(),
            stopAreas: stopAreasPayload(),
            naptanTagAdditions: tagAdditionsPayload(),
            ...routeMasterPayload(),
        }),
    })
        .then(async (resp) => {
            if (!resp.ok) {
                showMessage(
                    "danger",
                    `❌ Upload failed - ${resp.status}`,
                    await resp.text(),
                )
                return
            }

            return resp.json()
        })
        .then((data) => {
            if (!data) return

            if (!data.ok) {
                showMessage(
                    "danger",
                    `❌ Upload failed - ${data.error_code}`,
                    data.error_message,
                )
                return
            }

            // OSM assigns the real id on upload; without this the new relation would
            // be created and then be unreachable from here
            const created = data.relation_id
                ? `<br><br>Created relation <a href="${osmUrl}/relation/${data.relation_id}" target="_blank">#${data.relation_id}</a>.`
                : ""

            // the revert tool only knows about live OSM
            const revert = osmIsLive
                ? `<br><br><i>Something broke? Use <a href="https://revert.monicz.dev/?changesets=${data.changeset_id}" target="_blank">this tool</a> to revert it.</i>`
                : ""

            showMessage(
                "success",
                "✅ Upload successful",
                `The changeset <a href="${osmUrl}/changeset/${data.changeset_id}" target="_blank">${data.changeset_id}</a> has been uploaded.${created}${revert}`,
            )

            // Stop areas are read back from Overpass, which is minutes behind: the next
            // variant of this line calls at the same places, and would be offered a
            // second relation for stops this upload has just grouped.
            noteUploadedStopAreas(stopAreasPayload())

            // back to the variants, with this one marked, so the next is one click away
            if (routeMasterViewId() !== null) {
                if (relationId !== null) markRouteUploaded(relationId)
                unloadRoute()
                showMasterPicker()
                // the list in hand was an answer from before this upload, so the variant
                // just uploaded would be named after the tags it no longer has. Asking
                // again reads the relations back from the OSM API, which is current.
                masterReloadBtn.click()
                return
            }

            unload()
        })
        .catch((error) => {
            console.error(error)
            showMessage("danger", "❌ Upload failed", error)
        })
        .finally(() => {
            submitUploadBtn.disabled = false
        })
}

submitDownloadBtn.onclick = async () => {
    submitDownloadBtn.disabled = true

    fetch("/download_osm_change", {
        method: "POST",
        headers: {
            "Content-Encoding": "deflate",
            "Content-Type": "application/json",
        },
        body: await deflateCompress({
            relationId: relationId,
            route: routeData,
            tags: relationTags,
            tagsOriginal: relationTagsOriginal,
            newStops: newStopsPayload(),
            newStopPositions: stopPositionsPayload(),
            stopAreas: stopAreasPayload(),
            naptanTagAdditions: tagAdditionsPayload(),
            ...routeMasterPayload(),
        }),
    })
        .then(async (resp) => {
            if (!resp.ok) {
                showMessage(
                    "danger",
                    `❌ Download failed - ${resp.status}`,
                    await resp.text(),
                )
                return
            }

            return resp.blob()
        })
        .then((blob) => {
            if (!blob) return

            const a = document.createElement("a")
            a.href = URL.createObjectURL(blob)
            const name = relationId !== null ? `${relationId}` : "new"
            a.download = `relatify_${name}_${new Date().toISOString().replace(/:/g, "_")}.osc`
            a.click()
        })
        .catch((error) => {
            console.error(error)
            showMessage("danger", "❌ Download failed", error)
        })
        .finally(() => {
            submitDownloadBtn.disabled = false
        })
}

// support &load=1 in query string
const urlParams = new URLSearchParams(window.location.search)
if (urlParams.get("load") === "1") loadRelationBtn.click()
