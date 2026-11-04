import { corridorCells } from "./areaCells.js"
import { hideDownloadBar, map, showDownloadBar } from "./map.js"
import { newRouteType, processFetchRelationData, relationId } from "./menu.js"
import { showMessage } from "./messageBox.js"
import { createElementFromHTML, deflateCompress, downloadGridSize, downloadMaxCells } from "./utils.js"

export let downloadHistoryData = null
export let downloadTriggersData = null
let scheduledCells = []
let downloadingCells = []
// a "download this view" or selected area request in flight; one download at a time, as
// each answer carries the history the next must be sent with
let downloadingView = false

let processDownloadTriggersAbortController = null

const isDownloading = () => downloadingCells.length > 0 || downloadingView

export function processRelationDownloadTriggers(fetchData) {
    setDownloadViewButtonVisible(Boolean(fetchData))

    if (fetchData) {
        downloadHistoryData = fetchData.downloadHistory
        downloadTriggersData = fetchData.downloadTriggers

        if (!fetchData.fetchMerge) {
            if (processDownloadTriggersAbortController) {
                processDownloadTriggersAbortController.abort()
                processDownloadTriggersAbortController = null
            }

            scheduledCells = []
            downloadingCells = []
            downloadingView = false
        }
    } else {
        if (processDownloadTriggersAbortController) {
            processDownloadTriggersAbortController.abort()
            processDownloadTriggersAbortController = null
        }

        downloadHistoryData = null
        downloadTriggersData = null
        scheduledCells = []
        downloadingCells = []
        downloadingView = false
        stopAreaSelect()
    }

    updateDownloadViewButton()
    updateDownloadedArea()
    updateAreaSelect()
}

// Everything outside the downloaded cells is shaded, so the edge of what is downloaded
// can be seen before a route runs off it. Below the ways, and never in the way of a click.
map.createPane("downloadedArea").style.zIndex = 350

let downloadedAreaLayer = null

// the outline of the union of cells, as rings of [lat, lng]: each cell's edges, less
// those shared with a neighbour, chained end to end. Even-odd filling means it does
// not matter which way a ring turns where two cells meet only at a corner.
function downloadedAreaRings(cells) {
    const key = (x, y) => `${x},${y}`
    const present = new Set(cells.map(({ x, y }) => key(x, y)))
    const edges = new Map()

    for (const { x, y } of cells) {
        const sides = [
            [x, y, x + 1, y, key(x, y - 1)],
            [x + 1, y, x + 1, y + 1, key(x + 1, y)],
            [x + 1, y + 1, x, y + 1, key(x, y + 1)],
            [x, y + 1, x, y, key(x - 1, y)],
        ]
        for (const [x1, y1, x2, y2, neighbour] of sides) {
            if (present.has(neighbour)) continue
            const start = key(x1, y1)
            if (!edges.has(start)) edges.set(start, [])
            edges.get(start).push([x2, y2])
        }
    }

    const rings = []
    for (const [start, ends] of edges) {
        while (ends.length > 0) {
            const ring = []
            let [x, y] = start.split(",").map(Number)
            let current = start
            do {
                ring.push([y * downloadGridSize, x * downloadGridSize])
                ;[x, y] = edges.get(current).pop()
                current = key(x, y)
            } while (current !== start)
            rings.push(ring)
        }
    }
    return rings
}

function updateDownloadedArea() {
    if (downloadedAreaLayer) {
        downloadedAreaLayer.remove()
        downloadedAreaLayer = null
    }

    const cells = downloadHistoryData?.history.flat() ?? []
    if (cells.length === 0) return

    // the world, a few times over for a map panned round the antimeridian
    const world = [
        [-85, -540],
        [85, -540],
        [85, 540],
        [-85, 540],
    ]

    downloadedAreaLayer = L.polygon([world, ...downloadedAreaRings(cells)], {
        pane: "downloadedArea",
        renderer: L.svg({ pane: "downloadedArea" }),
        stroke: false,
        fillColor: "#000",
        fillOpacity: 0.2,
        fillRule: "evenodd",
        interactive: false,
    }).addTo(map)
}

