const editTags = document.getElementById("edit-tags")
const editTagsToggle = document.getElementById("edit-tags-toggle")
const editTagsAdd = document.getElementById("edit-tags-add")

// tags exactly as loaded from OSM - the baseline the edits are diffed against server-side
export let relationTagsOriginal = null
// working copy - what gets submitted and what the route calculation reads
export let relationTags = null

// always offered, in this order; an empty field means the tag is absent
const FEATURED_KEYS = ["name", "ref", "from", "via", "to", "network", "operator", "colour", "roundtrip"]
// interpreted server-side when loading the relation, so they are shown but not editable
const LOCKED_KEYS = new Set(["type", "route", "disused:route", "was:route", "public_transport:version"])
// changing these changes the calculated route, not just the tags that get uploaded
const RECALC_KEYS = new Set(["roundtrip"])

// ordered source of truth for the editor; relationTags is derived from it
let entries = []
let showAll = false

// set by menu.js - avoids importing the route module and closing an import cycle
let onRecalcNeeded = () => {}

export const setRecalcHandler = (handler) => {
    onRecalcNeeded = handler
}

const isFeatured = (key) => FEATURED_KEYS.includes(key)

const buildEntries = (tags) => {
    const result = FEATURED_KEYS.map((key) => ({ key, value: tags[key] ?? "" }))

    for (const key of Object.keys(tags).sort()) {
        if (!isFeatured(key)) result.push({ key, value: tags[key] })
    }

    return result
}

const syncWorkingCopy = () => {
    const next = {}

    for (const { key, value } of entries) {
        const trimmedKey = key.trim()
        const trimmedValue = value.trim()
        if (trimmedKey && trimmedValue) next[trimmedKey] = trimmedValue
    }

    relationTags = next
}

const isModified = (entry) => {
    const key = entry.key.trim()
    if (!key) return entry.value.trim() !== ""
    return (relationTagsOriginal[key] ?? "") !== entry.value.trim()
}

// a key typed into two rows at once would silently lose one of them
const isDuplicate = (entry) => {
    const key = entry.key.trim()
    if (!key) return false
    return entries.filter((other) => other.key.trim() === key).length > 1
}

// rendered rows paired with the entry each one edits
let rows = []

const markRow = (tr, entry) => {
    tr.classList.toggle("tag-modified", isModified(entry))
    tr.classList.toggle("tag-duplicate", isDuplicate(entry))
}

const markAllRows = () => {
    for (const { tr, entry } of rows) markRow(tr, entry)
}

const makeValueInput = (entry) => {
    // roundtrip is effectively an enum; a free-text field invites typos that change routing
    if (entry.key === "roundtrip") {
        const select = document.createElement("select")
        select.className = "form-select form-select-sm"

        const options = ["", "yes", "no"]
        // never silently drop a value we do not recognize
        if (entry.value && !options.includes(entry.value)) options.push(entry.value)

        for (const option of options) {
            const el = document.createElement("option")
            el.value = option
            el.textContent = option || "—"
            select.appendChild(el)
        }

        select.value = entry.value
        return select
    }

    const input = document.createElement("input")
    input.type = "text"
    input.className = "form-control form-control-sm"
    input.value = entry.value
    input.maxLength = 255
    return input
}

const makeRow = (entry) => {
    const tr = document.createElement("tr")
    const locked = LOCKED_KEYS.has(entry.key)

    const keyCell = document.createElement("td")
    keyCell.className = "key"

    if (isFeatured(entry.key) || locked) {
        keyCell.textContent = entry.key
    } else {
        const keyInput = document.createElement("input")
        keyInput.type = "text"
        keyInput.className = "form-control form-control-sm"
        keyInput.value = entry.key
        keyInput.maxLength = 255
        keyInput.placeholder = "key"
        keyInput.oninput = () => {
            entry.key = keyInput.value
            syncWorkingCopy()
            // a renamed key can collide with another row, so refresh every marker
            markAllRows()
        }
        keyCell.appendChild(keyInput)
    }

    const valueCell = document.createElement("td")
    valueCell.className = "value"

    const valueInput = makeValueInput(entry)
    valueInput.disabled = locked
    valueInput.oninput = () => {
        entry.value = valueInput.value
        syncWorkingCopy()
        markRow(tr, entry)
        if (RECALC_KEYS.has(entry.key)) onRecalcNeeded()
    }
    valueCell.appendChild(valueInput)

    tr.append(keyCell, valueCell)

    if (!isFeatured(entry.key) && !locked) {
        const removeCell = document.createElement("td")
        removeCell.className = "remove"

        const removeBtn = document.createElement("button")
        removeBtn.type = "button"
        removeBtn.className = "btn btn-link btn-sm p-0"
        removeBtn.textContent = "✕"
        removeBtn.title = `Remove ${entry.key}`
        removeBtn.onclick = () => {
            entries.splice(entries.indexOf(entry), 1)
            syncWorkingCopy()
            render()
            if (RECALC_KEYS.has(entry.key)) onRecalcNeeded()
        }

        removeCell.appendChild(removeBtn)
        tr.appendChild(removeCell)
    }

    if (locked) tr.classList.add("tag-locked")
    markRow(tr, entry)

    return tr
}

const visibleEntries = () => entries.filter((entry) => showAll || isFeatured(entry.key))

const render = () => {
    const shown = visibleEntries()

    const hiddenCount = entries.length - shown.length

    rows = shown.map((entry) => ({ tr: makeRow(entry), entry }))
    editTags.replaceChildren(...rows.map(({ tr }) => tr))
    editTagsToggle.textContent = showAll ? "Show fewer tags" : `Show all tags (${hiddenCount})`
    // nothing to reveal, but stay available while expanded so the view can be collapsed again
    editTagsToggle.classList.toggle("d-none", !showAll && hiddenCount === 0)
}

editTagsToggle.onclick = () => {
    showAll = !showAll
    render()
}

editTagsAdd.onclick = () => {
    // a new row has no key yet, so it is only reachable in the full view
    showAll = true
    entries.push({ key: "", value: "" })
    render()

    editTags.lastElementChild?.querySelector("input")?.focus()
}

export const processRelationTags = (data) => {
    relationTagsOriginal = { ...data.tags }
    entries = buildEntries(data.tags)
    showAll = false

    syncWorkingCopy()
    render()
}

export const unloadRelationTags = () => {
    relationTagsOriginal = null
    relationTags = null
    entries = []
    showAll = false

    editTags.replaceChildren()
}
