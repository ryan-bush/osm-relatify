const editTags = document.getElementById("edit-tags")

// tags exactly as loaded from OSM - the baseline the edits are diffed against server-side
export let relationTagsOriginal = null
// working copy - what gets submitted and what the route calculation reads
export let relationTags = null

// rendered in this order, when present on the relation
const DISPLAY_KEYS = ["fixme", "note", "from", "via", "to", "network", "operator", "roundtrip"]

const makeRow = (key, value) => {
    const tr = document.createElement("tr")

    const keyCell = document.createElement("td")
    keyCell.className = "key"
    keyCell.textContent = key

    const valueCell = document.createElement("td")
    valueCell.className = "value"
    valueCell.textContent = value

    tr.append(keyCell, valueCell)
    return tr
}

const makeHeaderRow = (text) => {
    const tr = document.createElement("tr")
    const td = document.createElement("td")

    td.colSpan = 2
    td.textContent = text

    tr.appendChild(td)
    return tr
}

const render = (nameOrRef) => {
    const rows = []

    if (nameOrRef) rows.push(makeHeaderRow(nameOrRef))

    for (const key of DISPLAY_KEYS) {
        const value = relationTags[key]
        if (value) rows.push(makeRow(key, value))
    }

    editTags.replaceChildren(...rows)
}

export const processRelationTags = (data) => {
    relationTagsOriginal = { ...data.tags }
    relationTags = { ...data.tags }

    render(data.nameOrRef)
}

export const unloadRelationTags = () => {
    relationTagsOriginal = null
    relationTags = null

    editTags.replaceChildren()
}
