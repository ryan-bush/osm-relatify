import { map, openInOpenStreetMap } from "./map.js"

let popup = null

export function clearBusStopsPopup() {
    if (popup) {
        popup.removeFrom(map)
        popup = null
    }
}

const TAGS_ICON = `
    <svg class="mb-1" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/>
        <circle cx="8" cy="8" r="1.5"/>
    </svg>`

const ROAD_ICON = `
    <svg class="mb-1" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M4 21 8 3"/>
        <path d="M20 21 16 3"/>
        <path d="M12 6v3"/>
        <path d="M12 13v3"/>
    </svg>`

const LIST_ICON = `
    <svg class="mb-1" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <line x1="9" y1="6" x2="20" y2="6"/>
        <line x1="9" y1="12" x2="20" y2="12"/>
        <line x1="9" y1="18" x2="20" y2="18"/>
        <circle cx="4.5" cy="6" r="1"/>
        <circle cx="4.5" cy="12" r="1"/>
        <circle cx="4.5" cy="18" r="1"/>
    </svg>`

// `naptanTags` and `stopPosition`, when given, are each { label, onClick }: one for
// filling in NaPTAN tags, one for putting a stop position on the road.
// `onViewTags`, when given, opens the stop's full tag list.
export function showContextMenu(e, stop, naptanTags = null, onViewTags = null, stopPosition = null) {
    clearBusStopsPopup()

    const naptanTagsButton = naptanTags
        ? `<button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="bs-naptan-tags">
               ${TAGS_ICON}
               <div>${naptanTags.label}</div>
           </button>`
        : ""

    const stopPositionButton = stopPosition
        ? `<button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="bs-stop-position">
               ${ROAD_ICON}
               <div>${stopPosition.label}</div>
           </button>`
        : ""

    const viewTagsButton = onViewTags
        ? `<button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="bs-view-tags">
               ${LIST_ICON}
               <div>Tags</div>
           </button>`
        : ""

    popup = L.popup(e.latlng, {
        content: `
            <div class="btn-group text-center">
                ${naptanTagsButton}
                ${stopPositionButton}
                ${viewTagsButton}
                <button class="btn btn-sm btn-light d-flex flex-column align-items-center" id="bs-open-osm">
                    <img class="mb-1" src="/static/img/brands/openstreetmap.webp" width="24" alt="OpenStreetMap logo">
                    <div>Inspect</div>
                </button>
            </div>`,
        closeButton: false,
        className: "popup-sm",
        maxWidth: 400,
    }).openOn(map)

    // scoped to this popup, as one closed a moment ago may still be fading out
    const openOsmButton = popup.getElement().querySelector("#bs-open-osm")

    if (naptanTags) popup.getElement().querySelector("#bs-naptan-tags").onclick = naptanTags.onClick

    if (stopPosition) popup.getElement().querySelector("#bs-stop-position").onclick = stopPosition.onClick

    if (onViewTags) popup.getElement().querySelector("#bs-view-tags").onclick = onViewTags

    openOsmButton.onclick = () => {
        const id = stop.id.split("_")[0]
        openInOpenStreetMap(`${stop.type}/${id}`)
        popup.close()
    }
}

// Lists the NaPTAN tags for a stop already in OSM, to add them or to take them back out.
export function showNaptanTagsForm(latlng, { tags, added, onAdd, onRemove }) {
    clearBusStopsPopup()

    const content = document.createElement("div")
    content.className = "new-stop-form"
    content.innerHTML = `
        <div class="new-stop-title">${added ? "NaPTAN tags to add" : "Add missing NaPTAN tags"}</div>
        <div class="new-stop-naptan">Only tags the stop lacks are added; its existing tags are kept. Check this is the stop NaPTAN means.</div>
        <table class="table table-sm naptan-tags-table mb-2"><tbody></tbody></table>
        <button type="button" class="btn btn-sm w-100 ${added ? "btn-outline-danger" : "btn-primary"} naptan-tags-action">
            ${added ? "Don't add these tags" : "Add tags"}
        </button>`

    // set through the DOM, so NaPTAN's text is never parsed as HTML
    const tbody = content.querySelector("tbody")
    for (const [key, value] of Object.entries(tags)) {
        const row = tbody.insertRow()
        const keyCell = row.insertCell()
        keyCell.className = "key"
        keyCell.textContent = key
        row.insertCell().textContent = value
    }

    content.querySelector(".naptan-tags-action").onclick = () => {
        popup.close()
        if (added) onRemove()
        else onAdd()
    }

    popup = L.popup(latlng, {
        content: content,
        closeButton: false,
        className: "popup-form",
        minWidth: 260,
        maxWidth: 320,
    }).openOn(map)
}

