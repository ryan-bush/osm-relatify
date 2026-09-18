// The tag editor's rows, as data. An entry is one row of the table: a key, a value, and
// nothing about how it is drawn, which is what makes this part testable on its own.

// Featured keys are always offered, in the order given, whether or not the relation has
// them; an empty value means the tag is absent. Everything else follows, sorted.
export function buildEntries(tags, featuredKeys) {
    const result = featuredKeys.map((key) => ({ key, value: tags[key] ?? "" }))

    for (const key of Object.keys(tags).sort()) {
        if (!featuredKeys.includes(key)) result.push({ key, value: tags[key] })
    }

    return result
}

// What the rows amount to: the tags that get submitted and that the route calculation
// reads. An emptied-out value is how the editor says "delete this tag", so it drops out.
export function tagsFromEntries(entries) {
    const result = {}

    for (const { key, value } of entries) {
        const trimmedKey = key.trim()
        const trimmedValue = value.trim()
        if (trimmedKey && trimmedValue) result[trimmedKey] = trimmedValue
    }

    return result
}

export const isModified = (entry, original) => {
    const key = entry.key.trim()
    if (!key) return entry.value.trim() !== ""
    return (original[key] ?? "") !== entry.value.trim()
}

// a key typed into two rows at once would silently lose one of them
export function isDuplicate(entry, entries) {
    const key = entry.key.trim()
    if (!key) return false
    return entries.filter((other) => other.key.trim() === key).length > 1
}

export const visibleEntries = (entries, featuredKeys, showAll) =>
    entries.filter((entry) => showAll || featuredKeys.includes(entry.key))
