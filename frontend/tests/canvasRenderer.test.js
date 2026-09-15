import assert from 'node:assert/strict'
import test from 'node:test'

import { calculateCanvasPageLayout } from '../src/composables/useCanvasRenderer.js'


test('canvas layout includes the reference PDF only in embedded mode', () => {
  const target = { width: 100, height: 200 }
  const reference = { width: 80, height: 150 }
  assert.deepEqual(calculateCanvasPageLayout(target, reference, true), {
    width: 208,
    height: 234,
    target: { x: 0, y: 34, width: 100, height: 200 },
    reference: { x: 128, y: 34, width: 80, height: 150 },
  })
  assert.deepEqual(calculateCanvasPageLayout(target, reference, false), {
    width: 100,
    height: 234,
    target: { x: 0, y: 34, width: 100, height: 200 },
    reference: null,
  })
})
