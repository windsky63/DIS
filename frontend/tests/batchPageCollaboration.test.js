import assert from 'node:assert/strict'
import test from 'node:test'
import { usePageCollaboration } from '../src/composables/usePageCollaboration.js'

const clone = value => JSON.parse(JSON.stringify(value))
function fixture({ lockedPage, failedPage, clientOverrides = {} } = {}) {
  const jobId = 'a'.repeat(32), calls = [], locks = new Map()
  const pages = [1, 2, 3].map(page => ({ page, reviewRevision: 0, candidates: [{ id: `w${page}`, number: `W${page}`, x: 10 }], layoutObstacles: {} }))
  const server = new Map(pages.map(page => [page.page, clone(page)]))
  const client = {
    acquirePageLock: async (_job, page) => {
      if (page === lockedPage) throw Object.assign(new Error('busy'), { status: 423, payload: { lock: { owner: { username: '另一用户' } } } })
      const lock = { page, lockToken: `lease-${page}` }
      locks.set(page, lock); calls.push(`acquire:${page}`); return { lock }
    },
    releasePageLock: async (_job, page, _client, token) => { assert.equal(locks.get(page).lockToken, token); locks.delete(page); calls.push(`release:${page}`) },
    getPageLocks: async () => ({ locks: [] }), getJobPage: async (_job, page) => ({ page: clone(server.get(page)) }),
    savePage: async (_job, page, data, revision, _client, token) => {
      assert.equal(locks.get(page).lockToken, token)
      assert.equal(server.get(page).reviewRevision, revision)
      if (page === failedPage) throw new Error('network failure')
      const saved = { ...clone(data), reviewRevision: revision + 1 }
      server.set(page, saved); calls.push(`save:${page}`); return saved
    },
    ...clientOverrides,
  }
  const collaboration = usePageCollaboration({ client, clientInstanceId: 'tab', getJobId: () => jobId,
    getCurrentPage: () => 1, getPageData: number => pages.find(page => page.page === number), switchPage: async () => {}, heartbeatIntervalMs: 0,
    savePageDraftBoundary: async page => calls.push(`draft:${page}`),
  })
  const change = page => ({ page, id: `w${page}`, revision: 0, patch: { number: `NEW${page}` } })
  return { jobId, calls, locks, pages, server, collaboration, change }
}

test('confirmed older batch content is synchronized while newer staged changes remain failed and retained', async () => {
  const oldPage = { page: 1, reviewRevision: 1, reviewSaveRequestId: 'old-request', candidates: [{ id: 'w1', number: 'OLD-SAVED', x: 10 }], layoutObstacles: {} }
  const f = fixture({ clientOverrides: { savePage: async () => {
    throw Object.assign(new Error('older save confirmed; save newer edits again'), { confirmedSave: oldPage, confirmedPage: oldPage })
  } } })
  try {
    await f.collaboration.enterPage(1)
    const changes = [f.change(1)]
    const desired = changes[0].patch.number
    const history = []
    const response = await f.collaboration.applyMarkerEdits(f.jobId, changes, { onPageSaved: (before, after) => history.push([before.candidates[0].number, after.candidates[0].number]) })
    assert.equal(response.errors.length, 1)
    assert.equal(f.pages[0].candidates[0].number, 'OLD-SAVED')
    assert.equal(f.pages[0].reviewRevision, 1)
    assert.equal(changes[0].patch.number, desired)
    assert.deepEqual(history, [['W1', 'OLD-SAVED']])
  } finally { f.collaboration.dispose() }
})

test('batch edits reuse the current lock, release temporary page locks and save each changed page once', async () => {
  const f = fixture()
  await f.collaboration.enterPage(1)
  f.calls.length = 0
  const history = []
  const response = await f.collaboration.applyMarkerEdits(f.jobId, [f.change(1), f.change(2)], {
    onPageSaved: (before, after) => history.push([before.candidates[0].number, after.candidates[0].number]),
  })
  assert.deepEqual(response.errors, [])
  assert.deepEqual(response.saved.map(saved => saved.page), [1, 2])
  assert.deepEqual(f.calls, ['save:1', 'draft:1', 'acquire:2', 'save:2', 'draft:2', 'release:2'])
  assert.deepEqual(history, [['W1', 'NEW1'], ['W2', 'NEW2']])
  assert.equal(f.collaboration.activeLock.value.page, 1)
  assert.deepEqual([...f.locks.keys()], [1])
  await f.collaboration.release()
})

test('locked and remotely updated pages are skipped while other pages save without losing failed edits', async () => {
  const f = fixture({ lockedPage: 2 })
  await f.collaboration.enterPage(1)
  f.server.get(3).reviewRevision = 1
  const response = await f.collaboration.applyMarkerEdits(f.jobId, [f.change(1), f.change(2), f.change(3)])
  assert.deepEqual(response.saved.map(saved => saved.page), [1])
  assert.deepEqual(response.errors.map(error => error.page), [2, 3])
  assert.match(response.errors[0].message, /另一用户/)
  assert.match(response.errors[1].message, /已被更新/)
  assert.equal(f.server.get(2).candidates[0].number, 'W2')
  assert.equal(f.server.get(3).candidates[0].number, 'W3')
  assert.deepEqual([...f.locks.keys()], [1])
  await f.collaboration.release()
})

test('a failed server save does not modify the local page or add an undo entry', async () => {
  const f = fixture({ failedPage: 2 })
  await f.collaboration.enterPage(1)
  let histories = 0
  const response = await f.collaboration.applyMarkerEdits(f.jobId, [f.change(2)], { onPageSaved: () => histories += 1 })
  assert.equal(response.saved.length, 0)
  assert.equal(response.errors.length, 1)
  assert.equal(f.pages[1].candidates[0].number, 'W2')
  assert.equal(histories, 0)
  assert.deepEqual([...f.locks.keys()], [1])
  await f.collaboration.release()
})

test('a batch created for a different task is refused', async () => {
  const f = fixture()
  await assert.rejects(f.collaboration.applyMarkerEdits('other-job', [f.change(1)]), /任务已切换/)
  assert.deepEqual(f.calls, [])
})
