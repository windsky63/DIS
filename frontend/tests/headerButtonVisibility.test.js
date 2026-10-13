import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createHeaderButtonVisibility,
  loadHeaderButtonVisibility,
  saveHeaderButtonVisibility,
} from '../src/headerButtonVisibility.js'

test('invalid or incomplete saved header visibility is normalized against visible defaults', () => {
  const visibility = createHeaderButtonVisibility({ assistant: false, tutorial: 0, unknown: false })

  assert.equal(visibility.assistant, false)
  assert.equal(visibility.tutorial, true)
  assert.equal(visibility.analysisQueue, true)
  assert.equal('unknown' in visibility, false)
  assert.equal('settings' in visibility, false)
})

test('header visibility round-trips through storage without a setting-button flag', () => {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  const expected = createHeaderButtonVisibility({ analysisQueue: false, userMenu: false })

  saveHeaderButtonVisibility(storage, expected)

  assert.deepEqual(loadHeaderButtonVisibility(storage), expected)
  assert.equal(JSON.parse([...values.values()][0]).settings, undefined)
})
