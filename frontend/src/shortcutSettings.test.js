import assert from 'node:assert/strict'
import test from 'node:test'

import { matchesShortcut, normalizeShortcut, shortcutFromEvent, SHORTCUT_DEFAULTS, SHORTCUT_SETTING_ROWS, useShortcutSettings } from './composables/useShortcutSettings.js'

test('shortcut normalization keeps modifiers ordered and keys canonical', () => {
  assert.equal(normalizeShortcut('shift+ctrl+s'), 'Ctrl+Shift+S')
  assert.equal(normalizeShortcut('alt+x'), 'Alt+X')
})

test('keyboard events match configured shortcuts independent of modifier order', () => {
  const event = { key: 's', ctrlKey: true, altKey: false, shiftKey: true, metaKey: false }
  assert.equal(shortcutFromEvent(event), 'Ctrl+Shift+S')
  assert.equal(matchesShortcut(event, 'Shift+Ctrl+s'), true)
  assert.equal(matchesShortcut(event, 'Ctrl+S'), false)
})

test('independent reference window has a configurable default shortcut', () => {
  assert.equal(SHORTCUT_DEFAULTS.openReferenceWindow, 'Ctrl+M')
  assert.equal(SHORTCUT_SETTING_ROWS.some(row => row.action === 'openReferenceWindow'), true)
})

test('all-marker modification mode has a configurable M shortcut', () => {
  assert.equal(SHORTCUT_DEFAULTS.modifyAll, 'M')
  assert.equal(SHORTCUT_SETTING_ROWS.some(row => row.action === 'modifyAll'), true)
})

test('stored shortcuts are preserved and missing settings use current defaults', () => {
  const custom = useShortcutSettings({ storage: { getItem: () => JSON.stringify({ openReferenceWindow: 'Ctrl+Alt+K' }), setItem: () => undefined } })
  assert.equal(custom.settings.value.openReferenceWindow, 'Ctrl+Alt+K')
  assert.equal(custom.settings.value.deleteWeld, 'Backspace')
})
