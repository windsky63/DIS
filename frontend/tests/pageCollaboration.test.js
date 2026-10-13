import assert from 'node:assert/strict'
import test from 'node:test'

import { usePageCollaboration } from '../src/composables/usePageCollaboration.js'


const tick = () => new Promise(resolve => setTimeout(resolve, 20))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((accept, fail) => { resolve = accept; reject = fail })
  return { promise, resolve, reject }
}

test('confirming an older save advances only server metadata and keeps subsequent edits dirty', async () => {
  const confirmed = { reviewRevision: 1, reviewSaveRequestId: 'old-save', reviewedAt: 'now' }
  const { collaboration, pages } = harness({ client: { savePage: async () => {
    throw Object.assign(new Error('older save confirmed'), { confirmedSave: confirmed })
  } } })
  try {
    await collaboration.enterPage(1)
    pages.get(1).candidates = [{ id: 'w1', number: 'new-content' }]
    collaboration.markDirty(1)
    await assert.rejects(collaboration.saveCurrentPage())
    assert.equal(pages.get(1).reviewRevision, 1)
    assert.equal(pages.get(1).candidates[0].number, 'new-content')
    assert.equal(collaboration.dirtyPages.value.has(1), true)
  } finally { collaboration.dispose() }
})

test('edits arriving during a pending save retain dirty status instead of closing as fully saved', async () => {
  const gate = deferred()
  const { collaboration, pages } = harness({ client: { savePage: () => gate.promise } })
  try {
    await collaboration.enterPage(1)
    pages.get(1).candidates = [{ id: 'w1', number: '1' }]
    collaboration.markDirty(1)
    const saving = collaboration.saveCurrentPage()
    pages.get(1).candidates[0].number = '2'
    gate.resolve({ saved: true, reviewRevision: 1 })
    await assert.rejects(saving, /新修改/)
    assert.equal(pages.get(1).candidates[0].number, '2')
    assert.equal(pages.get(1).reviewRevision, 1)
    assert.equal(collaboration.dirtyPages.value.has(1), true)
  } finally { gate.resolve({ reviewRevision: 1 }); collaboration.dispose() }
})

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
    savePageDraftBoundary: overrides.savePageDraftBoundary,
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

test('opening a page summary loads its markers even when the server revision is unchanged', async () => {
  const { collaboration, pages } = harness({
    client: {
      getJobPage: async (_job, page) => ({
        page: { page, reviewRevision: 0, candidates: [{ id: `marker-${page}`, number: 'W1' }] },
      }),
    },
  })
  pages.set(2, { page: 2, reviewRevision: 0, candidateCount: 1, candidates: [], detailsLoaded: false })

  await collaboration.enterPage(2)

  assert.equal(pages.get(2).detailsLoaded, true)
  assert.deepEqual(pages.get(2).candidates, [{ id: 'marker-2', number: 'W1' }])
  await collaboration.release()
})

test('dirty page navigation persists only the changed page draft when incremental storage is available', async () => {
  const pageDrafts = []
  const { collaboration, calls } = harness({
    savePageDraftBoundary: async page => { pageDrafts.push(page); calls.push(`page-draft:${page}`) },
  })
  await collaboration.enterPage(1)
  calls.length = 0
  collaboration.markDirty(1)

  await collaboration.enterPage(2)

  assert.deepEqual(pageDrafts, [1])
  assert.equal(calls.includes('draft'), false)
  assert.deepEqual(calls, ['render:2', 'save:1', 'page-draft:1', 'acquire:2', 'release:1'])
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

  assert.equal(secondResult, null)
  assert.equal(thirdResult, 3)
  assert.equal(getCurrentPage(), 3)
  assert.equal(collaboration.activeLock.value.page, 3)
  assert.deepEqual([...heldLocks], [3])
  assert.equal(calls.includes('acquire:2'), false)
})

test('an old heartbeat cannot restore the previous page lock after navigation', async () => {
  for (const failed of [false, true]) {
    const response = deferred()
    const { collaboration } = harness({ client: { heartbeatPageLock: () => response.promise } })
    await collaboration.enterPage(1)
    const heartbeat = collaboration.heartbeat().catch(() => null)
    await collaboration.enterPage(2)
    if (failed) response.reject(Object.assign(new Error('old token expired'), { status: 423 }))
    else response.resolve({ lock: { page: 1 } })
    await heartbeat
    assert.equal(collaboration.activeLock.value.page, 2)
    assert.equal(collaboration.readOnly.value, false)
    assert.equal(collaboration.leaseUncertain.value, false)
    await collaboration.release()
  }
})

