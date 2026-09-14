// The tag editor for the route relation being edited. It owns the one instance the rest
// of the application reads its tags from; createTagEditor() itself knows nothing about
// routes, so anything else edited alongside can have an editor of its own.

import { createTagEditor } from "./tagEditor.js"

// tags exactly as loaded from OSM - the baseline the edits are diffed against server-side
export let relationTagsOriginal = null
// working copy - what gets submitted and what the route calculation reads
export let relationTags = null

// always offered, in this order; an empty field means the tag is absent
const FEATURED_KEYS = [
    "name",
    "ref",
    "from",
    "via",
    "to",
    "network",
    "operator",
    "colour",
    "roundtrip",
]
// interpreted server-side when loading the relation, so they are shown but not editable
const LOCKED_KEYS = new Set([
    "type",
    "route",
    "disused:route",
    "was:route",
    "public_transport:version",
])
// changing these changes the calculated route, not just the tags that get uploaded
const RECALC_KEYS = new Set(["roundtrip"])
// roundtrip is effectively an enum; a free-text field invites typos that change routing
const ENUM_KEYS = { roundtrip: ["", "yes", "no"] }

// set by menu.js - avoids importing the route module and closing an import cycle
let onRecalcNeeded = () => {}

export const setRecalcHandler = (handler) => {
    onRecalcNeeded = handler
}

// Every edit, not just the ones that change the calculated route: a ref typed here is
// what the route's siblings are found by, and nothing else would go looking again.
let onTagsChanged = () => {}

export const setTagsChangedHandler = (handler) => {
    onTagsChanged = handler
}

const editor = createTagEditor({
    tableBody: document.getElementById("edit-tags"),
    toggleButton: document.getElementById("edit-tags-toggle"),
    addButton: document.getElementById("edit-tags-add"),
    featuredKeys: FEATURED_KEYS,
    lockedKeys: LOCKED_KEYS,
    recalcKeys: RECALC_KEYS,
    enumKeys: ENUM_KEYS,
    wideElement: document.getElementById("menu"),
    onChange: (tags) => {
        relationTags = tags
        if (tags !== null) onTagsChanged(tags)
    },
    // read through a wrapper rather than passed by reference, so the handler menu.js
    // registers later is the one that gets called
    onRecalcNeeded: () => onRecalcNeeded(),
})

export const processRelationTags = (data) => {
    editor.load(data.tags)
    relationTagsOriginal = editor.tagsOriginal
}

// Sets one of the route's tags from outside the table — the ref, when the mapper has
// corrected it on the route master being created for it instead.
export const setRelationTag = (key, value) => editor.setTag(key, value)

export const unloadRelationTags = () => {
    editor.unload()
    relationTagsOriginal = null
}
