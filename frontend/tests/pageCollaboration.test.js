import assert from 'node:assert/strict'
import test from 'node:test'

import { usePageCollaboration } from '../src/composables/usePageCollaboration.js'


const tick = () => new Promise(resolve => setTimeout(resolve, 20))

function harness(overrides = {}) {
  const calls = []
  let currentPage = 1
  const pages = new Map([
    [1, { page: 1, reviewRevision: 0, candidates: [] }],
    [2, { page: 2, reviewRevision: 0, candidates: [] }],
  ])
  const client = {
    acquirePageLock: async (_job, page) => { calls.push(`acquire:${page}`); return { lock: { page, lockToken: `token-${page}` } } },
    releasePageLock: async (_job, page) => { calls.push(`release:${page}`); return { released: true } },
    heartbeatPageLock: async () => ({ lock: {} }),
    savePage: async (_job, page) => { calls.push(`save:${page}`); return { reviewRevision: 1, reviewedAt: 'now' } },
    getPageLocks: async () => ({ locks: [] }),
    ...overrides.client,
  }
  const collaboration = usePageCollaboration({
    client,
    clientInstanceId: 'tab-a',
    getJobId: () => 'a'.repeat(32),
    getCurrentPage: () => currentPage,
    getPageData: page => pages.get(page),
    saveDraftBoundary: async () => { calls.push('draft') },
    hasPendingDraftChanges: overrides.hasPendingDraftChanges || (() => false),
    switchPage: async page => { calls.push(`render:${page}`); currentPage = page },
    heartbeatIntervalMs: 60_000,
  })
  return { collaboration, calls, getCurrentPage: () => currentPage }
}

test('draft-only workspace changes save on navigation without dirtying either review page', async () => {
  const { collaboration, calls, getCurrentPage } = harness({ hasPendingDraftChanges: () => true })
  await collaboration.enterPage(1)
  calls.length = 0

  await collaboration.enterPage(2)

  assert.deepEqual(calls, ['draft', 'release:1', 'acquire:2', 'render:2'])
  assert.equal(getCurrentPage(), 2)
  assert.deepEqual([...collaboration.dirtyPages.value], [])
})

test('editing only marks dirty and never starts timer persistence', async () => {
  const { collaboration, calls } = harness()
  collaboration.markDirty(1)
  await tick()
  assert.equal(collaboration.dirtyPages.value.has(1), true)
  assert.deepEqual(calls, [])
})

test('dirty page navigation saves then releases, acquires, and renders', async () => {
  const { collaboration, calls, getCurrentPage } = harness()
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)
  await collaboration.enterPage(2)
  assert.deepEqual(calls, ['draft', 'save:1', 'release:1', 'acquire:2', 'render:2'])
  assert.equal(getCurrentPage(), 2)
  assert.equal(collaboration.dirtyPages.value.has(1), false)
})

test('failed save keeps current page lock and dirty state', async () => {
  const { collaboration, calls, getCurrentPage } = harness({
    client: { savePage: async () => { calls.push('save:1'); throw new Error('save failed') } },
  })
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)
  await assert.rejects(() => collaboration.enterPage(2), /save failed/)
  assert.equal(getCurrentPage(), 1)
  assert.equal(collaboration.activeLock.value.page, 1)
  assert.equal(collaboration.dirtyPages.value.has(1), true)
  assert.deepEqual(calls, ['draft', 'save:1'])
})
