// What a stop will be called once the changeset is uploaded.
//
// Not always what it is called now: a NaPTAN rename the mapper has accepted in this
// session is about to replace it. Anything made from the stop — the stop position on the
// road, the stop area grouping it — has to use the name the stop is going to have, or it
// goes into OSM holding a name that the very same changeset is changing.
// `edited` is what the mapper typed into the stop form, which outranks both: they were
// looking at the stop when they typed it.
export function effectiveName(stop, naptanName, decision, edited = undefined) {
    if (edited !== undefined) return edited.trim()

    if (naptanName && decision === "naptan") return naptanName

    return stop?.tags?.name?.trim() ?? ""
}

// What makes two stops' names the same place, whatever the punctuation and case. Mirrors
// normalize_name(lower, special, whitespace) in utils.py: "Carreg-Bran" is "Carreg Bran".
export const placeKey = (name) =>
    (name ?? "")
        .toLowerCase()
        .replace(/[-\u2010-\u2015/]/gu, " ")
        .replace(/[^\p{L}\p{N}\p{M}_\s]/gu, "")
        .replace(/\s+/gu, " ")
        .trim()
