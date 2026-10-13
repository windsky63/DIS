import assert from 'node:assert/strict'
import test from 'node:test'

import { beijingDate, formatBeijingTime, parseRecordedTime } from '../src/beijingTime.js'

test('formats UTC and offset timestamps as Beijing time', () => {
  assert.equal(formatBeijingTime('2026-09-23T00:00:00Z'), '2026/09/23 08:00:00')
  assert.equal(formatBeijingTime('2026-09-23T08:00:00+08:00'), '2026/09/23 08:00:00')
})

test('treats legacy timestamps without an offset as already recorded in Beijing time', () => {
  assert.equal(parseRecordedTime('2026-09-23T08:00:00').toISOString(), '2026-09-23T00:00:00.000Z')
})

test('calculates filter dates against the Beijing calendar day', () => {
  assert.equal(beijingDate(0, new Date('2026-09-22T16:30:00Z')), '2026-09-23')
  assert.equal(beijingDate(-1, new Date('2026-09-22T16:30:00Z')), '2026-09-22')
})