test('a superseded lock conflict does not cancel the latest navigation or report another user', async () => {
  const conflict = deferred()
  const { collaboration, getCurrentPage } = harness({ client: {
    acquirePageLock: async (_job, page) => page === 2 ? conflict.promise : { lock: { page, lockToken: `token-${page}` } },
  } })
  await collaboration.enterPage(1)
  const older = collaboration.enterPage(2)
  await tick()
  const newer = collaboration.enterPage(3)
  conflict.reject(Object.assign(new Error('other reviewer'), { status: 423, payload: { lock: { page: 2, owner: { username: 'reviewer' } } } }))
  assert.deepEqual(await Promise.all([older, newer]), [null, 3])
  assert.equal(getCurrentPage(), 3)
  assert.equal(collaboration.activeLock.value.page, 3)
  await collaboration.release()
})

test('navigation requested while the original lock is being released still acquires the latest page', async () => {
  const release = deferred()
  const releasing = deferred()
  const { collaboration, getCurrentPage } = harness({ client: {
    releasePageLock: async (_job, page) => {
      if (page === 1) { releasing.resolve(); await release.promise }
      return { released: true }
    },
  } })
  await collaboration.enterPage(1)
  const older = collaboration.enterPage(2)
  await releasing.promise
  const newer = collaboration.enterPage(3)
  release.resolve()
  assert.deepEqual(await Promise.all([older, newer]), [null, 3])
  assert.equal(getCurrentPage(), 3)
  assert.equal(collaboration.activeLock.value.page, 3)
  await collaboration.release()
})

test('a rapid return to the origin retains its token and can still save edits', async () => {
  const acquiring = deferred()
  const finishAcquire = deferred()
  const locks = new Map()
  let sequence = 0
  const { collaboration, calls, getCurrentPage } = harness({ client: {
    acquirePageLock: async (_job, page) => {
      const lock = { page, lockToken: `lease-${++sequence}` }
      locks.set(page, lock)
      if (page === 2) { acquiring.resolve(); await finishAcquire.promise }
      return { lock }
    },
    releasePageLock: async (_job, page, _client, token) => {
      if (locks.get(page)?.lockToken === token) locks.delete(page)
      return { released: true }
    },
    savePage: async (_job, page, _data, _revision, _client, token) => {
      assert.equal(locks.get(page)?.lockToken, token)
      calls.push(`save:${page}`)
      return { reviewRevision: 1 }
    },
  } })
  await collaboration.enterPage(1)
  const originalToken = collaboration.activeLock.value.lockToken
  const older = collaboration.enterPage(2)
  await acquiring.promise
  const newer = collaboration.enterPage(1)
  finishAcquire.resolve()
  assert.deepEqual(await Promise.all([older, newer]), [null, 1])
  assert.equal(getCurrentPage(), 1)
  assert.equal(collaboration.activeLock.value.lockToken, originalToken)
  assert.deepEqual([...locks.keys()], [1])
  collaboration.markDirty(1)
  await collaboration.saveCurrentPage()
  assert.equal(collaboration.dirtyPages.value.size, 0)
  await collaboration.release()
})

test('a heartbeat failure for the current lease still blocks editing', async () => {
  const { collaboration } = harness({ client: {
    heartbeatPageLock: async () => { throw Object.assign(new Error('lease lost'), { status: 423 }) },
  } })
  await collaboration.enterPage(1)
  await assert.rejects(collaboration.heartbeat(), /lease lost/)
  assert.equal(collaboration.readOnly.value, true)
  await collaboration.release()
})

test('closing the workspace during a lock request cleans up its eventual lease without restarting navigation', async () => {
  const acquiring = deferred(), finishAcquire = deferred()
  const released = []
  const { collaboration } = harness({ client: {
    acquirePageLock: async (_job, page) => {
      if (page === 2) { acquiring.resolve(); await finishAcquire.promise }
      return { lock: { page, lockToken: `token-${page}` } }
    },
    releasePageLock: async (_job, page) => { released.push(page); return { released: true } },
  } })
  await collaboration.enterPage(1)
  const navigation = collaboration.enterPage(2)
  await acquiring.promise
  collaboration.dispose()
  finishAcquire.resolve()
  assert.equal(await navigation, null)
  assert.equal(collaboration.activeLock.value, null)
  assert.equal(collaboration.synchronizing.value, false)
  assert.deepEqual(released.sort(), [1, 2])
})

test('an older lock-list response cannot restore a released-page indicator', async () => {
  const resolvers = []
  const { collaboration } = harness({
    client: {
      getPageLocks: async () => new Promise(resolve => { resolvers.push(resolve) }),
    },
  })

  const older = collaboration.refreshLocks()
  const newer = collaboration.refreshLocks()
  resolvers[1]({ locks: [] })
  await newer
  resolvers[0]({ locks: [{ page: 2, clientInstanceId: 'stale-tab', owner: { username: '旧锁' } }] })
  await older

  assert.deepEqual(collaboration.lockSummaries.value, [])
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
