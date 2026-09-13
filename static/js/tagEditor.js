// An editable table of a relation's tags. One relation, one editor: the route being
// edited has one, and so does anything else whose tags are edited alongside it, which is
// why this is a factory over the elements it draws into rather than a single instance.

import {
    buildEntries,
    isDuplicate,
    isModified,
    tagsFromEntries,
    visibleEntries,
} from "./tagEntries.js"

/**
 * @param tableBody      the <tbody> the rows are drawn into
 * @param toggleButton   reveals the tags that are not featured
 * @param addButton      appends an empty row
 * @param featuredKeys   always offered, in this order; an empty field means absent
 * @param lockedKeys     shown but not editable, being interpreted server-side
 * @param recalcKeys     changing one of these changes the calculated route, not just the
 *                       tags that get uploaded
 * @param enumKeys       keys whose value is a fixed set, drawn as a select rather than a
 *                       free-text field that invites typos
 * @param wideElement    gets the menu-wide class while every tag is shown, the full list
 *                       needing room for an editable key and value on every row
 * @param onChange       called with the working copy whenever it changes
 * @param onRecalcNeeded called when a recalc key changes
 */
export function createTagEditor({
    tableBody,
    toggleButton,
    addButton,
    featuredKeys = [],
    lockedKeys = new Set(),
    recalcKeys = new Set(),
    enumKeys = {},
    wideElement = null,
    onChange = () => {},
    onRecalcNeeded = () => {},
}) {
    // tags exactly as loaded from OSM - the baseline the edits are diffed against server-side
    let tagsOriginal = null
    // ordered source of truth for the editor; the working copy is derived from it
    let entries = []
    let showAll = false
    // rendered rows paired with the entry each one edits
    let rows = []

    const isFeatured = (key) => featuredKeys.includes(key)

    const syncWorkingCopy = () => onChange(tagsFromEntries(entries))

    const markRow = (tr, entry) => {
        tr.classList.toggle("tag-modified", isModified(entry, tagsOriginal))
        tr.classList.toggle("tag-duplicate", isDuplicate(entry, entries))
    }

    const markAllRows = () => {
        for (const { tr, entry } of rows) markRow(tr, entry)
    }

    const makeValueInput = (entry) => {
        const options = enumKeys[entry.key]

        if (options) {
            const select = document.createElement("select")
            select.className = "form-select form-select-sm"

            // never silently drop a value we do not recognize
            const shown = options.includes(entry.value) ? options : [...options, entry.value]

            for (const option of shown) {
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
        const locked = lockedKeys.has(entry.key)

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
            if (recalcKeys.has(entry.key)) onRecalcNeeded()
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
                if (recalcKeys.has(entry.key)) onRecalcNeeded()
            }

            removeCell.appendChild(removeBtn)
            tr.appendChild(removeCell)
        }

        if (locked) tr.classList.add("tag-locked")
        markRow(tr, entry)

        return tr
    }

    const render = () => {
        const shown = visibleEntries(entries, featuredKeys, showAll)

        const hiddenCount = entries.length - shown.length

        rows = shown.map((entry) => ({ tr: makeRow(entry), entry }))
        tableBody.replaceChildren(...rows.map(({ tr }) => tr))
        toggleButton.textContent = showAll ? "Show fewer tags" : `Show all tags (${hiddenCount})`
        // nothing to reveal, but stay available while expanded so the view can be collapsed again
        toggleButton.classList.toggle("d-none", !showAll && hiddenCount === 0)
        wideElement?.classList.toggle("menu-wide", showAll)
    }

    toggleButton.onclick = () => {
        showAll = !showAll
        render()
    }

    addButton.onclick = () => {
        // a new row has no key yet, so it is only reachable in the full view
        showAll = true
        entries.push({ key: "", value: "" })
        render()

        tableBody.lastElementChild?.querySelector("input")?.focus()
    }

    return {
        load(tags) {
            tagsOriginal = { ...tags }
            entries = buildEntries(tags, featuredKeys)
            showAll = false

            syncWorkingCopy()
            render()
        },

        unload() {
            tagsOriginal = null
            entries = []
            showAll = false

            onChange(null)
            tableBody.replaceChildren()
            // render() is not reached from here, so the widened menu is put back by hand
            wideElement?.classList.remove("menu-wide")
        },

        get tagsOriginal() {
            return tagsOriginal
        },
    }
}