export const downloadTrigger = (id) => {
    if (downloadTriggersData?.[id]) {
        // compared by position: every way's cells are objects of their own, so the
        // same cell asked for by neighbouring ways would otherwise be downloaded twice
        const cellKey = ({ x, y }) => `${x},${y}`
        const queued = new Set([...downloadingCells, ...scheduledCells].map(cellKey))
        const newScheduledCells = downloadTriggersData[id].filter((cell) => !queued.has(cellKey(cell)))
        if (newScheduledCells.length > 0) {
            scheduledCells = scheduledCells.concat(newScheduledCells)
            processDownloadTriggers()
        }
    }
}

export const processDownloadTriggers = async (_retrying = false) => {
    if (_retrying) {
        if (downloadingCells.length === 0) return
    } else {
        if (isDownloading() || scheduledCells.length === 0) return

        downloadingCells = scheduledCells
        scheduledCells = []
    }

    showDownloadBar()
    updateDownloadViewButton()

    processDownloadTriggersAbortController = new AbortController()

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
            downloadTargets: downloadingCells,
        }),
        signal: processDownloadTriggersAbortController.signal,
    })
        .then(async (resp) => {
            // a busy or unreachable server is worth asking again; one that refused the
            // request will refuse it the same way every time
            if (resp.status >= 500) {
                console.error(resp)
                throw new Error("HTTP error")
            }

            if (!resp.ok) {
                finishDownload()
                showMessage("danger", `❌ Download failed - ${resp.status}`, await resp.text())
                return
            }

            const data = await resp.json()

            // Past this point the answer is in hand, so a failure is in handling it, and
            // asking again would only get the same answer and fail the same way, forever.
            try {
                processFetchRelationData(data)
            } catch (error) {
                console.error(error)
                finishDownload()
                showMessage("danger", "❌ Could not show the downloaded map data", error)
                return
            }

            downloadingCells = []
            updateDownloadViewButton()

            if (scheduledCells.length > 0) processDownloadTriggers()
            else hideDownloadBar()
        })
        .catch((error) => {
            if (error.name !== "AbortError") {
                console.error(error)
                setTimeout(() => {
                    processDownloadTriggers(true)
                }, 1000)
            } else {
                hideDownloadBar()
            }
        })
}

// Gives up on the cells being downloaded, so a click at the edge can ask for them again.
function finishDownload() {
    downloadingCells = []
    scheduledCells = []
    updateDownloadViewButton()
    hideDownloadBar()
}

// Everything in the map view at once, for a route too long to grow a few roads at a time
// by clicking at the edge of what is downloaded. The server works out which cells are
// new and refuses a view with too many of them.
const downloadView = () => {
    const bounds = map.getBounds()

    downloadRegion(
        {
            bounds: [bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast()],
        },
        "Downloading this view...",
    )
}

// One download of a region asked for as a whole: the view's `bounds`, or the cells of an
// `area` selected on the map.
const downloadRegion = async (region, message) => {
    if (!downloadHistoryData || isDownloading()) return

    downloadingView = true
    updateDownloadViewButton()
    showDownloadBar(message)

    processDownloadTriggersAbortController = new AbortController()

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
            ...region,
        }),
        signal: processDownloadTriggersAbortController.signal,
    })
        .then(async (resp) => {
            if (!resp.ok) {
                const detail = await resp
                    .json()
                    .then((body) => body.detail)
                    .catch(() => null)

                if (resp.status === 400 && detail)
                    showMessage("warning", "Nothing downloaded", detail)
                else
                    showMessage(
                        "danger",
                        `❌ Download failed - ${resp.status}`,
                        detail ?? "",
                    )
                return
            }

            processFetchRelationData(await resp.json())
        })
        .catch((error) => {
            if (error.name !== "AbortError") {
                console.error(error)
                showMessage("danger", "❌ Download failed", error)
            }
        })
        .finally(() => {
            downloadingView = false
            updateDownloadViewButton()
            updateAreaSelect()

            // edge clicks made while this was downloading were held back for it
            if (scheduledCells.length > 0) processDownloadTriggers()
            else if (!isDownloading()) hideDownloadBar()
        })
}

