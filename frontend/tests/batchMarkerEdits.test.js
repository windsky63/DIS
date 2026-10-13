import assert from 'node:assert/strict'
import test from 'node:test'
import { applyBatchMarkerPatches, batchMarkerRowPatch, createBatchMarkerRows, updateBatchMarkerRow } from '../src/batchMarkerEdits.js'

const style = { shape: 'circle', color: '#d4143c', frameSize: 28, fontSize: 15, lineWidth: 1.35, fillOpacity: 0 }
const page = () => ({ page: 2, reviewRevision: 3, width: 1000, layoutObstacles: { textRects: [[1, 2, 3, 4]] }, candidates: [
  { id: 'w1', page: 2, number: 'W1', xNorm: .2, labelXNorm: .4, included: true },
  { id: 'v1', page: 2, number: 'V1', componentKind: 'design-component', componentType: 'valve' },
] })

test('batch rows retain only editable fields, not the PDF geometry or original candidate objects', () => {
  const source = page()
  const rows = createBatchMarkerRows(source, () => style)
  assert.deepEqual(rows.map(row => row.type), ['weld', 'valve'])
  assert.equal(JSON.stringify(rows).includes('textRects'), false)
  updateBatchMarkerRow(rows[0], 'number', 'W9')
  assert.equal(rows[0].changed, true)
  assert.equal(source.candidates[0].number, 'W1')
  updateBatchMarkerRow(rows[0], 'number', 'W1')
  assert.equal(rows[0].changed, false)
})

test('number and reference patches preserve coordinates, other markers and geometry', () => {
  const source = page()
  const row = createBatchMarkerRows(source, () => style)[0]
  updateBatchMarkerRow(row, 'number', ' W10 ')
  updateBatchMarkerRow(row, 'referenceLabel', 'R10')
  const updated = applyBatchMarkerPatches(source, [batchMarkerRowPatch(row)])
  assert.equal(updated.candidates[0].number, 'W10')
  assert.equal(updated.candidates[0].referenceMatched, true)
  assert.equal(updated.candidates[0].xNorm, .2)
  assert.equal(updated.layoutObstacles, source.layoutObstacles)
  assert.equal(updated.candidates[1], source.candidates[1])
  assert.equal(source.candidates[0].number, 'W1')
})

test('changing appearance creates a per-marker override including all effective appearance fields', () => {
  const row = createBatchMarkerRows(page(), () => style)[0]
  updateBatchMarkerRow(row, 'shape', 'split-circle')
  updateBatchMarkerRow(row, 'secondaryText', 'LOW-1')
  updateBatchMarkerRow(row, 'color', '#123456')
  const patch = batchMarkerRowPatch(row).patch
  assert.deepEqual(patch.markerStyle, { ...style, shape: 'split-circle', color: '#123456' })
  assert.equal(patch.markerStyleOverride, true)
  assert.equal(patch.secondaryText, 'LOW-1')
  assert.equal(Object.hasOwn(patch, 'number'), false)
})

test('invalid included numbers and out-of-range appearance values are refused before saving', () => {
  const row = createBatchMarkerRows(page(), () => style)[0]
  updateBatchMarkerRow(row, 'number', ' ')
  assert.throws(() => batchMarkerRowPatch(row), /必须填写编号/)
  updateBatchMarkerRow(row, 'included', false)
  assert.doesNotThrow(() => batchMarkerRowPatch(row))
  updateBatchMarkerRow(row, 'fontSize', 1000)
  assert.throws(() => batchMarkerRowPatch(row), /字号/)
})

test('a changed server revision or missing marker never produces a partially edited page', () => {
  const source = page()
  const row = createBatchMarkerRows(source, () => style)[0]
  updateBatchMarkerRow(row, 'number', 'W2')
  const change = batchMarkerRowPatch(row)
  assert.throws(() => applyBatchMarkerPatches({ ...source, reviewRevision: 4 }, [change]), /已被更新/)
  assert.throws(() => applyBatchMarkerPatches(source, [change, { ...change, id: 'missing' }]), /已不存在/)
  assert.equal(source.candidates[0].number, 'W1')
})

test('batch frame size survives save and remains an individual override', () => {
  const source = page()
  const row = createBatchMarkerRows(source, () => style)[0]
  updateBatchMarkerRow(row, 'frameSize', 46)
  const updated = applyBatchMarkerPatches(source, [batchMarkerRowPatch(row)])
  assert.equal(updated.candidates[0].markerStyle.frameSize, 46)
  assert.equal(updated.candidates[0].markerStyleOverride, true)
  assert.equal(updated.candidates[1], source.candidates[1])
  updateBatchMarkerRow(row, 'frameSize', 65)
  assert.throws(() => batchMarkerRowPatch(row), /框尺寸/)
})
