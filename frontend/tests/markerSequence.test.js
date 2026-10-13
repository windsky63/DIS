import assert from 'node:assert/strict'
import test from 'node:test'

import { parseMarkerSequenceNumber, shiftMarkerNumbersAfterSelection } from '../src/markerSequence.js'

test('shifts duplicate and following marker numbers while keeping the selection unchanged', () => {
  const selected = { id: 'new-f4', number: 'F4', included: true, componentType: 'flange' }
  const page = { candidates: [
    { id: 'f1', number: 'F1', included: true, componentType: 'flange' },
    { id: 'old-f4', number: 'F4', included: true, componentType: 'flange' },
    { id: 'f5', number: 'F5', included: true, componentType: 'flange' },
    { id: 'f6', number: 'F6', included: true, componentType: 'flange' },
    selected,
  ] }

  const result = shiftMarkerNumbersAfterSelection(page, selected)

  assert.equal(selected.number, 'F4')
  assert.deepEqual(page.candidates.map(item => item.number), ['F1', 'F5', 'F6', 'F7', 'F4'])
  assert.equal(result.changed.length, 3)
})

test('only shifts active markers of the same type, prefix, and suffix', () => {
  const selected = { id: 'selected', number: 'XV-004-A', included: true, componentType: 'valve' }
  const page = { candidates: [
    selected,
    { id: 'same', number: 'XV-004-A', included: true, componentType: 'valve' },
    { id: 'later', number: 'XV-009-A', included: true, componentType: 'valve' },
    { id: 'earlier', number: 'XV-003-A', included: true, componentType: 'valve' },
    { id: 'other-suffix', number: 'XV-004-B', included: true, componentType: 'valve' },
    { id: 'other-type', number: 'XV-004-A', included: true, componentType: 'flange' },
    { id: 'excluded', number: 'XV-004-A', included: false, componentType: 'valve' },
  ] }

  shiftMarkerNumbersAfterSelection(page, selected)

  assert.deepEqual(page.candidates.map(item => item.number), [
    'XV-004-A', 'XV-005-A', 'XV-010-A', 'XV-003-A', 'XV-004-B', 'XV-004-A', 'XV-004-A',
  ])
})

test('rejects labels without a numeric sequence and parses the final numeric group', () => {
  assert.equal(parseMarkerSequenceNumber('FINAL'), null)
  assert.deepEqual(parseMarkerSequenceNumber('AREA2-F04-A'), {
    prefix: 'AREA2-F', value: 4, width: 2, suffix: '-A',
  })
})
