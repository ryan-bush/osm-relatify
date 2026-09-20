// What a line's variants say about themselves, read side by side. Seeing eight variants
// of one number together is how you notice that one of them has the wrong operator, or
// that the master and the routes in it disagree about the number itself.
//
// Nothing here fixes anything: these are things to look at, in a tool whose whole job is
// that the mapper decides.

// tags a line's variants would normally agree on, the master included
const SHARED_KEYS = ["network", "operator", "colour"]

const value = (tags, key) => tags?.[key]?.trim() ?? ""

// Two operators can run one line between them, and OSM says so by listing both:
// operator=A;B. Read as one string, a master tagged that way disagrees with every variant
// under it, including the ones it is describing - so these tags are read as the lists they
// are, and a variant agrees when everything it states is something the master states too.
const values = (tags, key) =>
    value(tags, key)
        .split(";")
        .map((part) => part.trim())
        .filter(Boolean)

// what the value says, rather than how it was written: A;B and B;A are one answer
const valueKey = (tags, key) => [...new Set(values(tags, key))].sort().join(";")

const label = (route) => `#${route.id}`

const list = (routes) => routes.map(label).join(", ")

const plural = (count, one, many) => (count === 1 ? one : many)

// The route's own kind as tagged, which its master should be a master of.
const routeKind = (tags) => value(tags, tags?.type ?? "")

/**
 * Everything worth saying about a master and the routes in it.
 *
 * Each issue names the variants it is about, so a list of eight can be read without
 * counting rows. Silence is the answer for anything neither side states: a tag the master
 * does not carry is not a tag its variants are wrong about.
 */
export function routeMasterIssues(masterTags, routes) {
    // A member OSM did not describe says nothing about itself, so there is nothing to say
    // about it: counting it as a variant with no ref, and no ends, is inventing a fault
    // out of a lookup that did not happen.
    routes = routes.filter((route) => route.described !== false)

    const issues = []
    const add = (message, routes) =>
        issues.push({ message, routes: routes.map((route) => route.id) })

    const masterRef = value(masterTags, "ref")
    if (masterRef) {
        const wrong = routes.filter((route) => {
            const ref = value(route.tags, "ref")
            return ref && ref !== masterRef
        })

        if (wrong.length)
            add(
                `${list(wrong)} ${plural(wrong.length, "has a ref", "have refs")} other than ${masterRef}.`,
                wrong,
            )
    }

    const missingRef = routes.filter((route) => !value(route.tags, "ref"))
    if (missingRef.length)
        add(
            `${list(missingRef)} ${plural(missingRef.length, "has", "have")} no ref.`,
            missingRef,
        )

    const masterKind = value(masterTags, "route_master")
    if (masterKind) {
        const wrong = routes.filter((route) => {
            const kind = routeKind(route.tags)
            return kind && kind !== masterKind
        })

        if (wrong.length)
            add(
                `${list(wrong)} ${plural(wrong.length, `is not a ${masterKind} route`, `are not ${masterKind} routes`)}.`,
                wrong,
            )
    }

    for (const key of SHARED_KEYS) {
        const master = value(masterTags, key)

        if (master) {
            const stated = new Set(values(masterTags, key))
            const wrong = routes.filter((route) => {
                const own = values(route.tags, key)
                return own.length && !own.every((one) => stated.has(one))
            })

            // phrased around the article, "a operator" being the alternative
            if (wrong.length)
                add(
                    `${list(wrong)} ${plural(wrong.length, "has", "have")} a different ${key} from the master (${master}).`,
                    wrong,
                )

            continue
        }

        // the master says nothing, so the variants are only wrong about each other
        const stated = routes.filter((route) => value(route.tags, key))
        const distinct = new Map(
            stated.map((route) => [
                valueKey(route.tags, key),
                value(route.tags, key),
            ]),
        )

        if (distinct.size > 1)
            add(
                `The variants give ${distinct.size} different values for ${key}: ${[...distinct.values()].join(", ")}.`,
                stated,
            )
    }

    // where a variant starts and ends is how a mapper tells one direction from the other
    const missingEnds = routes.filter(
        (route) => !value(route.tags, "from") || !value(route.tags, "to"),
    )
    if (missingEnds.length)
        add(
            `${list(missingEnds)} ${plural(missingEnds.length, "does not say where it starts or ends", "do not say where they start or end")}.`,
            missingEnds,
        )

    return issues
}
