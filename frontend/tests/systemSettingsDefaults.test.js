import assert from 'node:assert/strict'
import test from 'node:test'

import { createSystemSettingsDefaults } from '../src/systemSettingsDefaults.js'
import { applyMarkerTypeDefault, createTaskMarkerSettings } from '../src/markerSettingsScope.js'

test('automatic PDF fitting is disabled by default', () => {
  assert.equal(createSystemSettingsDefaults().autoFitPdf, false)
})

test('restoring defaults retains the original keyboard marker creation mode', () => {
  assert.equal(createSystemSettingsDefaults().manualAddInteraction, 'keyboard')
})

test('workspace side regions use the standard width by default', () => {
  const defaults = createSystemSettingsDefaults()
  assert.equal(defaults.leftDrawerSize, 'standard')
  assert.equal(defaults.reviewDrawerSize, 'standard')
})

test('operation messages default to visible at the bottom', () => {
  const defaults = createSystemSettingsDefaults()
  assert.equal(defaults.operationMessagesEnabled, true)
  assert.equal(defaults.messageLocation, 'bottom')
})

test('all optional header controls are visible by default', () => {
  assert.deepEqual(createSystemSettingsDefaults().headerButtons, {
    analysisQueue: true,
    mineruStatus: true,
    projectSwitch: true,
    assistant: true,
    tutorial: true,
    shortcuts: true,
    userMenu: true,
  })
})

test('system defaults include the other marker appearance', () => {
  const appearance = createSystemSettingsDefaults().markerAppearance.special
  assert.deepEqual(appearance, {
    shape: 'split-circle', frameSize: 28, fontSize: 11,
    lineWidth: 1.35, color: '#c45d3c', fillOpacity: 0,
  })
})

test('saved font-only settings regain category frame sizes while preserving existing frames', () => {
  const settings = createTaskMarkerSettings({ appearance: {
    weld: { fontSize: 19 }, components: { valve: { fontSize: 10 }, flange: { frameSize: 42 } },
    special: { fontSize: 9 },
  } })
  assert.equal(settings.appearance.weld.frameSize, 28)
  assert.equal(settings.appearance.weld.fontSize, 19)
  assert.equal(settings.appearance.components.valve.frameSize, 20)
  assert.equal(settings.appearance.components.flange.frameSize, 42)
  assert.equal(settings.appearance.special.frameSize, 28)
})

test('applying the other marker default only updates its appearance', () => {
  const task = createTaskMarkerSettings({ appearance: { special: { shape: 'circle', color: '#111111' } } })
  const defaults = createTaskMarkerSettings({ appearance: { special: { shape: 'split-circle', color: '#22aa88' } } })
  const next = applyMarkerTypeDefault(task, defaults, 'other')

  assert.equal(next.appearance.special.shape, 'split-circle')
  assert.equal(next.appearance.special.color, '#22aa88')
  assert.deepEqual(next.appearance.weld, task.appearance.weld)
})
