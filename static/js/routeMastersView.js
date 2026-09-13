// Shows which route master the loaded route belongs to, what else is in it, and what is
// about to change: the master it will join or be created in, and the ones it will leave.

import {
    clearPendingRouteMaster,
    createRouteMaster,
    currentRouteMasters,
    describeMaster,
    describeRoute,
    detachRouteMaster,
    editRouteMasterTags,
    isDetaching,
    linkRouteMaster,
    mismatchesOf,
    pendingRouteMaster,
    routeMasterCandidates,
    routeMastersKnown,
    setPendingRouteMasterTags,
    setRouteMasters,
    undescribedMemberCount,
    undetachRouteMaster,
} from "./routeMasters.js"
import { createTagEditor } from "./tagEditor.js"
import { osmUrl } from "./utils.js"

const container = document.getElementById("edit-route-master")
const tagsWrap = document.getElementById("edit-route-master-tags-wrap")

// what the route looked like when it was downloaded, for seeding a new master
let routeTags = {}
let relationId = null
let isCreating = false
// set by menu.js, which owns the button that goes on to the summary
let onChanged = () => {}

export const setRouteMasterChangeHandler = (handler) => {
    onChanged = handler
}

// A master's tags are edited by an editor of its own, the route's being a separate one.
// Nothing about it is route-specific beyond the keys worth offering first.
const tagEditor = createTagEditor({
    tableBody: document.getElementById("edit-route-master-tags"),
    toggleButton: document.getElementById("edit-route-master-tags-toggle"),
    addButton: document.getElementById("edit-route-master-tags-add"),
    featuredKeys: ["name", "ref", "network", "operator", "colour"],
    // what makes it a master, and what the application reads it back by
    lockedKeys: new Set(["type", "route_master"]),
    wideElement: document.getElementById("menu"),
    onChange: setPendingRouteMasterTags,
})

const changed = () => {
    render()
    onChanged()
}

const relationLink = (id, text) => {
    const a = document.createElement("a")
    a.href = `${osmUrl}/relation/${id}`
    a.target = "_blank"
    // names come from OSM, so they are set as text and never as markup
    a.textContent = text
    return a
}

const makeNote = (text, severity = null) => {
    const div = document.createElement("div")
    div.className = severity
        ? `route-master-note warning-${severity}`
        : "route-master-note"
    div.textContent = text
    return div
}

const makeButton = (text, onclick, { primary = false } = {}) => {
    const button = document.createElement("button")
    button.type = "button"
    button.className = primary
        ? "btn btn-sm btn-outline-primary route-master-action"
        : "btn btn-link btn-sm p-0 route-master-action"
    button.textContent = text
    button.onclick = onclick
    return button
}

const makeActions = (...buttons) => {
    const row = document.createElement("div")
    row.className = "route-master-actions"
    row.append(...buttons.filter(Boolean))
    return row
}

// The variants a master holds, with the loaded route marked so its place among them is
// plain. A master whose members could not be looked up lists nothing rather than guess.
const makeRouteList = (master) => {
    const list = document.createElement("ul")
    list.className = "route-master-routes"

    for (const route of master.routes) {
        const item = document.createElement("li")

        if (route.id === relationId) {
            item.className = "route-master-route-current"
            item.textContent = `${describeRoute(route)} (this route)`
        } else {
            item.appendChild(relationLink(route.id, describeRoute(route)))
        }

        list.appendChild(item)
    }

    return list
}

const makeMasterBlock = (master) => {
    const block = document.createElement("div")
    block.className = "route-master-block"

    const heading = document.createElement("div")
    heading.className = "route-master-heading"
    heading.appendChild(relationLink(master.id, describeMaster(master)))

    const id = document.createElement("span")
    id.className = "route-master-id"
    id.textContent = ` #${master.id}`
    heading.appendChild(id)

    block.appendChild(heading)

    if (master.routes.length) block.appendChild(makeRouteList(master))

    const plural = (count) => (count !== 1 ? "s" : "")
    const undescribed = undescribedMemberCount(master)

    if (undescribed > 0)
        block.appendChild(
            makeNote(
                master.routes.length
                    ? `${undescribed} more member${plural(undescribed)} not listed.`
                    : `Holds ${master.members.length} member${plural(master.members.length)}.`,
            ),
        )

    for (const mismatch of mismatchesOf(master, routeTags))
        block.appendChild(makeNote(mismatch, "LOW"))

    if (isDetaching(master.id)) {
        block.appendChild(
            makeNote("This route will be removed from it.", "LOW"),
        )
        block.appendChild(
            makeActions(
                makeButton("Keep it here", () => {
                    undetachRouteMaster(master.id)
                    changed()
                }),
            ),
        )
        return block
    }

    const pending = pendingRouteMaster()
    const editing = pending?.id === master.id && pending.tagsOriginal !== null

    block.appendChild(
        makeActions(
            makeButton(editing ? "Stop editing tags" : "Edit tags", () => {
                if (editing) clearPendingRouteMaster()
                else editRouteMasterTags(master)
                changed()
            }),
            makeButton("Remove this route", () => {
                detachRouteMaster(master.id)
                changed()
            }),
        ),
    )

    return block
}

