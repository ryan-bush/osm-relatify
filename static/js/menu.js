import { busStopData, processBusStopData } from "./busStopsLayer.js"
import {
    downloadHistoryData,
    processRelationDownloadTriggers,
} from "./downloadTriggers.js"
import { map } from "./map.js"
import { showMessage } from "./messageBox.js"
import {
    processRelationTags,
    relationTags,
    relationTagsOriginal,
    setRecalcHandler,
    unloadRelationTags,
} from "./tagEditor.js"
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
import { requestCalcBusRoute, routeData } from "./waysRoute.js"

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

// tagEditor.js cannot import the route module directly without closing an import cycle,
// so the dependency is registered from here instead. The call is wrapped rather than
// passed by reference so the binding is only read once the modules have finished loading.
setRecalcHandler(() => requestCalcBusRoute())

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

loadRelationForm.addEventListener("submit", (e) => {
    e.preventDefault()

    if (loadRelationBtn.classList.contains("is-loading")) return

    relationId = Number.parseInt(relationIdInput.value)
    relationIdInput.disabled = true
    loadRelationBtn.classList.add("btn-secondary")
    loadRelationBtn.classList.add("is-loading")
    loadRelationBtn.innerHTML = busAnimationElement.innerHTML

    isCreating = false
    newRouteType = null
    showRelationIdentity()

    fetch("/query", {
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

            processFetchRelationData(data)
        })
        .catch((error) => {
            console.error(error)
            showMessage("danger", "❌ Relation load failed", error)
        })
        .finally(() => {
            relationIdInput.disabled = false
            loadRelationBtn.classList.remove("btn-secondary")
            loadRelationBtn.classList.remove("is-loading")
            loadRelationBtn.innerHTML = "Load"
        })
})

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
                    "Fill in <b>name</b>, <b>ref</b>, <b>from</b> and <b>to</b> in the tag table before uploading.",
            )
        })
        .catch((error) => {
            isCreating = false
            console.error(error)
            showMessage("danger", "❌ Could not start a new relation", error)
        })
        .finally(() => {
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
}

export const processRouteWarnings = (data) => {
    if (activeView === "submit") switchView("edit")

    editSubmitBtn.classList.add("d-none")

    editWarnings.innerHTML = ""
    let highestSeverityLevel = 0

    for (const warning of data.warnings) {
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
            warning.message === "Some stops are not reached"
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

    editSubmitBtn.classList.toggle("mt-2", data.warnings.length > 0)

    if (highestSeverityLevel === 0) editSubmitBtn.classList.remove("d-none")
}

const unload = () => {
    switchView("load")

    processRelationEndpointData(null)
    processRelationWaysData(null)
    processRelationDownloadTriggers(null)
    processBusStopData(null)
    unloadRelationTags()
    submitComment.value = ""

    relationId = null
    isCreating = false
    newRouteType = null
}

editBackBtn.onclick = unload

editReloadBtn.onclick = async () => {
    editBackBtn.disabled = true
    editReloadBtn.disabled = true

    const defaultInnerText = editReloadBtn.innerText
    editReloadBtn.innerText = "Reloading..."

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
            editReloadBtn.innerText = defaultInnerText

            editBackBtn.disabled = false
            editReloadBtn.disabled = false
        })
}

// mirrors make_comment() in main.py purely to show what will be used when the field is
// left blank; the server generates the comment it actually uploads
const makeDefaultComment = () => {
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

        routeSummary.appendChild(
            createElementFromHTML(`
        <div class="route-summary-item">
            <img class="stop-icon" src="/static/img/bus_stop.webp" alt="Bus stop icon" height="28">
            <div class="stop-name">${styleStopName(getBusCollectionName(collection))}</div>
            <div class="stop-info">
                ${
                    isPlatform
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
