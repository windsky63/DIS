import assert from 'node:assert/strict'
import test from 'node:test'

import { createDefaultMarkerAppearance, createSpecialMarkerAppearance } from '../src/defaultMarkerAppearance.js'


test('every marker category starts with a transparent fill', () => {
  const defaults = createDefaultMarkerAppearance()
  assert.equal(defaults.weld.fillOpacity, 0)
  assert.deepEqual(Object.values(defaults.components).map(style => style.fillOpacity), [0, 0, 0])
})

test('special marker appearance is an independent object-level default', () => {
  const first = createSpecialMarkerAppearance()
  const second = createSpecialMarkerAppearance()
  first.frameSize = 60
  assert.equal(second.frameSize, 20)
  assert.equal(second.fontSize, 12)
  assert.equal(second.fillOpacity, 0)
})

test('component markers start at frame size 20 and font size 12', () => {
  const defaults = createDefaultMarkerAppearance()
  for (const style of Object.values(defaults.components)) {
    assert.equal(style.frameSize, 20)
    assert.equal(style.fontSize, 12)
  }
})

test('default marker appearance returns independent mutable objects', () => {
  const first = createDefaultMarkerAppearance()
  const second = createDefaultMarkerAppearance()
  first.weld.fillOpacity = 1
  first.components.flange.fillOpacity = 1
  assert.equal(second.weld.fillOpacity, 0)
  assert.equal(second.components.flange.fillOpacity, 0)
})
