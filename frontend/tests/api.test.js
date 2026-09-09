import assert from 'node:assert/strict'
import test from 'node:test'

import { api } from '../src/api.js'


test('tutorial reference manifest uses the tutorial endpoint and remains lazy', async () => {
  const originalFetch = globalThis.fetch
  const requested = []
  globalThis.fetch = async url => {
    requested.push(String(url))
    return {
      ok: true,
      json: async () => ({ files: [{ index: 0, name: 'reference.pdf', url: '/api/tutorial/references/0' }] }),
    }
  }
  try {
    const files = await api.getJobReferenceManifest('tutorial-000207')
    assert.deepEqual(requested, ['/api/tutorial/references'])
    assert.equal(files.length, 1)
    assert.equal(files[0].lazyReference, true)
    assert.equal(files[0].weldMarkerReferenceIndex, 0)
  } finally {
    globalThis.fetch = originalFetch
  }
})
