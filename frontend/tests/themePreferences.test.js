import assert from 'node:assert/strict'
import test from 'node:test'
import { APP_THEMES, loadThemePreference, resolveThemeName, THEME_STORAGE_KEY } from '../src/themePreferences.js'
import { SHORTCUT_DEFAULTS, useShortcutSettings } from '../src/composables/useShortcutSettings.js'

test('theme preference restores saved choices and safely defaults on unavailable or corrupt storage', () => {
  assert.equal(loadThemePreference({ getItem: key => key === THEME_STORAGE_KEY ? 'dark' : null }), 'dark')
  assert.equal(loadThemePreference({ getItem: () => 'unknown' }), 'light')
  assert.equal(loadThemePreference({ getItem: () => { throw new Error('denied') } }), 'light')
})

test('system theme follows device appearance while explicit choices stay fixed', () => {
  assert.equal(resolveThemeName('system', true), 'weldDark')
  assert.equal(resolveThemeName('system', false), 'weldLight')
  assert.equal(resolveThemeName('light', true), 'weldLight')
  assert.equal(resolveThemeName('dark', false), 'weldDark')
  assert.equal(APP_THEMES.weldDark.dark, true)
  assert.equal(APP_THEMES.weldLight.dark, false)
})

test('existing shortcut preferences receive the new region shortcut and still detect conflicts', () => {
  const shortcuts = useShortcutSettings({ storage: { getItem: () => JSON.stringify({ save: 'Alt+S' }), setItem: () => {} } })
  assert.equal(shortcuts.settings.value.save, 'Alt+S')
  assert.equal(shortcuts.settings.value.deleteRegion, SHORTCUT_DEFAULTS.deleteRegion)
  shortcuts.settings.value.deleteRegion = 'Alt+S'
  assert.match(shortcuts.conflict.value, /Alt\+S/)
})
