import assert from 'node:assert/strict'
import test from 'node:test'

import { createPastedMarker, matchesModificationType } from './markerClipboard.js'


test('all modification mode includes all marker types while weld mode excludes special markers', () => {
  const candidates = [
    { componentKind: 'manual-weld' },
    { componentKind: 'design-component', componentType: 'valve' },
    { componentKind: 'design-component', componentType: 'flange' },
    { componentKind: 'design-component', componentType: 'support' },
    { componentKind: 'special-marker', componentType: 'special' },
  ]

  assert.deepEqual(candidates.map(item => matchesModificationType(item, 'all')), [true, true, true, true, true])
  assert.equal(matchesModificationType(candidates[0], 'weld'), true)
  assert.equal(matchesModificationType(candidates[4], 'weld'), false)
  assert.equal(matchesModificationType(candidates[1], 'flange'), false)
  assert.equal(matchesModificationType(candidates[2], 'flange'), true)
})

test('pasted marker keeps its type number and appearance at the requested point', () => {
  const source = {
    id: 'recognized-1', page: 3, number: 'V7', x: 200, y: 150, xNorm: .2, yNorm: .3,
    labelX: 250, labelY: 100, labelXNorm: .25, labelYNorm: .2,
    componentKind: 'design-component', componentType: 'valve', markerStyle: { color: '#123456' },
    referenceLabel: 'V7', referenceMatched: true, included: false,
  }

  const pasted = createPastedMarker(source, {
    id: 'p3-copy-1', page: 3, pageWidth: 1000, pageHeight: 500, xNorm: .9, yNorm: .95,
  })

  assert.equal(pasted.id, 'p3-copy-1')
  assert.equal(pasted.number, 'V7')
  assert.equal(pasted.componentType, 'valve')
  assert.deepEqual(pasted.markerStyle, { color: '#123456' })
  assert.notEqual(pasted.markerStyle, source.markerStyle)
  assert.deepEqual(
    { x: pasted.x, y: pasted.y, labelX: pasted.labelX, labelY: pasted.labelY },
    { x: 900, y: 475, labelX: 950, labelY: 425 },
  )
  assert.equal(pasted.origin, 'manual')
  assert.equal(pasted.included, true)
  assert.equal(pasted.referenceLabel, '')
  assert.equal(pasted.referenceMatched, false)
})

test('pasted marker preserves a label offset that starts at the page edge', () => {
  const pasted = createPastedMarker({ xNorm: .2, yNorm: .2, labelXNorm: 0, labelYNorm: 0 }, {
    id: 'edge-copy', page: 1, pageWidth: 1000, pageHeight: 500, xNorm: .6, yNorm: .7,
  })

  assert.ok(Math.abs(pasted.labelXNorm - .4) < 1e-10)
  assert.ok(Math.abs(pasted.labelYNorm - .5) < 1e-10)
})

test('manual marker placement uses the configured leader length', async () => {
  const placement = await import('./manualMarkerPlacement.js').catch(() => ({}))
  assert.equal(typeof placement.manualLabelPosition, 'function')
  const result = placement.manualLabelPosition({ x: 100, y: 100 }, 1000, 500, 75)
  assert.ok(Math.abs(Math.hypot(result.x - 100, result.y - 100) - 75) < 1e-9)
  assert.ok(result.x > 100)
  assert.ok(result.y < 100)
})

test('manual leader length defaults higher and is clamped to the supported range', async () => {
  const placement = await import('./manualMarkerPlacement.js').catch(() => ({}))
  assert.equal(placement.DEFAULT_MANUAL_LEADER_LENGTH, 72)
  assert.equal(placement.normalizeManualLeaderLength(5), 20)
  assert.equal(placement.normalizeManualLeaderLength(999), 240)
})
