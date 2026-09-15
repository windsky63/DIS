import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ERROR_MESSAGES_KEY,
  OPERATION_MESSAGES_KEY,
  loadMessagePreferences,
  saveMessagePreference,
} from '../src/messagePreferences.js'

function createStorage(entries = {}) {
  const values = new Map(Object.entries(entries))
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null },
    setItem(key, value) { values.set(key, String(value)) },
    value(key) { return values.get(key) },
  }
}

test('message preferences default both operation and error messages to enabled', () => {
  assert.deepEqual(loadMessagePreferences(createStorage()), {
    operationMessagesEnabled: true,
    errorMessagesEnabled: true,
  })
})

test('message preferences ignore the removed combined setting', () => {
  const storage = createStorage({ 'weld-marker.messages-enabled': 'false' })

  assert.deepEqual(loadMessagePreferences(storage), {
    operationMessagesEnabled: true,
    errorMessagesEnabled: true,
  })
})

test('message preferences load independent settings', () => {
  const storage = createStorage({
    [OPERATION_MESSAGES_KEY]: 'false',
    [ERROR_MESSAGES_KEY]: 'true',
  })

  assert.deepEqual(loadMessagePreferences(storage), {
    operationMessagesEnabled: false,
    errorMessagesEnabled: true,
  })
})

test('message preferences are saved independently', () => {
  const storage = createStorage()

  saveMessagePreference(storage, OPERATION_MESSAGES_KEY, false)
  saveMessagePreference(storage, ERROR_MESSAGES_KEY, true)

  assert.equal(storage.value(OPERATION_MESSAGES_KEY), 'false')
  assert.equal(storage.value(ERROR_MESSAGES_KEY), 'true')
})
