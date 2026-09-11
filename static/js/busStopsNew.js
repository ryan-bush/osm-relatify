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
}

export function addNewStop(latLng, tags) {
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
    }

    applyTags(stop, tags)
    newStops.set(stop.id, stop)
    return stop
}

export const updateNewStop = (stop, tags) => applyTags(stop, tags)

export const moveNewStop = (stop, latLng) => {
    stop.latLng = latLng
}

export const removeNewStop = (stop) => newStops.delete(stop.id)

export function clearNewStops() {
    newStops.clear()
    nextId = -1
}

export const newStopCount = () => newStops.size

export const newStopCollections = () =>
    Array.from(newStops.values(), (stop) => ({ platform: stop, stop: null }))

export const newStopsPayload = () =>
    Array.from(newStops.values(), (stop) => ({
        id: Number.parseInt(stop.id, 10),
        lat: stop.latLng[0],
        lon: stop.latLng[1],
        tags: stop.tags,
    }))
