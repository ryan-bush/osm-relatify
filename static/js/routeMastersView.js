// Shows which route master the loaded route belongs to, and what else is in it.

import {
    currentRouteMasters,
    describeMaster,
    describeRoute,
    mismatchesOf,
    routeMastersKnown,
    setRouteMasters,
    undescribedMemberCount,
} from "./routeMasters.js"
import { osmUrl } from "./utils.js"

const container = document.getElementById("edit-route-master")

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

// The variants a master holds, with the loaded route marked so its place among them is
// plain. A master whose members could not be looked up lists nothing rather than guess.
const makeRouteList = (master, relationId) => {
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

const makeMasterBlock = (master, tags, relationId) => {
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

    if (master.routes.length)
        block.appendChild(makeRouteList(master, relationId))

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

    for (const mismatch of mismatchesOf(master, tags))
        block.appendChild(makeNote(mismatch, "LOW"))

    return block
}

const render = (tags, relationId, isCreating) => {
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
    } else if (isCreating) {
        children.push(makeNote("A route being created is not in one yet."))
    } else if (masters.length === 0) {
        children.push(makeNote("Not in a route master.", "LOW"))
    } else {
        if (masters.length > 1)
            children.push(
                makeNote(
                    `This route is in ${masters.length} route masters. It should be in only one.`,
                    "LOW",
                ),
            )

        for (const master of masters)
            children.push(makeMasterBlock(master, tags, relationId))
    }

    container.replaceChildren(...children)
    container.classList.remove("d-none")
}

export const processRouteMasters = (
    data,
    { relationId = null, isCreating = false } = {},
) => {
    setRouteMasters(data)

    if (data === null) {
        container.replaceChildren()
        container.classList.add("d-none")
        return
    }

    render(data.tags ?? {}, relationId, isCreating)
}
