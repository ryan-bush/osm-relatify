import { createElementFromHTML, mapboxToken } from "./utils.js"

// map
function getInitialMapView() {
    const hash = window.location.hash
    const hashPattern = /map=(\d+)\/(-?\d+\.\d+)\/(-?\d+\.\d+)/

    if (hashPattern.test(hash)) {
        const [_, zoom, lat, lng] = hash.match(hashPattern)
        return [Number.parseFloat(lat), Number.parseFloat(lng), Number.parseInt(zoom, 10)]
    }

    return [52.232, 21.0068, 6] // default view
}

const [defaultLat, defaultLng, defaultZoom] = getInitialMapView()

export const canvasRenderer = L.canvas({
    padding: 0,
})

// Neither tile server draws past z19, so beyond that the z19 tiles are stretched rather
// than asked for tiles that do not exist. Blurry, but the two sides of a road and the
// stop position between them are far easier to tell apart with the room to spare.
const MAX_ZOOM = 21
const MAX_TILE_ZOOM = 19
const IMAGERY_MIN_ZOOM = 17

export const map = L.map("map", {
    center: [defaultLat, defaultLng],
    zoom: defaultZoom,
    zoomControl: false,
    // Fixed, rather than taken from the tile layers: the imagery's two halves each cover
    // only part of the range, and switching to it would zoom the map into their limits.
    minZoom: 0,
    maxZoom: MAX_ZOOM,
})

// openstreetmap-like url hash
export function getLocationHash() {
    const center = map.getCenter()
    const zoom = map.getZoom()
    return `map=${zoom}/${center.lat.toFixed(6)}/${center.lng.toFixed(6)}`
}

export const openInOpenStreetMap = (path) => {
    if (!path) path = ""

    window.open(`https://www.openstreetmap.org/${path}#${getLocationHash()}`, "_blank")
}

function updateUrl() {
    window.location.hash = getLocationHash()
}

map.on("moveend", updateUrl)
map.on("zoomend", updateUrl)

map.on("contextmenu", (e) => {
    // prevent default right-click context menu from appearing
    // this is not to hide anything, it's just for convenience
    e.originalEvent.preventDefault()
})

// map tiles
const attribution = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

// Imagery is there for placing stops, so it only shows from IMAGERY_MIN_ZOOM: further out
// the OSM map is drawn in its place. The name is what goes in imagery_used.
const imageryLayer = (name, url, imageryAttribution) =>
    L.layerGroup(
        [
            L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
                attribution: attribution,
                maxZoom: IMAGERY_MIN_ZOOM - 1,
            }),
            L.tileLayer(url, {
                attribution: imageryAttribution,
                minZoom: IMAGERY_MIN_ZOOM,
                maxZoom: MAX_ZOOM,
                maxNativeZoom: MAX_TILE_ZOOM,
            }),
        ],
        { imagery: name },
    )

const baseLayers = {
    OpenStreetMap: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: attribution,
        maxZoom: MAX_ZOOM,
        maxNativeZoom: MAX_TILE_ZOOM,
    }),
    "OpenStreetMap DE": L.tileLayer("https://tile.openstreetmap.de/{z}/{x}/{y}.png", {
        attribution: attribution,
        maxZoom: MAX_ZOOM,
        maxNativeZoom: MAX_TILE_ZOOM,
    }),
    // Esri permits tracing its imagery into OSM
    "Esri World Imagery (zoom 17+)": imageryLayer(
        "Esri World Imagery",
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        'Imagery &copy; <a href="https://www.esri.com/" target="_blank">Esri</a> and its providers',
    ),
}

// Mapbox permits tracing its imagery into OSM too, but needs a token of our own; see
// MAPBOX_TOKEN in config.py
if (mapboxToken) {
    baseLayers["Mapbox Satellite (zoom 17+)"] = imageryLayer(
        "Mapbox Satellite",
        `https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}{r}.jpg?access_token=${encodeURIComponent(mapboxToken)}`,
        '&copy; <a href="https://www.mapbox.com/about/maps/" target="_blank">Mapbox</a> ' +
            '&copy; <a href="https://www.maxar.com/" target="_blank">Maxar</a> ' +
            '<strong><a href="https://apps.mapbox.com/feedback/" target="_blank">Improve this map</a></strong>',
    )
}

const selectedBaseLayer = localStorage.getItem("baseLayer")
let activeBaseLayerName = selectedBaseLayer in baseLayers ? selectedBaseLayer : "OpenStreetMap"

baseLayers[activeBaseLayerName].addTo(map)

L.control.layers(baseLayers).addTo(map)

// the imagery shown since the last upload, which the next changeset credits
const imageryUsed = new Set()

const shownImagery = () =>
    map.getZoom() >= IMAGERY_MIN_ZOOM ? baseLayers[activeBaseLayerName].options.imagery : undefined

export const isImageryActive = () => shownImagery() !== undefined

const noteImageryUsed = () => {
    if (isImageryActive()) imageryUsed.add(shownImagery())
}

noteImageryUsed()

export const imageryUsedPayload = () => [...imageryUsed]

// after an upload, only what stays on screen is used for the next one
export function resetImageryUsed() {
    imageryUsed.clear()
    noteImageryUsed()
}

// "imagerychange" tells the layers drawn over the map when imagery comes or goes, be it
// from picking another base layer or from zooming across IMAGERY_MIN_ZOOM
let imageryWasActive = isImageryActive()

const onImageryMaybeChanged = () => {
    noteImageryUsed()
    if (isImageryActive() === imageryWasActive) return
    imageryWasActive = isImageryActive()
    map.fire("imagerychange")
}

map.on("baselayerchange", (e) => {
    localStorage.setItem("baseLayer", e.name)
    activeBaseLayerName = e.name
    onImageryMaybeChanged()
})

map.on("zoomend", onImageryMaybeChanged)

// map controls
L.control.zoom({ position: "topright" }).addTo(map)
L.control.scale().addTo(map)

// open in openstreetmap
class OsmButton extends L.Control {
    onAdd = () => {
        const div = createElementFromHTML(`
            <div class="leaflet-bar leaflet-control leaflet-control-custom" title="Open in OpenStreetMap">
                <a href="javascript:;">
                    <img src="/static/img/brands/openstreetmap.webp" height="24" alt="Open in OpenStreetMap">
                </a>
            </div>`)

        div.onclick = () => openInOpenStreetMap()

        return div
    }
}

new OsmButton({ position: "topright" }).addTo(map)

// data download progress
class DownloadBar extends L.Control {
    onAdd = () => {
        const div = createElementFromHTML(`
            <div id="download-bar" class="leaflet-bar leaflet-control leaflet-control-custom download-bar d-none">
                <p class="mb-0">Downloading map data...</p>
                <div class="progress" style="height:8px">
                    <div class="progress-bar progress-bar-striped progress-bar-animated" style="width:100%"></div>
                </div>
            </div>`)

        return div
    }
}

new DownloadBar({ position: "bottomright" }).addTo(map)

// The bar over the map, which is the one place a wait is visible wherever it started:
// panning to grow the area, or loading a relation from the menu or a route master's list.
export const showDownloadBar = (message = "Downloading map data...") => {
    const bar = document.getElementById("download-bar")

    bar.querySelector("p").textContent = message
    bar.classList.remove("d-none")
}

export const hideDownloadBar = () => {
    document.getElementById("download-bar").classList.add("d-none")
}