// What is about to happen, when it is not simply "nothing": the master this route will
// join, or the one it will be created in.
const makePendingBlock = () => {
    const pending = pendingRouteMaster()
    if (pending === null) return null

    const block = document.createElement("div")
    block.className = "route-master-block route-master-pending"

    if (pending.id === null) {
        block.appendChild(
            makeNote("A route master will be created for this route.", "LOW"),
        )
    } else if (
        currentRouteMasters().some((master) => master.id === pending.id)
    ) {
        // already a member: only its tags are being changed
        block.appendChild(
            makeNote(`Editing the tags of route master #${pending.id}.`, "LOW"),
        )
    } else {
        const found = routeMasterCandidates().find(
            (master) => master.id === pending.id,
        )
        const name = found ? describeMaster(found) : `#${pending.id}`
        block.appendChild(
            makeNote(
                pending.automatic
                    ? `This route will be added to ${name}, which its ref and network match.`
                    : `This route will be added to ${name}.`,
                "LOW",
            ),
        )
    }

    block.appendChild(
        makeActions(
            makeButton("Undo", () => {
                clearPendingRouteMaster()
                changed()
            }),
        ),
    )

    return block
}

// The masters this route could join, when it is in none. More than one and the mapper
// picks; exactly one that matches outright has already been picked for them.
const makeCandidateChooser = () => {
    const candidates = routeMasterCandidates()
    const block = document.createElement("div")
    block.className = "route-master-block"

    let select = null

    if (candidates.length) {
        select = document.createElement("select")
        select.className = "form-select form-select-sm"

        for (const master of candidates) {
            const option = document.createElement("option")
            option.value = `${master.id}`
            option.textContent = `${describeMaster(master)} (#${master.id})`
            select.appendChild(option)
        }

        block.appendChild(select)
    }

    block.appendChild(
        makeActions(
            select &&
                makeButton(
                    "Add to it",
                    () => {
                        const found = candidates.find(
                            (master) => `${master.id}` === select.value,
                        )
                        if (found) linkRouteMaster(found)
                        changed()
                    },
                    { primary: true },
                ),
            makeButton("Create one", () => {
                createRouteMaster(routeTags)
                changed()
            }),
        ),
    )

    return block
}

// The tag table belongs to whatever master is being created or edited, and to nothing
// otherwise; loading it afresh each time keeps it from showing another master's tags.
let editingKey = null

const syncTagEditor = () => {
    const pending = pendingRouteMaster()
    const editable =
        pending !== null &&
        (pending.id === null || pending.tagsOriginal !== null)
    const key = editable ? `${pending.id}` : null

    tagsWrap.classList.toggle("d-none", !editable)

    if (key === editingKey) return
    editingKey = key

    if (editable) tagEditor.load(pending.tags)
    else tagEditor.unload()
}

const render = () => {
    const heading = document.createElement("div")
    heading.className = "route-master-title"
    heading.textContent = "Route master"

    const children = [heading]
    const masters = currentRouteMasters()

    if (!routeMastersKnown()) {
        children.push(
            makeNote(
                "Could not check whether this route is in a route master. Reload the relation to try again.",
                "LOW",
            ),
        )
    } else {
        if (masters.length > 1)
            children.push(
                makeNote(
                    `This route is in ${masters.length} route masters. It should be in only one.`,
                    "LOW",
                ),
            )

        for (const master of masters) children.push(makeMasterBlock(master))

        // every master it is in is being left, so it is on its way to having none
        const leavingAll =
            masters.length > 0 &&
            masters.every((master) => isDetaching(master.id))

        if (masters.length === 0 || leavingAll) {
            if (pendingRouteMaster() === null) {
                children.push(
                    makeNote(
                        isCreating
                            ? "A route being created is not in one yet."
                            : "Not in a route master.",
                        isCreating ? null : "LOW",
                    ),
                )
                children.push(makeCandidateChooser())
            }
        }

        const queued = makePendingBlock()
        if (queued) children.push(queued)
    }

    container.replaceChildren(...children)
    container.classList.remove("d-none")

    syncTagEditor()
}

export const processRouteMasters = (data, options = {}) => {
    setRouteMasters(data)

    routeTags = data?.tags ?? {}
    relationId = options.relationId ?? null
    isCreating = options.isCreating ?? false

    if (data === null) {
        container.replaceChildren()
        container.classList.add("d-none")
        syncTagEditor()
        return
    }

    render()
}