const FORM_KEYS = ["name", "local_ref", "shelter", "bench"]

// mirrors make_new_stop_tags() and make_stop_position_tags() in bus_stop_creation.py:
// these are added on upload, so a preview without them would not be the whole story
function platformUploadTags(tags, routeType) {
    const result = { ...tags, highway: "bus_stop", public_transport: "platform" }
    if (routeType) result[routeType] = "yes"
    return result
}

function stopPositionUploadTags(tags, routeType) {
    const result = { public_transport: "stop_position" }
    if (routeType) result[routeType] = "yes"
    if (tags.name) result.name = tags.name
    return result
}

const yesNoOptions = `
    <option value="">Unknown</option>
    <option value="yes">Yes</option>
    <option value="no">No</option>`

// Adds a new stop when `stop` is null, starting from `tags` if given, otherwise edits
// that pending stop. Tags the form has no field for, such as naptan:*, are kept.
// `stopPosition` is { available, checked }; onSave is given the tags and whether a stop
// position on the road was asked for.
export function showNewStopForm(
    latlng,
    { stop = null, tags = null, nearby = null, stopPosition = null, routeType = null, onSave, onDelete = null },
) {
    clearBusStopsPopup()

    const initialTags = stop?.tags ?? tags ?? {}
    const atcoCode = initialTags["naptan:AtcoCode"]

    const form = document.createElement("form")
    form.className = "new-stop-form"
    form.innerHTML = `
        <div class="new-stop-title">${stop ? "New bus stop" : atcoCode ? "Add this stop from NaPTAN" : "Add a bus stop here"}</div>
        <div class="new-stop-naptan d-none"></div>
        <label>Name
            <input class="form-control form-control-sm" name="name" maxlength="255" required pattern=".*\\S.*">
        </label>
        <label>Local ref <span class="text-body-secondary">(stop letter or stand)</span>
            <input class="form-control form-control-sm" name="local_ref" maxlength="255">
        </label>
        <div class="d-flex gap-2">
            <label class="flex-fill">Shelter
                <select class="form-select form-select-sm" name="shelter">${yesNoOptions}</select>
            </label>
            <label class="flex-fill">Bench
                <select class="form-select form-select-sm" name="bench">${yesNoOptions}</select>
            </label>
        </div>
        <label class="new-stop-check">
            <input type="checkbox" name="stop_position">
            <span></span>
        </label>
        <div class="new-stop-nearby d-none"></div>
        <details class="new-stop-tags">
            <summary>All tags</summary>
            <div class="new-stop-tags-body"></div>
        </details>
        <div class="d-flex gap-2">
            <button type="submit" class="btn btn-sm btn-primary flex-fill">${stop ? "Save" : "Add stop"}</button>
            ${stop ? '<button type="button" class="btn btn-sm btn-outline-danger new-stop-delete">Delete</button>' : ""}
        </div>`

    // set through the DOM rather than the template, so typed text is never parsed as HTML
    for (const key of FORM_KEYS) form.elements[key].value = initialTags[key] ?? ""

    const stopPositionCheck = form.elements.stop_position
    const stopPositionLabel = stopPositionCheck.nextElementSibling

    if (stopPosition?.available) {
        stopPositionCheck.checked = stopPosition.checked
        stopPositionLabel.textContent = "Also mark where the bus halts, on the road"
    } else {
        stopPositionCheck.checked = false
        stopPositionCheck.disabled = true
        stopPositionLabel.textContent = stopPosition
            ? "No route road is close enough for a stop position"
            : "A stop position needs the route drawn first"
    }

    if (atcoCode) {
        const details = [
            `NaPTAN ${atcoCode}`,
            initialTags["naptan:Indicator"],
            initialTags["naptan:Bearing"] && `buses heading ${initialTags["naptan:Bearing"]}`,
        ]
        const notice = form.querySelector(".new-stop-naptan")
        notice.textContent = `${details.filter(Boolean).join(" · ")}. NaPTAN positions can be tens of metres out, so drag the stop to where the pole is.`
        notice.classList.remove("d-none")
    }

    if (nearby) {
        const notice = form.querySelector(".new-stop-nearby")
        const who = nearby.name ? `“${nearby.name}”` : "Another stop"
        notice.textContent = `${who} is ${Math.round(nearby.distance)} m away. Make sure this is not the same stop.`
        notice.classList.remove("d-none")
    }

    // Leaflet pans and zooms the map on arrow and +/- keys, which would swallow them here
    L.DomEvent.on(form, "keydown", L.DomEvent.stopPropagation)

    // what the stop carries as the form stands: the tags it came with, minus the ones
    // the form owns, plus whatever those fields now say
    function collectTags() {
        const collected = Object.fromEntries(
            Object.entries(initialTags).filter(([key]) => !FORM_KEYS.includes(key)),
        )
        for (const key of FORM_KEYS) {
            const value = form.elements[key].value.trim()
            if (value) collected[key] = value
        }
        return collected
    }

    const tagsBody = form.querySelector(".new-stop-tags-body")

    function refreshTags() {
        const collected = collectTags()
        const sections = [{ label: "Platform · new node", tags: platformUploadTags(collected, routeType) }]

        if (stopPositionCheck.checked) {
            sections.push({
                label: "Stop position · new node on the road",
                tags: stopPositionUploadTags(collected, routeType),
            })
        }

        tagsBody.replaceChildren(renderTagSections(sections))
    }

    refreshTags()
    form.addEventListener("input", refreshTags)
    form.addEventListener("change", refreshTags)

    form.onsubmit = (e) => {
        e.preventDefault()

        popup.close()
        onSave(collectTags(), stopPositionCheck.checked)
    }

    if (stop) {
        form.querySelector(".new-stop-delete").onclick = () => {
            popup.close()
            onDelete()
        }
    }

    popup = L.popup(latlng, {
        content: form,
        closeButton: false,
        className: "popup-form",
        minWidth: 240,
        maxWidth: 260,
    }).openOn(map)

    form.elements.name.focus()
}

