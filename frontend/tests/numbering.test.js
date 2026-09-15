import assert from 'node:assert/strict'
import test from 'node:test'

import { isSpecialMarker, numberDocumentResult, resultHasMissingNumbers } from '../src/numbering.js'


test('numbering keeps reference labels and skips their reserved component numbers', () => {
  const result = { pages: [{ page: 1, candidates: [
    { x: 1, y: 1, componentKind: 'design-component', componentType: 'valve', referenceLabel: 'V1' },
    { x: 2, y: 2, componentKind: 'design-component', componentType: 'valve' },
    { x: 3, y: 3 },
  ] }] }
  numberDocumentResult(result, 4, { useReferenceNumber: true, formatWeldNumber: number => `F${number}` })
  assert.deepEqual(result.pages[0].candidates.map(item => item.number), ['V1', 'V2', 'F4'])
  assert.equal(resultHasMissingNumbers(result), false)
})

test('special markers keep their own number and do not consume weld numbers', () => {
  const result = { pages: [{ page: 1, candidates: [
    { x: 1, y: 1, number: 'M-custom', componentKind: 'special-marker', componentType: 'special' },
    { x: 2, y: 2 },
  ] }] }
  numberDocumentResult(result, 7, { formatWeldNumber: number => `W${number}` })
  assert.deepEqual(result.pages[0].candidates.map(item => item.number), ['M-custom', 'W7'])
  assert.equal(isSpecialMarker(result.pages[0].candidates[0]), true)
})
