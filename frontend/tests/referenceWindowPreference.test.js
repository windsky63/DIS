import assert from 'node:assert/strict'
import test from 'node:test'

import {
  loadAutoReferenceWindow,
  loadReferenceHintLocation,
  saveAutoReferenceWindow,
  saveReferenceHintLocation,
  shouldShowHintsInReferenceWindow,
} from '../src/referenceWindowPreference.js'


test('automatic reference window preference is persisted as a boolean', () => {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  assert.equal(loadAutoReferenceWindow(storage), false)
  saveAutoReferenceWindow(storage, true)
  assert.equal(loadAutoReferenceWindow(storage), true)
  saveAutoReferenceWindow(storage, false)
  assert.equal(loadAutoReferenceWindow(storage), false)
})

test('reference hints default to the detached reference window and persist the selected location', () => {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  assert.equal(loadReferenceHintLocation(storage), 'reference')
  saveReferenceHintLocation(storage, 'design')
  assert.equal(loadReferenceHintLocation(storage), 'design')
  saveReferenceHintLocation(storage, 'invalid')
  assert.equal(loadReferenceHintLocation(storage), 'reference')
})

test('reference window owns hints only while detached and configured as reference', () => {
  assert.equal(shouldShowHintsInReferenceWindow(true, 'reference'), true)
  assert.equal(shouldShowHintsInReferenceWindow(true, 'design'), false)
  assert.equal(shouldShowHintsInReferenceWindow(false, 'reference'), false)
})
