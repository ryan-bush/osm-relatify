// Run with: node --test "tests/js/*.mjs"
import assert from "node:assert/strict"
import { test } from "node:test"

import { corridorCells } from "../../static/js/areaCells.js"

const GRID = 0.01
const keys = (cells) => new Set(cells.map(({ x, y }) => `${x},${y}`))

test("no points, no cells", () => {
    assert.deepEqual(corridorCells([], GRID), [])
})

test("a single point takes the cells within the width of it", () => {
    // the middle of a cell, which is about 1100 by 700 m at 52 degrees north
    const cells = keys(corridorCells([[52.005, 0.005]], GRID, 300))

    assert.deepEqual(cells, new Set(["0,5200"]))
})

test("a point by a cell edge takes the cell over the edge too", () => {
    // about 70 m west of the line between cells 0 and 1
    const cells = keys(corridorCells([[52.005, 0.009]], GRID, 300))

    assert.deepEqual(cells, new Set(["0,5200", "1,5200"]))
})

test("a straight line east takes a strip of cells, not the area around it", () => {
    // 10 km along the middle of one row of cells
    const cells = corridorCells(
        [
            [52.005, 0.0],
            [52.005, 0.1],
        ],
        GRID,
        300,
    )

    assert.ok(cells.every(({ y }) => y === 5200))
    assert.deepEqual(keys(cells), new Set(Array.from({ length: 12 }, (_, i) => `${i - 1},5200`)))
})

test("a diagonal line stays narrow", () => {
    // 50 km south-west to north-east: a corridor, far short of the bounding box of it
    const line = [
        [52.0, 0.0],
        [52.3, 0.5],
    ]
    const cells = corridorCells(line, GRID)
    const box = 31 * 51

    assert.ok(cells.length < box / 5, `${cells.length} cells`)
})

test("no cell is listed twice", () => {
    const cells = corridorCells(
        [
            [52.0, 0.0],
            [52.05, 0.05],
            [52.0, 0.1],
        ],
        GRID,
    )

    assert.equal(keys(cells).size, cells.length)
})
