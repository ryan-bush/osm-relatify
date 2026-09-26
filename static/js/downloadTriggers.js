import { hideDownloadBar, map, showDownloadBar } from "./map.js"
import { newRouteType, processFetchRelationData, relationId } from "./menu.js"
import { showMessage } from "./messageBox.js"
import { createElementFromHTML, deflateCompress } from "./utils.js"

export let downloadHistoryData = null
export let downloadTriggersData = null
let scheduledCells = []
let downloadingCells = []
// a "download this view" request in flight; one download at a time, as each answer
// carries the history the next must be sent with
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
    }

    updateDownloadViewButton()
}

export const downloadTrigger = (id) => {
    if (downloadTriggersData?.[id]) {
        const newScheduledCells = downloadTriggersData[id].filter(
            (cell) => !downloadingCells.includes(cell),
        )
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
        .then((resp) => {
            if (!resp.ok) {
                console.error(resp)
                throw new Error("HTTP error")
            }

            return resp.json()
        })
        .then((data) => {
            processFetchRelationData(data)

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

// Everything in the map view at once, for a route too long to grow a few roads at a time
// by clicking at the edge of what is downloaded. The server works out which cells are
// new and refuses a view with too many of them.
const downloadView = async () => {
    if (!downloadHistoryData || isDownloading()) return

    const bounds = map.getBounds()

    downloadingView = true
    updateDownloadViewButton()
    showDownloadBar("Downloading this view...")

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
            bounds: [
                bounds.getSouth(),
                bounds.getWest(),
                bounds.getNorth(),
                bounds.getEast(),
            ],
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
                    class="d-flex align-items-center justify-content-center">
                    <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                        <path d="M1 1h4v1.5H2.5V5H1zm10 0h4v4h-1.5V2.5H11zM1 11h1.5v2.5H5V15H1zm12.5 0H15v4h-4v-1.5h2.5z"/>
                        <path d="M7.25 4h1.5v4.69l1.72-1.72 1.06 1.06L8 11.56 4.47 8.03l1.06-1.06 1.72 1.72z"/>
                    </svg>
                </a>
            </div>`)

        L.DomEvent.disableClickPropagation(div)
        div.querySelector("a").onclick = downloadView

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
    const link = document.querySelector("#download-view a")
    const busy = isDownloading()

    link.classList.toggle("leaflet-disabled", busy)
    link.setAttribute("aria-disabled", String(busy))
}
