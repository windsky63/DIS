import assert from 'node:assert/strict'
import test from 'node:test'

import { reflowLabelPositions } from '../src/markerLayout.js'


test('marker layout places included labels inside the page', () => {
  const page = {
    width: 200,
    height: 120,
    candidates: [{ id: 'a', x: 50, y: 50, number: 'F1', included: true }],
    layoutObstacles: { textRects: [], processSegments: [] },
  }
  const totals = reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 28, fontSize: 15 }))
  assert.equal(totals.placed, 1)
  assert.ok(page.candidates[0].labelX > 0 && page.candidates[0].labelX < page.width)
  assert.ok(page.candidates[0].labelY > 0 && page.candidates[0].labelY < page.height)
})

test('manual reflow changes only the current page', async () => {
  const layout = await import('../src/markerLayout.js')
  assert.equal(typeof layout.reflowCurrentPageLabelPositions, 'function')
  const current = {
    page: 1, width: 300, height: 200,
    candidates: [{ id: 'current', number: '1', x: 100, y: 100, labelX: 250, labelY: 180, included: true }],
    layoutObstacles: { textRects: [], processSegments: [] },
  }
  const untouched = {
    page: 2, width: 300, height: 200,
    candidates: [{ id: 'other', number: '2', x: 100, y: 100, labelX: 250, labelY: 180, included: true }],
    layoutObstacles: { textRects: [], processSegments: [] },
  }
  layout.reflowCurrentPageLabelPositions(current, () => ({ shape: 'circle', frameSize: 28, fontSize: 15 }))
  assert.notEqual(current.candidates[0].labelX, 250)
  assert.equal(untouched.candidates[0].labelX, 250)
})

test('dense layout exposes repair diagnostics and caps emergency leaders', () => {
  const page = {
    width: 640,
    height: 420,
    candidates: Array.from({ length: 9 }, (_, index) => ({
      id: `dense-${index}`,
      number: String(index + 1),
      x: 300 + (index % 3) * 9,
      y: 195 + Math.floor(index / 3) * 10,
      included: true,
    })),
    layoutObstacles: {
      textRects: [[245, 150, 375, 180], [245, 230, 375, 260]],
      processSegments: [
        { start: [180, 200], end: [460, 200] },
        { start: [180, 220], end: [460, 220] },
      ],
    },
  }
  const totals = reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 20, fontSize: 12 }))

  assert.equal(totals.placed, 9)
  assert.equal(totals.remainingCollisions, 0)
  assert.ok(totals.repairPasses >= 0 && totals.repairPasses <= 3)
  page.candidates.forEach(item => {
    assert.ok(Math.hypot(item.labelX - item.x, item.labelY - item.y) <= 20 * 18 + 1e-6)
  })
})

test('long straight pipe uses ordered near-perpendicular group lanes', () => {
  const page = {
    width: 620,
    height: 420,
    candidates: [150, 220, 290, 360, 430].map((x, index) => ({ id: `pipe-${index}`, number: String(index + 1), x, y: 210, included: true })),
    layoutObstacles: { textRects: [], processSegments: [{ start: [80, 210], end: [520, 210], width: 2 }] },
  }
  reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 20, fontSize: 12 }))

  const offsets = page.candidates.map(item => item.labelY - item.y)
  assert.ok(offsets.every(value => value > 0) || offsets.every(value => value < 0))
  assert.ok(page.candidates.every(item => Math.abs(item.labelX - item.x) <= 32))
  assert.ok(page.candidates.every(item => item.layoutDiagnostics.source.startsWith('long-pipe-group')))
  const lanes = page.candidates.map(item => item.layoutDiagnostics.longPipeLane)
  assert.deepEqual(lanes, [...lanes].sort((a, b) => a - b))
})

test('full port emits reference diagnostics and stays deterministic', () => {
  const template = {
    width: 500,
    height: 400,
    candidates: [{ id: 'diagnostic', number: '1', x: 220, y: 200, included: true }],
    layoutObstacles: { textRects: [[195, 130, 245, 175]], processSegments: [{ start: [80, 200], end: [420, 200], width: 2 }] },
  }
  const first = structuredClone(template)
  const second = structuredClone(template)
  const style = () => ({ shape: 'circle', frameSize: 20, fontSize: 12 })
  reflowLabelPositions([first], style)
  reflowLabelPositions([second], style)

  assert.deepEqual(first.candidates, second.candidates)
  const diagnostics = first.candidates[0].layoutDiagnostics
  assert.ok(['long-pipe-group', 'local-pipe-band-compact', 'local-pipe-band', 'local-pipe-band-extended', 'perimeter-distribution', 'strict-perpendicular', 'relaxed-direction', 'non-crossing-emergency', 'fallback-best-effort'].includes(diagnostics.selectionStage))
  assert.equal(diagnostics.hardCollisionCount, 0)
  assert.ok(diagnostics.lineLength > 0)
  assert.ok(diagnostics.hardCollisionSummary)
})

