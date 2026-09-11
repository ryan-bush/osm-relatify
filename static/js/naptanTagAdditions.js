// NaPTAN tags the user chose to add to stops already in OSM. Nothing is written until
// upload, where each stop is fetched again and only the tags it still lacks are added.
const additions = new Map()

const additionKey = (stop) => `${stop.type},${stop.id}`

export const getTagAddition = (stop) => additions.get(additionKey(stop))

export function addTagAddition(stop, tags) {
    additions.set(additionKey(stop), {
        type: stop.type,
        id: Number.parseInt(stop.id, 10),
        tags: tags,
    })
}

export const removeTagAddition = (stop) => additions.delete(additionKey(stop))

export const clearTagAdditions = () => additions.clear()

export const tagAdditionCount = () => additions.size

export const tagAdditionsPayload = () => Array.from(additions.values())