class DownloadViewButton extends L.Control {
    onAdd = () => {
        const div = createElementFromHTML(`
            <div id="download-view" class="leaflet-bar leaflet-control leaflet-control-custom d-none">
                <a href="javascript:;" role="button" title="Download this view" aria-label="Download this view"
                    class="download-view-button d-flex align-items-center justify-content-center">
                    <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                        <path d="M1 1h4v1.5H2.5V5H1zm10 0h4v4h-1.5V2.5H11zM1 11h1.5v2.5H5V15H1zm12.5 0H15v4h-4v-1.5h2.5z"/>
                        <path d="M7.25 4h1.5v4.69l1.72-1.72 1.06 1.06L8 11.56 4.47 8.03l1.06-1.06 1.72 1.72z"/>
                    </svg>
                </a>
                <a href="javascript:;" role="button" title="Download along a line drawn on the map"
                    aria-label="Download along a line drawn on the map" aria-pressed="false"
                    class="area-select-button d-flex align-items-center justify-content-center">
                    <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" aria-hidden="true">
                        <path d="M2 13.5 6 8l4 2.5 4-7" stroke-width="5" stroke-opacity=".25" stroke-linejoin="round" stroke-linecap="round"/>
                        <path d="M2 13.5 6 8l4 2.5 4-7" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
                    </svg>
                </a>
            </div>`)

        L.DomEvent.disableClickPropagation(div)
        div.querySelector(".download-view-button").onclick = downloadView
        div.querySelector(".area-select-button").onclick = () => (areaSelecting ? stopAreaSelect() : startAreaSelect())

        return div
    }
}

new DownloadViewButton({ position: "topright" }).addTo(map)

function setDownloadViewButtonVisible(visible) {
    document
        .getElementById("download-view")
        .classList.toggle("d-none", !visible)
}

function updateDownloadViewButton() {
    const link = document.querySelector("#download-view .download-view-button")
    const busy = isDownloading()

    link.classList.toggle("leaflet-disabled", busy)
    link.setAttribute("aria-disabled", String(busy))
}

// Selecting an area to download: a line clicked out along the route, and the cells within
// CORRIDOR_WIDTH of it. A coach on the motorway needs the motorway, where the view around
// it at a zoom that fits the limit takes every town either side as well.

let areaSelecting = false
let areaPoints = []
let areaMouse = null
let areaLayer = null
let areaOverlay = null

map.createPane("areaSelect").style.zIndex = 460

class AreaSelectPanel extends L.Control {
    onAdd = () => {
        const div = createElementFromHTML(`
            <div id="area-select-panel" class="leaflet-bar leaflet-control area-select-panel d-none">
                <p class="mb-1">Click along the route to select the area either side of it to download. Right-click or Backspace takes back a point.</p>
                <p class="mb-2 small area-select-status"></p>
                <div class="d-flex gap-1">
                    <button type="button" class="btn btn-sm btn-primary area-select-download">Download</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary area-select-undo">Undo</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary area-select-cancel">Cancel</button>
                </div>
            </div>`)

        L.DomEvent.disableClickPropagation(div)
        L.DomEvent.disableScrollPropagation(div)
        div.querySelector(".area-select-download").onclick = downloadSelectedArea
        div.querySelector(".area-select-undo").onclick = undoAreaPoint
        div.querySelector(".area-select-cancel").onclick = stopAreaSelect

        return div
    }
}

new AreaSelectPanel({ position: "bottomright" }).addTo(map)

// the cells of the selection not yet downloaded
function selectedNewCells() {
    const downloaded = new Set((downloadHistoryData?.history.flat() ?? []).map(({ x, y }) => `${x},${y}`))

    return corridorCells(areaPoints, downloadGridSize).filter(({ x, y }) => !downloaded.has(`${x},${y}`))
}

function startAreaSelect() {
    if (areaSelecting || !downloadHistoryData) return

    areaSelecting = true
    areaPoints = []
    areaMouse = null

    // Over the map's layers, so a click while drawing neither adds a way to the route nor
    // opens a stop; the map below still pans and zooms, as the events reach its container.
    areaOverlay = L.DomUtil.create("div", "area-select-overlay", map.getContainer())
    // a right-click takes back the last point, rather than adding a stop there
    L.DomEvent.on(areaOverlay, "contextmenu", (e) => {
        L.DomEvent.stop(e)
        undoAreaPoint()
    })

    areaLayer = L.layerGroup().addTo(map)
    map.doubleClickZoom.disable()
    map.on("click", onAreaClick)
    map.on("mousemove", onAreaMouseMove)
    map.on("dblclick", downloadSelectedArea)
    document.addEventListener("keydown", onAreaKeyDown)

    updateAreaSelect()
}

