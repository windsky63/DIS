import assert from 'node:assert/strict'
import test from 'node:test'

import * as workspaceStorage from '../src/workspaceStorage.js'

const { fileFingerprint, prepareDraftV2, sha256Hex } = workspaceStorage

test('SHA-256 fallback works when Web Crypto subtle is unavailable', async () => {
  const bytes = new TextEncoder().encode('abc')
  assert.equal(
    await sha256Hex(bytes, {}),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
  )
})

test('file fingerprint does not require a secure browser context', async () => {
  const originalCrypto = globalThis.crypto
  Object.defineProperty(globalThis, 'crypto', { configurable: true, value: undefined })
  try {
    const file = new File([new TextEncoder().encode('%PDF-test')], 'drawing.pdf', {
      type: 'application/pdf',
      lastModified: 123456789
    })
    const fingerprint = await fileFingerprint(file)
    assert.match(fingerprint, /^[a-f0-9]{64}$/)
  } finally {
    Object.defineProperty(globalThis, 'crypto', { configurable: true, value: originalCrypto })
  }
})

test('draft v2 deduplicates files and separates light pages from full page records', async () => {
  const file = new File([new TextEncoder().encode('%PDF-shared')], 'drawing.pdf', {
    type: 'application/pdf',
    lastModified: 123,
  })
  const stored = { blob: file, name: file.name, type: file.type, lastModified: file.lastModified }
  const page = {
    page: 1,
    candidates: [{ id: 'p1', page: 1, number: 'F1' }],
    layoutObstacles: { textRects: [[1, 2, 3, 4]], processSegments: [] },
  }
  const prepared = await prepareDraftV2('workspace', {
    result: { jobId: 'job', pages: [page], referenceFiles: ['missing-reference.pdf'] },
    projectResults: [{ jobId: 'job', pages: [page], referenceFiles: ['missing-reference.pdf'] }],
    activeProjectIndex: 0,
    targetFile: stored,
    projectFiles: [stored],
    referenceFiles: [],
    pcfFiles: [],
    fileName: file.name,
    currentPage: 1,
  })

  assert.equal(prepared.fileGroups.targetFile[0].storedFileKey, prepared.fileGroups.projectFiles[0].storedFileKey)
  assert.equal(prepared.manifest.fileGroups.targetFile[0].blob, undefined)
  assert.equal(prepared.resultEntries[0].pages[0].value.layoutObstacles.textRects.length, 1)
  assert.equal(prepared.manifest.results[0].lightPages[0].layoutObstacles, undefined)
  assert.deepEqual(prepared.manifest.results[0].lightPages[0].candidates, [])
  assert.equal(prepared.metadata.candidateCount, 1)
  assert.equal(prepared.metadata.archiveStatus, 'partial')
})

test('a materialized stored file keeps its Blob so a cleaned draft can be saved again', async () => {
  const file = new File([new TextEncoder().encode('%PDF-restored')], 'restored.pdf', {
    type: 'application/pdf',
    lastModified: 456,
  })
  const prepared = await prepareDraftV2('restored-workspace', {
    result: { pages: [] },
    targetFile: { storedFileKey: 'previously-cleaned-key', blob: file, name: file.name, type: file.type, size: file.size },
  })

  assert.equal(prepared.fileGroups.targetFile[0].blob, file)
  assert.equal(prepared.manifest.fileGroups.targetFile[0].blob, undefined)
})

test('orphan file cleanup preserves blobs referenced by any remaining draft', () => {
  assert.equal(typeof workspaceStorage.orphanStoredFileKeys, 'function')
  const manifests = [
    { fileGroups: { targetFile: [{ storedFileKey: 'shared' }], referenceFiles: [{ storedFileKey: 'reference' }] } },
    { fileGroups: { projectFiles: [{ storedFileKey: 'shared' }], pcfFiles: [{ storedFileKey: 'pcf' }] } },
  ]

  assert.deepEqual(
    workspaceStorage.orphanStoredFileKeys(manifests, ['shared', 'reference', 'pcf', 'deleted-draft-only']),
    ['deleted-draft-only'],
  )
})

test('orphan file cleanup treats malformed legacy manifests as unreferenced', () => {
  assert.equal(typeof workspaceStorage.orphanStoredFileKeys, 'function')
  assert.deepEqual(
    workspaceStorage.orphanStoredFileKeys([null, {}, { fileGroups: { targetFile: null } }], ['old-pdf']),
    ['old-pdf'],
  )
})
