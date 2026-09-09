import assert from 'node:assert/strict'
import test from 'node:test'

import { materializeWorkspaceFile, restoreWorkspaceFile } from '../src/workspaceFile.js'

test('keeps a native File returned by the tutorial API intact', async () => {
  const source = new File([new Uint8Array([37, 80, 68, 70])], '000207.pdf', { type: 'application/pdf' })
  let storageReads = 0
  const resolved = await materializeWorkspaceFile(source, async () => { storageReads += 1 })

  assert.equal(resolved, source)
  assert.equal(resolved.name, '000207.pdf')
  assert.equal(storageReads, 0)
})

test('restores a serialized workspace Blob as a File', () => {
  const restored = restoreWorkspaceFile({
    blob: new Blob([new Uint8Array([1, 2, 3])], { type: 'application/pdf' }),
    name: 'saved.pdf',
    type: 'application/pdf',
    lastModified: 123,
    relativePath: 'refs/saved.pdf',
    referenceIndex: 4,
  })

  assert.ok(restored instanceof File)
  assert.equal(restored.name, 'saved.pdf')
  assert.equal(restored.webkitRelativePath, 'refs/saved.pdf')
  assert.equal(restored.weldMarkerReferenceIndex, 4)
})