test('strict perpendicular stage selects the shortest reference length', () => {
  const page = { width: 500, height: 400, candidates: [{ id: 'normal', number: '1', x: 250, y: 200, included: true }], layoutObstacles: { textRects: [], processSegments: [{ start: [80, 200], end: [420, 200], width: 2 }] } }
  reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 20, fontSize: 12 }))
  const item = page.candidates[0]
  assert.equal(item.layoutDiagnostics.selectionStage, 'strict-perpendicular')
  assert.ok(Math.abs(item.layoutDiagnostics.lineLength - (Math.hypot(10, 10) + 12)) < .01)
  assert.ok(Math.abs(item.labelX - item.x) < .01)
})

test('perimeter mode tries local pipe bands before radial spokes', () => {
  const anchors = [[100, 100], [300, 100], [500, 100], [100, 300], [300, 300], [500, 300], [200, 200], [400, 200]]
  const page = {
    width: 620, height: 420,
    candidates: anchors.map(([x, y], index) => ({ id: `local-${index}`, number: String(index + 1), x, y, included: true })),
    layoutObstacles: { textRects: [], processSegments: anchors.map(([x, y], index) => index % 2 === 0 ? { start: [x - 25, y], end: [x + 25, y], width: 2 } : { start: [x, y - 25], end: [x, y + 25], width: 2 }) },
  }
  reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 20, fontSize: 12 }))
  assert.ok(page.candidates.every(item => item.layoutDiagnostics.selectionStage.startsWith('local-pipe-band')))
})

test('foreign source anchor proximity stays a soft penalty like the reference engine', () => {
  const page = { width: 400, height: 300, candidates: [
    { id: 'near-a', number: '1', x: 200, y: 150, included: true },
    { id: 'near-b', number: '2', x: 200, y: 150, included: true },
  ], layoutObstacles: { textRects: [], processSegments: [] } }
  const totals = reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 20, fontSize: 12 }))
  assert.equal(totals.remainingCollisions, 0)
})

test('all marker frames stay inside the identified main graphic region', () => {
  const page = { width: 900, height: 600, candidates: Array.from({ length: 9 }, (_, index) => ({
    id: `bounded-${index}`, number: String(index + 1), x: 300 + index * 15, y: 250, included: true,
  })), layoutObstacles: { textRects: [], processSegments: [{ start: [220, 250], end: [520, 250] }],
    mainGraphicRegion: { left: 180, top: 120, right: 550, bottom: 430 } } }
  reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 28, fontSize: 15 }))
  for (const item of page.candidates) {
    assert.ok(item.labelX - 14 >= 185 && item.labelX + 14 <= 545)
    assert.ok(item.labelY - 14 >= 125 && item.labelY + 14 <= 425)
    assert.equal('_layoutBounds' in item, false)
  }
})

test('marker frame avoids ordinary vector graphics as hard obstacles', () => {
  const page = { width: 400, height: 300, candidates: [
    { id: 'graphic-safe', number: '1', x: 100, y: 100, included: true },
  ], layoutObstacles: { textRects: [], processSegments: [], graphicSegments: [
    { start: [125, 55], end: [125, 180] },
    { start: [55, 125], end: [180, 125] },
  ] } }
  const totals = reflowLabelPositions([page], () => ({ shape: 'circle', frameSize: 20, fontSize: 12 }))
  const item = page.candidates[0]
  assert.equal(totals.remainingCollisions, 0)
  assert.equal(item.layoutDiagnostics.hardCollisionSummary.labelGraphic, 0)
  assert.equal(item.labelX - 12 <= 125 && item.labelX + 12 >= 125 && item.labelY - 12 <= 180 && item.labelY + 12 >= 55, false)
  assert.equal(item.labelY - 12 <= 125 && item.labelY + 12 >= 125 && item.labelX - 12 <= 180 && item.labelX + 12 >= 55, false)
})
