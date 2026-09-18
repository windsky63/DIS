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
    [3, { page: 3, reviewRevision: 0, candidates: [] }],
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
    getJobId: () => overrides.jobId || 'a'.repeat(32),
    getCurrentPage: () => currentPage,
    getPageData: page => pages.get(page),
    saveDraftBoundary: async () => { calls.push('draft') },
    hasPendingDraftChanges: overrides.hasPendingDraftChanges || (() => false),
    switchPage: async (page, options) => {
      calls.push(`render:${page}`)
      currentPage = page
      await overrides.onSwitch?.(page, options, pages)
    },
    heartbeatIntervalMs: 60_000,
  })
  return { collaboration, calls, pages, getCurrentPage: () => currentPage }
}

test('draft-only workspace changes save on navigation without dirtying either review page', async () => {
  const { collaboration, calls, getCurrentPage } = harness({ hasPendingDraftChanges: () => true })
  await collaboration.enterPage(1)
  calls.length = 0

  await collaboration.enterPage(2)

  assert.deepEqual(calls, ['render:2', 'draft', 'acquire:2', 'release:1'])
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

test('dirty page navigation renders then saves, acquires, and releases the original lock', async () => {
  const { collaboration, calls, getCurrentPage } = harness()
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)
  await collaboration.enterPage(2)
  assert.deepEqual(calls, ['render:2', 'save:1', 'draft', 'acquire:2', 'release:1'])
  assert.equal(getCurrentPage(), 2)
  assert.equal(collaboration.dirtyPages.value.has(1), false)
})

test('locked target page is skipped and the next available page is opened', async () => {
  const { collaboration, calls, getCurrentPage } = harness({
    client: {
      acquirePageLock: async (_job, page) => {
        calls.push(`acquire:${page}`)
        if (page === 2) {
          const cause = new Error('该页正在被其他窗口核对')
          cause.status = 423
          cause.payload = { lock: { page: 2, owner: { userId: 'user-a', username: 'A' }, clientInstanceId: 'tab-a' } }
          throw cause
        }
        return { lock: { page, lockToken: `token-${page}` } }
      },
    },
  })
  await collaboration.enterPage(1)
  calls.length = 0

  const enteredPage = await collaboration.enterPage(2, [3])

  assert.equal(enteredPage, 3)
  assert.equal(getCurrentPage(), 3)
  assert.deepEqual(calls, ['render:2', 'acquire:2', 'acquire:3', 'render:3', 'release:1'])
})

test('failed save keeps current page lock and dirty state', async () => {
  const { collaboration, calls, getCurrentPage } = harness({
    client: { savePage: async () => { calls.push('save:1'); throw new Error('save failed') } },
  })
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)
  await assert.rejects(() => collaboration.enterPage(2), /保存失败，请重试.*save failed/)
  assert.equal(getCurrentPage(), 1)
  assert.equal(collaboration.activeLock.value.page, 1)
  assert.equal(collaboration.dirtyPages.value.has(1), true)
  assert.deepEqual(calls, ['render:2', 'save:1', 'draft', 'render:1'])
})

test('browser draft failure does not prevent the dirty page reaching the server', async () => {
  const calls = []

  const failing = usePageCollaboration({
    client: {
      savePage: async (_job, page) => { calls.push(`save:${page}`); return { reviewRevision: 1, reviewedAt: 'now' } },
    },
    getJobId: () => 'a'.repeat(32),
    getCurrentPage: () => 1,
    getPageData: () => ({ page: 1, reviewRevision: 0, candidates: [] }),
    saveDraftBoundary: async () => { calls.push('draft'); throw new Error('IndexedDB unavailable') },
    switchPage: async () => {},
    heartbeatIntervalMs: 0,
  })
  failing.activeLock.value = { jobId: 'a'.repeat(32), page: 1, lockToken: 'token-1' }
  failing.markDirty(1)

  await assert.rejects(() => failing.saveCurrentPage(), /IndexedDB unavailable/)

  assert.deepEqual(calls, ['save:1', 'draft'])
  assert.equal(failing.dirtyPages.value.has(1), false)
})

test('tutorial changes stay in the browser draft and never acquire a lock or save a server page', async () => {
  const { collaboration, calls, getCurrentPage } = harness({ jobId: 'tutorial-000207' })
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)

  await collaboration.enterPage(2)

  assert.deepEqual(calls, ['render:2', 'draft'])
  assert.equal(getCurrentPage(), 2)
  assert.equal(collaboration.dirtyPages.value.has(1), false)
  assert.equal(collaboration.activeLock.value, null)
})

test('dirty navigation displays the target before the original page save completes', async () => {
  let finishSave
  const { collaboration, getCurrentPage } = harness({
    client: {
      savePage: async () => {
        await new Promise(resolve => { finishSave = resolve })
        return { reviewRevision: 1, reviewedAt: 'now' }
      },
    },
  })
  await collaboration.enterPage(1)
  collaboration.markDirty(1)

  const navigation = collaboration.enterPage(2)
  await new Promise(resolve => setTimeout(resolve, 0))

  assert.equal(getCurrentPage(), 2)
  assert.equal(collaboration.synchronizing.value, true)
  finishSave()
  await navigation
  assert.equal(collaboration.activeLock.value.page, 2)
})