function stopAreaSelect() {
    if (!areaSelecting) return

    areaSelecting = false
    areaPoints = []
    areaMouse = null

    areaOverlay.remove()
    areaOverlay = null
    areaLayer.remove()
    areaLayer = null

    map.doubleClickZoom.enable()
    map.off("click", onAreaClick)
    map.off("mousemove", onAreaMouseMove)
    map.off("dblclick", downloadSelectedArea)
    document.removeEventListener("keydown", onAreaKeyDown)

    updateAreaSelect()
}

function onAreaClick(e) {
    // the second click of a double-click, which finishes the line rather than extending it
    const last = areaPoints.at(-1)
    if (last && map.latLngToContainerPoint(last).distanceTo(e.containerPoint) < 5) return

    areaPoints.push([e.latlng.lat, e.latlng.lng])
    updateAreaSelect()
}

function onAreaMouseMove(e) {
    areaMouse = e.latlng
    updateAreaRubberBand()
}

function onAreaKeyDown(e) {
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return

    if (e.key === "Escape") stopAreaSelect()
    else if (e.key === "Enter") downloadSelectedArea()
    else if (e.key === "Backspace") undoAreaPoint()
    else return

    e.preventDefault()
}

function undoAreaPoint() {
    areaPoints.pop()
    updateAreaSelect()
}

function downloadSelectedArea() {
    const cells = selectedNewCells()
    if (cells.length === 0 || cells.length > downloadMaxCells || isDownloading()) return

    stopAreaSelect()
    downloadRegion({ area: cells }, "Downloading the selected area...")
}

function updateAreaRubberBand() {
    const band = areaLayer?.rubberBand
    if (!band) return

    const last = areaPoints.at(-1)
    band.setLatLngs(last && areaMouse ? [last, areaMouse] : [])
}

function updateAreaSelect() {
    const button = document.querySelector("#download-view .area-select-button")
    button.classList.toggle("area-select-active", areaSelecting)
    button.setAttribute("aria-pressed", String(areaSelecting))

    const panel = document.getElementById("area-select-panel")
    panel.classList.toggle("d-none", !areaSelecting)
    if (!areaSelecting) return

    const cells = selectedNewCells()
    const tooMany = cells.length > downloadMaxCells

    areaLayer.clearLayers()
    if (cells.length > 0) {
        L.polygon(downloadedAreaRings(cells), {
            pane: "areaSelect",
            color: tooMany ? "#dc3545" : "#0d6efd",
            weight: 1,
            fillOpacity: 0.2,
            fillRule: "evenodd",
            interactive: false,
        }).addTo(areaLayer)
    }
    if (areaPoints.length > 1) {
        L.polyline(areaPoints, {
            pane: "areaSelect",
            color: "#0d6efd",
            weight: 3,
            interactive: false,
        }).addTo(areaLayer)
    }
    for (const point of areaPoints) {
        L.circleMarker(point, {
            pane: "areaSelect",
            radius: 4,
            color: "#0d6efd",
            fillColor: "#fff",
            fillOpacity: 1,
            weight: 2,
            interactive: false,
        }).addTo(areaLayer)
    }
    areaLayer.rubberBand = L.polyline([], {
        pane: "areaSelect",
        color: "#0d6efd",
        weight: 2,
        dashArray: "4 6",
        interactive: false,
    }).addTo(areaLayer)
    updateAreaRubberBand()

    const status = panel.querySelector(".area-select-status")
    status.classList.toggle("text-danger", tooMany)
    status.classList.toggle("text-body-secondary", !tooMany)
    if (areaPoints.length === 0) status.textContent = "Double-click or press Enter to download, Esc to cancel."
    else if (cells.length === 0) status.textContent = "Everything selected is already downloaded."
    else if (tooMany)
        status.textContent = `${cells.length} cells is too many to download at once (at most ${downloadMaxCells}). Undo a point and download it in parts.`
    else status.textContent = `${cells.length} new cells. Double-click or press Enter to download.`

    const download = panel.querySelector(".area-select-download")
    download.disabled = cells.length === 0 || tooMany || isDownloading()
    panel.querySelector(".area-select-undo").disabled = areaPoints.length === 0
}
