import assert from 'node:assert/strict'
import test from 'node:test'

import { saveAndCloseWorkspace } from '../src/saveAndCloseWorkspace.js'


test('save and close persists before releasing and closing the workspace', async () => {
  const calls = []

  await saveAndCloseWorkspace({
    persist: async () => { calls.push('persist') },
    leave: async () => { calls.push('leave') },
    close: () => { calls.push('close') },
    showTasks: async () => { calls.push('tasks') },
  })

  assert.deepEqual(calls, ['persist', 'leave', 'close', 'tasks'])
})

test('save and close preserves the open workspace when persistence fails', async () => {
  const calls = []

  await assert.rejects(() => saveAndCloseWorkspace({
    persist: async () => { calls.push('persist'); throw new Error('save failed') },
    leave: async () => { calls.push('leave') },
    close: () => { calls.push('close') },
    showTasks: async () => { calls.push('tasks') },
  }), /save failed/)

  assert.deepEqual(calls, ['persist'])
})
