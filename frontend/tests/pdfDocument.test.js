import assert from 'node:assert/strict'
import test from 'node:test'

import { pdfDocumentParameters } from '../src/services/pdfDocument.js'


test('remote job PDFs use HTTP Range before background archival', () => {
  const parameters = pdfDocumentParameters({ url: '/api/jobs/id/target', lazyTarget: true })

  assert.equal(parameters.url, '/api/jobs/id/target')
  assert.equal(parameters.disableRange, false)
  assert.equal(parameters.disableStream, true)
  assert.equal(parameters.disableAutoFetch, true)
  assert.equal(parameters.rangeChunkSize, 64 * 1024)
})

test('local files continue to use array-buffer loading', () => {
  assert.equal(pdfDocumentParameters({ arrayBuffer() {} }), null)
})
