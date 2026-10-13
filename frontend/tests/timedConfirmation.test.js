import assert from 'node:assert/strict'
import test from 'node:test'

import { useTimedConfirmation } from '../src/composables/useTimedConfirmation.js'

test('a destructive action requires a second click and clears after confirmation', () => {
  const scheduled = []
  const confirmation = useTimedConfirmation({
    schedule: callback => { scheduled.push(callback); return scheduled.length },
    cancelSchedule() {},
  })
  assert.equal(confirmation.request('logout'), false)
  assert.equal(confirmation.pendingId.value, 'logout')
  assert.equal(confirmation.request('logout'), true)
  assert.equal(confirmation.pendingId.value, '')
})

test('a pending destructive action expires without running it', () => {
  let expire
  const confirmation = useTimedConfirmation({
    schedule: callback => { expire = callback; return 1 },
    cancelSchedule() {},
  })
  assert.equal(confirmation.request('logout'), false)
  expire()
  assert.equal(confirmation.pendingId.value, '')
})
