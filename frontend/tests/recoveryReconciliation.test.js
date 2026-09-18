import assert from 'node:assert/strict'
import test from 'node:test'

import { classifyRecoveryPages } from '../src/recoveryReconciliation.js'


test('recovery comparison separates unchanged, safe local edits, revision conflicts, and unavailable pages', () => {
  const localPages = [
    { page: 1, reviewRevision: 2, candidates: [{ id: 'same', number: '1' }] },
    { page: 2, reviewRevision: 3, candidates: [{ id: 'local-safe', number: '2A' }] },
    { page: 3, reviewRevision: 1, candidates: [{ id: 'local-conflict', number: '3A' }] },
    { page: 4, reviewRevision: 0, candidates: [{ id: 'local-only', number: '4A' }] },
  ]
  const serverPages = [
    { page: 1, reviewRevision: 2, candidates: [{ number: '1', id: 'same' }] },
    { page: 2, reviewRevision: 3, candidates: [{ id: 'server-old', number: '2' }] },
    { page: 3, reviewRevision: 4, candidates: [{ id: 'server-new', number: '3B' }] },
  ]

  const comparison = classifyRecoveryPages(localPages, serverPages)

  assert.deepEqual(comparison.unchangedPages, [1])
  assert.deepEqual(comparison.safePages, [2])
  assert.deepEqual(comparison.conflictPages, [3])
  assert.deepEqual(comparison.unavailablePages, [4])
  assert.equal(comparison.serverByPage.get(3).candidates[0].id, 'server-new')
})
