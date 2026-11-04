// The download cells along a line drawn on the map, for a route that runs a long way
// between towns: a coach on the motorway needs the motorway, not the 17 by 11 km of
// everything either side that "download this view" would take with it.

const EARTH_RADIUS = 6_371_000 // metres

// how far either side of the drawn line is downloaded: room for the slip roads, and for
// a line clicked a little off the road it follows
export const CORRIDOR_WIDTH = 500 // metres

// how often along the line the cells are looked for; well under the width, so a cell the
// corridor only clips between two samples is still found
const SAMPLE_STEP = 100 // metres

const toRadians = (degrees) => (degrees * Math.PI) / 180

// every cell within `width` metres of the line through `latLngs`, as {x, y}
export function corridorCells(latLngs, gridSize, width = CORRIDOR_WIDTH) {
    const cells = new Map()

    const addAround = ([lat, lng]) => {
        // metres per degree here; the corridor is narrow enough for the earth to be flat
        const mLat = toRadians(EARTH_RADIUS)
        const mLng = mLat * Math.cos(toRadians(lat))

        const minX = Math.floor((lng - width / mLng) / gridSize)
        const maxX = Math.floor((lng + width / mLng) / gridSize)
        const minY = Math.floor((lat - width / mLat) / gridSize)
        const maxY = Math.floor((lat + width / mLat) / gridSize)

        for (let x = minX; x <= maxX; x++) {
            for (let y = minY; y <= maxY; y++) {
                // the nearest point of the cell to this one
                const nearLng = Math.min(Math.max(lng, x * gridSize), (x + 1) * gridSize)
                const nearLat = Math.min(Math.max(lat, y * gridSize), (y + 1) * gridSize)
                const dx = (nearLng - lng) * mLng
                const dy = (nearLat - lat) * mLat

                if (dx * dx + dy * dy <= width * width) cells.set(`${x},${y}`, { x, y })
            }
        }
    }

    if (latLngs.length > 0) addAround(latLngs[0])

    for (let i = 1; i < latLngs.length; i++) {
        const [lat1, lng1] = latLngs[i - 1]
        const [lat2, lng2] = latLngs[i]

        const mLat = toRadians(EARTH_RADIUS)
        const mLng = mLat * Math.cos(toRadians((lat1 + lat2) / 2))
        const length = Math.hypot((lat2 - lat1) * mLat, (lng2 - lng1) * mLng)
        const steps = Math.max(1, Math.ceil(length / SAMPLE_STEP))

        for (let s = 1; s <= steps; s++) {
            const t = s / steps
            addAround([lat1 + (lat2 - lat1) * t, lng1 + (lng2 - lng1) * t])
        }
    }

    return [...cells.values()]
}
