import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SHORTCUT_DEFAULTS,
  SHORTCUT_SETTING_ROWS,
  matchesShortcut,
  useShortcutSettings,
} from '../src/composables/useShortcutSettings.js'

test('shortcut settings retain clear operation groups', () => {
  assert.deepEqual([...new Set(SHORTCUT_SETTING_ROWS.map(row => row.group))], ['常用操作', '标识编辑', '页面与画布'])
})

test('system settings expose every hard-coded canvas navigation shortcut', () => {
  for (const action of ['toggleHelp', 'addAtPointer', 'copyMarker', 'pasteMarker', 'fitCanvas', 'previousPage', 'nextPage']) {
    assert.ok(SHORTCUT_SETTING_ROWS.some(row => row.action === action), `${action} must be configurable`)
    assert.ok(SHORTCUT_DEFAULTS[action], `${action} must have a default`)
  }
})

test('arrow defaults match their keyboard events', () => {
  assert.equal(matchesShortcut({ key: 'ArrowLeft', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false }, SHORTCUT_DEFAULTS.previousPage), true)
})

test('the shift-following shortcut always includes Ctrl', () => {
  assert.equal(SHORTCUT_DEFAULTS.shiftFollowing, 'Ctrl+F')
  const storage = {
    getItem: () => JSON.stringify({ shiftFollowing: 'Shift+G' }),
    setItem: () => {},
  }
  const { settings, editors, updateModifier } = useShortcutSettings({ storage })
  assert.equal(settings.value.shiftFollowing, 'Ctrl+Shift+G')

  updateModifier('shiftFollowing', 'Alt')
  assert.equal(settings.value.shiftFollowing, 'Ctrl+Alt+G')
  assert.equal(editors.value.shiftFollowing.modifier, 'Ctrl+Alt')
})

test('fixed mouse gestures and keyboard zoom stay out of editable shortcuts', () => {
  for (const action of ['wheelPreviousPage', 'wheelNextPage', 'wheelZoom', 'panCanvas', 'zoomIn', 'zoomOut']) {
    assert.equal(SHORTCUT_SETTING_ROWS.some(row => row.action === action), false)
    assert.equal(Object.hasOwn(SHORTCUT_DEFAULTS, action), false)
  }
})

test('removed mouse and keyboard zoom bindings are discarded from stored settings', () => {
  const storage = {
    getItem: () => JSON.stringify({ zoomIn: 'Plus', wheelZoom: 'Alt+Wheel', save: 'Alt+S' }),
    setItem: () => {},
  }
  const { settings } = useShortcutSettings({ storage })
  assert.equal(settings.value.save, 'Alt+S')
  assert.equal(Object.hasOwn(settings.value, 'zoomIn'), false)
  assert.equal(Object.hasOwn(settings.value, 'wheelZoom'), false)
})