// Every tag on a stop, read-only. `sections` is one entry per element of the collection,
// as { label, tags }, so a platform and its stop position are shown together.
export function showAllTagsForm(latlng, sections) {
    clearBusStopsPopup()

    const content = document.createElement("div")
    content.className = "new-stop-form"
    content.innerHTML = `<div class="new-stop-title">Tags</div>`
    content.append(renderTagSections(sections))

    popup = L.popup(latlng, {
        content: content,
        closeButton: false,
        className: "popup-form popup-tags",
        minWidth: 260,
        maxWidth: 340,
    }).openOn(map)
}

// One labelled table per element, built through the DOM so nothing is parsed as HTML.
export function renderTagSections(sections) {
    const content = document.createDocumentFragment()

    for (const { label, tags } of sections) {
        const heading = document.createElement("div")
        heading.className = "all-tags-heading"
        // set through the DOM, so nothing from OSM is ever parsed as HTML
        heading.textContent = label
        content.append(heading)

        const entries = Object.entries(tags)

        if (!entries.length) {
            const empty = document.createElement("div")
            empty.className = "all-tags-empty"
            empty.textContent = "No tags"
            content.append(empty)
            continue
        }

        const table = document.createElement("table")
        table.className = "table table-sm naptan-tags-table mb-2"
        const tbody = table.createTBody()

        for (const [key, value] of entries.sort(([a], [b]) => a.localeCompare(b))) {
            const row = tbody.insertRow()
            const keyCell = row.insertCell()
            keyCell.className = "key"
            keyCell.textContent = key
            row.insertCell().textContent = value
        }

        content.append(table)
    }

    return content
}

// Offers the stop position for a stop already in OSM, to add it or take it back out.
export function showStopPositionForm(latlng, { tags, added, distance, onAdd, onRemove }) {
    clearBusStopsPopup()

    const content = document.createElement("div")
    content.className = "new-stop-form"
    content.innerHTML = `
        <div class="new-stop-title">${added ? "Stop position to add" : "Add a stop position"}</div>
        <div class="new-stop-naptan"></div>
        <button type="button"
                class="btn btn-sm w-100 stop-position-action ${added ? "btn-outline-danger" : "btn-primary"}">
            ${added ? "Don't add it" : "Add stop position"}
        </button>`

    content.querySelector(".new-stop-naptan").textContent = added
        ? "A new node on the road, created with the route and added to the relation."
        : `A new node goes on the route ${Math.round(distance)} m away, where the bus halts, ` +
          "and joins the relation. Check the route runs the way the bus does here."

    content.querySelector(".new-stop-title").after(renderTagSections([{ label: "New node on the road", tags: tags }]))

    content.querySelector(".stop-position-action").onclick = () => {
        popup.close()
        if (added) onRemove()
        else onAdd()
    }

    popup = L.popup(latlng, {
        content: content,
        closeButton: false,
        className: "popup-form",
        minWidth: 260,
        maxWidth: 320,
    }).openOn(map)
}
