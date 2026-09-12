// What a stop will be called once the changeset is uploaded.
//
// Not always what it is called now: a NaPTAN rename the mapper has accepted in this
// session is about to replace it. Anything made from the stop — the stop position on the
// road, the stop area grouping it — has to use the name the stop is going to have, or it
// goes into OSM holding a name that the very same changeset is changing.
export function effectiveName(stop, naptanName, decision) {
    if (naptanName && decision === "naptan") return naptanName

    return stop?.tags?.name?.trim() ?? ""
}
