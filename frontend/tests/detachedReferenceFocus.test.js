import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createDetachedReferenceFocus,
  resolveDetachedReferenceViewport,
} from '../src/detachedReferenceFocus.js'

test('detached hint selection adds the file and page required by marker filtering', () => {
  assert.deepEqual(createDetachedReferenceFocus(
    { type: 'flange', label: 'FL12', point: [500, 250] },
    { hintDocumentFile: 'EP3D.pdf', page: 8 },
  ), {
    type: 'flange', label: 'FL12', point: [500, 250], file: 'EP3D.pdf', page: 8,
  })
})

test('detached hint navigation centers the PDF point at a readable zoom', () => {
  assert.deepEqual(resolveDetachedReferenceViewport({
    focus: { point: [500, 250] },
    pageInfo: { width: 1000, height: 500 },
    layout: { width: 1200, height: 600 },
    viewport: { width: 800, height: 600 },
    zoom: 1,
  }), {
    zoom: 2.2,
    pan: { x: -920, y: -360 },
  })
})

test('detached hint navigation rejects incomplete coordinates', () => {
  assert.equal(createDetachedReferenceFocus({ label: 'FL12' }, { hintDocumentFile: 'EP3D.pdf', page: 8 }), null)
  assert.equal(resolveDetachedReferenceViewport({ focus: null }), null)
})
