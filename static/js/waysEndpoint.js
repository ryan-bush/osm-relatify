import { map, openInOpenStreetMap } from "./map.js"
import { requestCalcBusRoute } from "./waysRoute.js"
import {
    isUTurnAllowed,
    isUTurnFromOsm,
    nearestWayEnd,
    refreshUTurnMarkers,
    toggleUTurn,
} from "./waysUTurn.js"

let startMarker = null
let stopMarker = null
let popup = null

export let startWay = null
export let stopWay = null

export function processRelationEndpointData(fetchData) {
    if (fetchData) {
        if (fetchData.fetchMerge) {
            if (startWay && !fetchData.ways[startWay.id]) {
                setStartMarker(null)
                clearPopup()
            }

            if (stopWay && !fetchData.ways[stopWay.id]) {
                setStopMarker(null)
                clearPopup()
            }
        } else {
            setStartMarker(fetchData.startWay)
            setStopMarker(fetchData.stopWay)
        }
    } else {
        setStartMarker(null)
        setStopMarker(null)
        clearPopup()
    }
}

function createEndpointIcon(iconUrl) {
    const size = 24

    return L.icon({
        className: "endpoint-icon",
        iconUrl: iconUrl,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
    })
}

function onEndpointDataChanged() {
    requestCalcBusRoute()
}

function setStartMarker(way) {
    if (startMarker) {
        startMarker.removeFrom(map)
        startMarker = null
    }

    startWay = way

    if (startWay) {
        startMarker = L.marker(way.midpoint, {
            icon: createEndpointIcon("/static/img/start.webp"),
            interactive: false,
        }).addTo(map)
    }

    onEndpointDataChanged()
}

function setStopMarker(way) {
    if (stopMarker) {
        stopMarker.removeFrom(map)
        stopMarker = null
    }

    stopWay = way

    if (stopWay) {
        stopMarker = L.marker(way.midpoint, {
            icon: createEndpointIcon("/static/img/finish.webp"),
            interactive: false,
        }).addTo(map)
    }

    onEndpointDataChanged()
}

function clearPopup() {
    if (popup) {
        popup.removeFrom(map)
        popup = null
    }
}

const U_TURN_ICON = `
    <svg class="mb-1" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M6 20V9a5 5 0 0 1 10 0v11"/>
        <polyline points="12,16 16,20 20,16"/>
    </svg>`

export function showContextMenu(e, way) {
    clearPopup()

    // a U-turn belongs to one end of the way, so act on the end that was clicked
    const isStart = nearestWayEnd(way, e.latlng)
    // a turning circle in OSM already permits the turn; nothing for us to toggle
    const fromOsm = isUTurnFromOsm(way, isStart)
    const allowed = isUTurnAllowed(way, isStart)
    // turning around means leaving the way the direction it was entered from, so
    // there is nothing to toggle on a road that can only be driven one way
    const unusable = way.oneway

    let uTurnButton
    if (fromOsm) {
        uTurnButton = `<button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="ep-u-turn"
                               disabled title="This end is tagged highway=turning_circle in OpenStreetMap">
                           ${U_TURN_ICON}
                           <div>Turning circle</div>
                       </button>`
    } else if (unusable) {
        uTurnButton = `<button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="ep-u-turn"
                               disabled title="A oneway cannot be driven back the way it was entered">
                           ${U_TURN_ICON}
                           <div>No U-turn</div>
                       </button>`
    } else {
        uTurnButton = `<button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="ep-u-turn">
                           ${U_TURN_ICON}
                           <div>${allowed ? "<b>Disallow</b> U-turn" : "Allow <b>U-turn</b>"}</div>
                       </button>`
    }

    popup = L.popup(e.latlng, {
        content: `
            <div class="btn-group text-center">
                ${uTurnButton}
                <button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="ep-set-start">
                    <img class="mb-1" src="/static/img/start.webp" width="24" alt="Start icon">
                    <div>Set <b>START</b></div>
                </button>
                <button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="ep-set-stop">
                    <img class="mb-1" src="/static/img/finish.webp" width="24" alt="Finish icon">
                    <div>Set <b>END</b></div>
                </button>
                <button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="ep-open-osm">
                    <img class="mb-1" src="/static/img/brands/openstreetmap.webp" width="24" alt="OpenStreetMap logo">
                    <div>Inspect</div>
                </button>
            </div>`,
        closeButton: false,
        className: "popup-sm",
        maxWidth: 400,
    }).openOn(map)

    // scoped to this popup: a menu closed a moment ago is still fading out with the
    // same ids, and a document-wide lookup would wire up its buttons instead
    const content = popup.getElement()
    const setStartButton = content.querySelector("#ep-set-start")
    const setStopButton = content.querySelector("#ep-set-stop")
    const openOsmButton = content.querySelector("#ep-open-osm")

    setStartButton.onclick = () => {
        setStartMarker(way)
        popup.close()
    }

    setStopButton.onclick = () => {
        setStopMarker(way)
        popup.close()
    }

    openOsmButton.onclick = () => {
        const id = way.id.split("_")[0]
        openInOpenStreetMap(`way/${id}`)
        popup.close()
    }

    if (!fromOsm && !unusable) {
        content.querySelector("#ep-u-turn").onclick = () => {
            toggleUTurn(way, isStart)
            refreshUTurnMarkers()
            requestCalcBusRoute()
            popup.close()
        }
    }
}
