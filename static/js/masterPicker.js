// A route master is not the thing that gets edited: its variants are. Given a master's
// id, this lists them and lets one be picked, so editing a line means loading the master
// once rather than copying each variant's id out of OSM in turn.

import { routeMasterIssues } from "./routeMasterChecks.js"
import { describeRoute } from "./routeMasters.js"
import { createTagEditor } from "./tagEditor.js"
import { osmUrl } from "./utils.js"

const summary = document.getElementById("master-summary")
const list = document.getElementById("master-routes")
const issueList = document.getElementById("master-issues")
const tagsTable = document.getElementById("master-tags-table")
const tagsControls = document.getElementById("master-tags-controls")
const tagsActions = document.getElementById("master-tags-actions")
const editTagsBtn = document.createElement("button")
const idElements = document.querySelectorAll(".view .master-id")
const urlElements = document.querySelectorAll(".view .master-url")

// the master being worked through, kept so going back to it does not fetch it again
let view = null
// variants uploaded during this visit, so it is plain which are still to do
const done = new Set()

// set by menu.js, which owns loading a relation
let onEdit = () => {}

export const setRouteEditHandler = (handler) => {
    onEdit = handler
}

// the master's own tags, edited here rather than from inside one of its variants. They go
// up as a changeset of their own: there is no route being edited to carry them along.
let editedTags = null
let editing = false

const tagEditor = createTagEditor({
    tableBody: document.getElementById("master-tags"),
    toggleButton: document.getElementById("master-tags-toggle"),
    addButton: document.getElementById("master-tags-add"),
    featuredKeys: ["name", "ref", "network", "operator", "colour"],
    // what makes it a master, and what the application reads it back by
    lockedKeys: new Set(["type", "route_master"]),
    wideElement: document.getElementById("menu"),
    onChange: (tags) => {
        editedTags = tags
    },
})

export const routeMasterTagsPayload = () =>
    view === null || !editing
        ? null
        : { id: view.id, tags: editedTags ?? {}, tagsOriginal: view.tags }

export const routeMasterTagsEdited = () => {
    if (!editing || editedTags === null) return false

    const keys = new Set([
        ...Object.keys(editedTags),
        ...Object.keys(view.tags),
    ])
    return [...keys].some(
        (key) => (editedTags[key] ?? "") !== (view.tags[key] ?? ""),
    )
}

const setEditing = (next) => {
    editing = next

    tagsTable.classList.toggle("d-none", !editing)
    tagsControls.classList.toggle("d-none", !editing)
    tagsActions.classList.toggle("d-none", !editing)

    if (editing) tagEditor.load(view.tags)
    else tagEditor.unload()

    editTagsBtn.textContent = editing ? "Stop editing tags" : "Edit its tags"
}

export const routeMasterView = () => view
export const routeMasterViewId = () => view?.id ?? null
export const markRouteUploaded = (id) => done.add(id)

export function setRouteMasterView(data) {
    view = data
    if (data === null) done.clear()
    // a fresh answer is not the one the open edits were made against
    setEditing(false)
}

const makeNote = (text) => {
    const div = document.createElement("div")
    div.className = "route-master-note"
    div.textContent = text
    return div
}

// What a variant is called in the list: its ref and name, and the ends of the line under
// it, which is what tells two directions of the same number apart at a glance.
const makeRouteRow = (route) => {
    const row = document.createElement("div")
    row.className = "master-route"

    const heading = document.createElement("div")
    heading.className = "master-route-name"
    // names come from OSM, so they are set as text and never as markup
    heading.textContent = describeRoute(route)
    row.appendChild(heading)

    const from = route.tags?.from?.trim()
    const to = route.tags?.to?.trim()

    if (from || to) {
        const ends = document.createElement("div")
        ends.className = "master-route-ends"
        ends.textContent = `${from || "?"} → ${to || "?"}`
        row.appendChild(ends)
    }

    const actions = document.createElement("div")
    actions.className = "master-route-actions"

    if (route.editable) {
        const edit = document.createElement("button")
        edit.type = "button"
        edit.className = "btn btn-sm btn-outline-primary"
        edit.textContent = done.has(route.id) ? "Edit again" : "Edit"
        edit.onclick = () => onEdit(route.id)
        actions.appendChild(edit)
    } else if (!route.described) {
        // Nothing is known about it either way. Saying it is not a route this application
        // can open would be saying something OSM never said: the relation may have been
        // deleted since the master last mentioned it, or the lookup may have failed, which
        // reloading puts right.
        actions.appendChild(
            makeNote(
                "OSM did not say what this member is. It may have been deleted; otherwise reload to look again.",
            ),
        )
    } else {
        actions.appendChild(
            makeNote(
                "Not a PTv2 route this application can open, so it is left alone here.",
            ),
        )
    }

    const link = document.createElement("a")
    link.href = `${osmUrl}/relation/${route.id}`
    link.target = "_blank"
    link.className = "master-route-id"
    link.textContent = `#${route.id}`
    actions.appendChild(link)

    row.appendChild(actions)

    if (done.has(route.id)) {
        row.classList.add("master-route-done")
        row.appendChild(makeNote("Uploaded in this session."))
    }

    return row
}

export function renderMasterPicker() {
    if (view === null) return

    for (const element of idElements) element.textContent = `${view.id}`
    for (const element of urlElements)
        element.href = `${osmUrl}/relation/${view.id}`

    const name = view.tags?.name?.trim() || view.tags?.ref?.trim()
    const heading = document.createElement("div")
    heading.className = "master-name"
    heading.textContent = name || "Unnamed route master"

    const children = [heading]
    const count = view.routes.length

    children.push(makeNote(count === 1 ? "1 variant." : `${count} variants.`))

    if (view.otherMembers.length)
        children.push(
            makeNote(
                `It also holds ${view.otherMembers.join(", ")}, which a route master should not.`,
            ),
        )

    editTagsBtn.type = "button"
    editTagsBtn.className = "btn btn-link btn-sm p-0"
    editTagsBtn.textContent = editing ? "Stop editing tags" : "Edit its tags"
    editTagsBtn.onclick = () => {
        setEditing(!editing)
        renderMasterPicker()
    }
    children.push(editTagsBtn)

    summary.replaceChildren(...children)
    renderIssues()
    list.replaceChildren(...view.routes.map(makeRouteRow))
}

// Everything the variants say about themselves, read side by side. A master with one
// variant carrying a different operator is the thing this list exists to surface.
const renderIssues = () => {
    const issues = routeMasterIssues(view.tags, view.routes)

    issueList.replaceChildren(
        ...issues.map((issue) => {
            const div = document.createElement("div")
            div.className = "route-master-note warning-LOW"
            div.textContent = issue.message
            return div
        }),
    )
}