test('failed optimistic navigation returns to the original page without losing edits', async () => {
  const { collaboration, calls, pages, getCurrentPage } = harness({
    client: {
      savePage: async () => { throw new Error('network unavailable') },
    },
    onSwitch: (page, options, storedPages) => {
      if (page === 1 && options?.preserveLocal !== true) storedPages.get(1).candidates = [{ id: 'server-copy' }]
    },
  })
  await collaboration.enterPage(1)
  calls.length = 0
  const editedCandidates = [{ id: 'local-edit', number: 'W-99', x: 12, y: 34 }]
  pages.get(1).candidates = editedCandidates
  collaboration.markDirty(1)

  await assert.rejects(() => collaboration.enterPage(2), /保存失败，请重试/)

  assert.equal(getCurrentPage(), 1)
  assert.equal(collaboration.activeLock.value.page, 1)
  assert.equal(collaboration.dirtyPages.value.has(1), true)
  assert.equal(pages.get(1).candidates, editedCandidates)
  assert.deepEqual(pages.get(1).candidates, [{ id: 'local-edit', number: 'W-99', x: 12, y: 34 }])
  assert.deepEqual(calls, ['render:2', 'draft', 'render:1'])
})

test('rapid optimistic navigation acquires only the latest target lock', async () => {
  let finishSave
  const heldLocks = new Set()
  const { collaboration, calls, getCurrentPage } = harness({
    client: {
      acquirePageLock: async (_job, page) => {
        calls.push(`acquire:${page}`)
        heldLocks.add(page)
        return { lock: { page, lockToken: `token-${page}` } }
      },
      releasePageLock: async (_job, page) => {
        calls.push(`release:${page}`)
        heldLocks.delete(page)
        return { released: true }
      },
      savePage: async () => {
        calls.push('save:1')
        await new Promise(resolve => { finishSave = resolve })
        return { reviewRevision: 1, reviewedAt: 'now' }
      },
    },
  })
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)

  const secondPage = collaboration.enterPage(2)
  await new Promise(resolve => setTimeout(resolve, 0))
  const thirdPage = collaboration.enterPage(3)
  await new Promise(resolve => setTimeout(resolve, 0))
  assert.equal(getCurrentPage(), 3)
  finishSave()
  const [secondResult, thirdResult] = await Promise.all([secondPage, thirdPage])

  assert.equal(secondResult, 2)
  assert.equal(thirdResult, 3)
  assert.equal(getCurrentPage(), 3)
  assert.equal(collaboration.activeLock.value.page, 3)
  assert.deepEqual([...heldLocks], [3])
  assert.equal(calls.includes('acquire:2'), false)
})

test('confirmed recovery overwrite saves every dirty page with latest revisions and writes one browser draft', async () => {
  const saved = []
  const { collaboration, calls, pages } = harness({
    client: {
      getJobPage: async (_job, page) => ({ page: {
        page,
        reviewRevision: page === 2 ? 7 : 0,
        candidates: [{ id: `server-${page}` }],
        layoutObstacles: { textRects: [], processSegments: [] },
      } }),
      savePage: async (_job, page, pageData, baseRevision) => {
        calls.push(`save:${page}`)
        saved.push({ page, baseRevision, candidates: structuredClone(pageData.candidates) })
        return { reviewRevision: baseRevision + 1, reviewedAt: 'now' }
      },
    },
  })
  pages.get(1).candidates = [{ id: 'local-1' }]
  pages.get(2).candidates = [{ id: 'local-2' }]
  pages.get(3).candidates = [{ id: 'local-unavailable' }]
  collaboration.markDirty(1)
  collaboration.markDirty(2)
  collaboration.markDirty(3)

  await collaboration.saveAllDirtyPages({ overwritePages: [2], skipPages: [3] })

  assert.deepEqual(saved, [
    { page: 1, baseRevision: 0, candidates: [{ id: 'local-1' }] },
    { page: 2, baseRevision: 7, candidates: [{ id: 'local-2' }] },
  ])
  assert.equal(calls.filter(call => call === 'draft').length, 1)
  assert.deepEqual([...collaboration.dirtyPages.value], [3])
  assert.equal(calls.includes('acquire:3'), false)
})

test('navigation waits for an in-flight all-page save instead of submitting the current page twice', async () => {
  let finishSave
  let saveCalls = 0
  const { collaboration, getCurrentPage } = harness({
    client: {
      savePage: async () => {
        saveCalls += 1
        await new Promise(resolve => { finishSave = resolve })
        return { reviewRevision: 1, reviewedAt: 'now' }
      },
    },
  })
  await collaboration.enterPage(1)
  collaboration.markDirty(1)

  const batchSave = collaboration.saveAllDirtyPages()
  await new Promise(resolve => setTimeout(resolve, 0))
  const navigation = collaboration.enterPage(2)
  await new Promise(resolve => setTimeout(resolve, 0))
  finishSave()
  await Promise.all([batchSave, navigation])

  assert.equal(saveCalls, 1)
  assert.equal(getCurrentPage(), 2)
})

test('activating a recovered page replaces a stale lock from another job', async () => {
  const { collaboration, calls } = harness()
  collaboration.activeLock.value = { jobId: 'b'.repeat(32), page: 1, lockToken: 'old-token' }

  await collaboration.acquireCurrentPage()

  assert.equal(collaboration.activeLock.value.jobId, 'a'.repeat(32))
  assert.deepEqual(calls.slice(0, 2), ['release:1', 'acquire:1'])
})

test('leaving a recovered workspace saves every dirty page before releasing the current lock', async () => {
  const { collaboration, calls } = harness()
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)
  collaboration.markDirty(2)

  await collaboration.leavePage()

  assert.deepEqual([...collaboration.dirtyPages.value], [])
  assert.equal(calls.filter(call => call === 'draft').length, 1)
  assert.deepEqual(calls.filter(call => call.startsWith('save:')), ['save:1', 'save:2'])
  assert.equal(calls.at(-1), 'release:1')
})
