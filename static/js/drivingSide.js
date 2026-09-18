// Which side of the road traffic keeps to where the route runs. It decides which way
// round the route goes: a bus serves a stop from its own kerb, so a loop is driven the
// way that keeps the stops on that side. The server finds it from the country; the
// mapper can say otherwise, which lasts for as long as the route is open.
import { requestCalcBusRoute } from "./waysRoute.js"

// what the download said, or null when it could not tell
let found = null
// what the mapper picked, or null to go with what was found
let chosen = null

const select = document.getElementById("edit-driving-side")

// with nothing to go on, as it was before this could be told at all
export const drivingSide = () => chosen ?? found ?? "right"

export function processDrivingSide(fetchData) {
    if (!fetchData) {
        found = null
        chosen = null
    } else {
        // a further download is the same route, so the mapper's choice stands
        if (!fetchData.fetchMerge) chosen = null
        found = fetchData.drivingSide ?? found
    }

    render()
}

function render() {
    if (!select) return

    const auto = found ? `Auto: ${found} (from the country)` : "Auto: unknown, assuming right"

    select.innerHTML = ""
    for (const [value, label] of [
        ["", auto],
        ["left", "Left"],
        ["right", "Right"],
    ]) {
        const option = document.createElement("option")
        option.value = value
        option.textContent = label
        option.selected = (chosen ?? "") === value
        select.appendChild(option)
    }
}

select?.addEventListener("change", () => {
    chosen = select.value || null
    requestCalcBusRoute()
})
