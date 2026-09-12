// Bus stops the user placed on the map. Nothing about them exists in OSM until upload,
// when the same changeset creates them. Until then each carries a negative id, which is
// how an osmChange refers to an element it is creating, so the route and the relation
// members can point at a stop before OSM has assigned it a real id.
const newStops = new Map()
let nextId = -1

export const isNewStop = (stop) => Boolean(stop) && stop.id.startsWith("-")

// mirrors FetchRelationBusStop.from_data(), so a new stop reads like a downloaded one
const displayName = (tags) =>
    [tags.name, tags.local_ref].filter(Boolean).join(" ")

const applyTags = (stop, tags) => {
    stop.tags = tags
    stop.name = displayName(tags)
    stop.groupName = stop.name.toLowerCase()

    if (stop.stopPosition) {
        stop.stopPosition.node.name = stop.name
        stop.stopPosition.node.groupName = stop.groupName
    }
}

// The node on the road, shaped like a downloaded stop position. `placement` comes from
// planStopPosition(); passing null means this stop gets a platform only.
function applyPlacement(stop, placement) {
    if (!placement) {
        stop.stopPosition = null
        return
    }

    // reuses the id while the stop is only being moved, so the route keeps referring to it
    const node = stop.stopPosition?.node ?? {
        id: `${nextId--}`,
        type: "node",
        member: true,
        tags: { public_transport: "stop_position" },
        name: stop.name,
        groupName: stop.groupName,
        highway: null,
        public_transport: "stop_position",
    }

    node.latLng = placement.latLng
    stop.stopPosition = { node: node, placement: placement }
}

export function addNewStop(latLng, tags, placement = null) {
    // shaped like a downloaded platform, which is what the route calculation expects;
    // the tags that make it a stop are added by the server on upload
    const stop = {
        id: `${nextId--}`,
        type: "node",
        member: true,
        latLng: latLng,
        tags: {},
        name: "",
        groupName: "",
        highway: "bus_stop",
        public_transport: "platform",
        stopPosition: null,
    }

    applyTags(stop, tags)
    applyPlacement(stop, placement)
    newStops.set(stop.id, stop)
    return stop
}

export const updateNewStop = (stop, tags, placement = null) => {
    applyTags(stop, tags)
    applyPlacement(stop, placement)
}

export const moveNewStop = (stop, latLng, placement = null) => {
    stop.latLng = latLng
    applyPlacement(stop, placement)
}

export const removeNewStop = (stop) => newStops.delete(stop.id)

export function clearNewStops() {
    newStops.clear()
    nextId = -1
}

export const newStopCount = () => newStops.size

export const newStopCollections = () =>
    Array.from(newStops.values(), (stop) => ({
        platform: stop,
        stop: stop.stopPosition?.node ?? null,
    }))

// where on the road each stop position goes, for the route calculation to allow for
export const newStopPlacements = () =>
    Array.from(newStops.values(), (stop) => stop.stopPosition?.placement).filter(Boolean)

export const newStopsPayload = () =>
    Array.from(newStops.values(), (stop) => ({
        id: Number.parseInt(stop.id, 10),
        lat: stop.latLng[0],
        lon: stop.latLng[1],
        tags: stop.tags,
        stopPosition: stop.stopPosition
            ? {
                  id: Number.parseInt(stop.stopPosition.node.id, 10),
                  lat: stop.stopPosition.node.latLng[0],
                  lon: stop.stopPosition.node.latLng[1],
                  wayId: stop.stopPosition.placement.wayId,
                  afterNode: stop.stopPosition.placement.afterNode,
                  beforeNode: stop.stopPosition.placement.beforeNode,
              }
            : null,
    }))
