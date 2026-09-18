import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, ref } from 'vue'
import { useWorkspaceHistory } from '../src/composables/useWorkspaceHistory.js'
import { usePageCollaboration } from '../src/composables/usePageCollaboration.js'

const clone = value => JSON.parse(JSON.stringify(value))

function fixture() {
  const currentPage = ref(1)
  const result = ref({ jobId: 'a'.repeat(32), pages: [
    { page: 1, reviewRevision: 0, candidates: [{ id: 'V1' }], layoutObstacles: {} },
    { page: 2, reviewRevision: 0, candidates: [{ id: 'V2' }], layoutObstacles: {} },
  ] })
  const pages = computed(() => result.value.pages)
  const pageData = computed(() => pages.value.find(page => page.page === currentPage.value))
  const server = new Map(pages.value.map(page => [page.page, clone(page)]))
  const calls = []
  let history
  const client = {
    acquirePageLock: async (_job, page) => { calls.push(`acquire:${page}`); return { lock: { page, lockToken: `token-${page}` } } },
    releasePageLock: async (_job, page) => { calls.push(`release:${page}`); return {} },
    getPageLocks: async () => ({ locks: [] }),
    getJobPage: async (_job, page) => { calls.push(`fetch:${page}`); return { page: clone(server.get(page)) } },
    savePage: async (_job, page, incoming, base) => {
      calls.push(`save:${page}`)
      assert.equal(base, server.get(page).reviewRevision, 'must submit latest server revision')
      const saved = { ...clone(incoming), reviewRevision: base + 1, reviewedBy: { username: 'tester' }, reviewedAt: `saved-${base + 1}` }
      server.set(page, saved)
      return saved
    },
  }
  const collaboration = usePageCollaboration({
    client, getJobId: () => result.value.jobId, getCurrentPage: () => currentPage.value,
    getPageData: page => pages.value.find(item => item.page === page),
    switchPage: async page => { currentPage.value = page }, heartbeatIntervalMs: 0,
    onPageReloaded: page => history.clearPage(page),
  })
  const markerStyle = ref({ color: '#f00' })
  history = useWorkspaceHistory({
    result, pages, pageData, markerStyle, componentMarkerStyles: ref({}),
    projectResults: ref([result.value]), activeProjectIndex: ref(0), selectedId: ref(''),
    cloneValue: clone, scheduleSave: () => collaboration.markDirty(currentPage.value),
    showNotice() {}, logAudit() {},
    canEdit: () => collaboration.activeLock.value?.page === currentPage.value
      && !collaboration.readOnly.value && collaboration.saveState.value !== 'saving',
  })
  return { currentPage, result, pages, pageData, server, client, calls, collaboration, history, markerStyle }
}

test('delete page 1, switch page 2, return and undo/redo saves without revision conflict', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(1)
    f.history.begin('delete page 1')
    f.pageData.value.candidates = []
    f.history.commit()
    await f.collaboration.enterPage(2)
    assert.equal(f.server.get(1).reviewRevision, 1)
    assert.equal(f.history.canUndo.value, false)
    f.history.undo()
    assert.deepEqual(f.pages.value[0].candidates, [])
    assert.deepEqual([...f.collaboration.dirtyPages.value], [])
    await f.collaboration.enterPage(1)
    assert.equal(f.history.canUndo.value, true)
    f.history.undo()
    assert.deepEqual(f.pageData.value.candidates, [{ id: 'V1' }])
    assert.equal(f.pageData.value.reviewRevision, 1)
    assert.equal(f.pageData.value.reviewedAt, 'saved-1')
    assert.deepEqual([...f.collaboration.dirtyPages.value], [1])
    await f.collaboration.enterPage(2)
    assert.equal(f.server.get(1).reviewRevision, 2)
    await f.collaboration.enterPage(1)
    assert.equal(f.history.canRedo.value, true)
    f.history.redo()
    assert.equal(f.pageData.value.reviewRevision, 2)
    await f.collaboration.saveCurrentPage()
    assert.equal(f.server.get(1).reviewRevision, 3)
    assert.deepEqual(f.server.get(1).candidates, [])
  } finally { await f.collaboration.release() }
})

test('page 2 edits do not destroy page 1 redo or restore unrelated global styles', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(1)
    f.history.begin('delete')
    f.pageData.value.candidates = []
    f.history.commit()
    f.history.undo()
    await f.collaboration.enterPage(2)
    f.history.begin('page 2 edit')
    f.pageData.value.candidates.push({ id: 'V3' })
    f.history.commit()
    f.markerStyle.value.color = '#0f0'
    await f.collaboration.enterPage(1)
    assert.equal(f.history.canRedo.value, true)
    f.history.redo()
    assert.equal(f.markerStyle.value.color, '#0f0')
    assert.equal(f.pages.value[1].candidates.length, 2)
  } finally { await f.collaboration.release() }
})

test('lost lock prevents undo without consuming history', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(1)
    f.history.begin('delete')
    f.pageData.value.candidates = []
    f.history.commit()
    f.collaboration.readOnly.value = true
    assert.equal(f.history.canUndo.value, false)
    f.history.undo()
    assert.equal(f.history.undoStack.value.length, 1)
    assert.deepEqual(f.pageData.value.candidates, [])
  } finally { await f.collaboration.release() }
})

test('newly acquired lock checks cached page and invalidates stale history on remote update', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(1)
    f.history.begin('delete')
    f.pageData.value.candidates = []
    f.history.commit()
    await f.collaboration.enterPage(2)
    f.server.set(1, { ...f.server.get(1), reviewRevision: 2, candidates: [{ id: 'other-user' }] })
    await f.collaboration.enterPage(1)
    assert.equal(f.pageData.value.reviewRevision, 2)
    assert.deepEqual(f.pageData.value.candidates, [{ id: 'other-user' }])
    assert.equal(f.history.canUndo.value, false)
    assert.equal(f.calls.filter(call => call === 'fetch:1').length, 2)
  } finally { await f.collaboration.release() }
})

test('server sync failure releases newly acquired lock and keeps previous page', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(1)
    const fetch = f.client.getJobPage
    f.client.getJobPage = async (job, page) => { if (page === 2) throw new Error('sync failed'); return fetch(job, page) }
    await assert.rejects(() => f.collaboration.enterPage(2), /sync failed/)
    assert.equal(f.currentPage.value, 1)
    assert.ok(f.calls.includes('release:2'))
    assert.equal(f.collaboration.activeLock.value.page, 1)
  } finally { await f.collaboration.release() }
})

test('updated target with dirty edits refuses reload and preserves local content', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(2)
    f.pages.value[0].candidates = [{ id: 'local-unsaved' }]
    f.collaboration.markDirty(1)
    f.server.set(1, { ...f.server.get(1), reviewRevision: 1 })
    await assert.rejects(() => f.collaboration.enterPage(1), /本地未保存修改已保留/)
    assert.deepEqual(f.pages.value[0].candidates, [{ id: 'local-unsaved' }])
    assert.equal(f.pages.value[0].reviewRevision, 0)
    assert.equal(f.currentPage.value, 2)
  } finally { await f.collaboration.release() }
})

test('manual save and navigation share the same in-flight page save', async () => {
  const f = fixture()
  try {
    await f.collaboration.enterPage(1)
    f.collaboration.markDirty(1)
    const save = f.client.savePage
    let finish
    f.client.savePage = async (...args) => { await new Promise(resolve => { finish = resolve }); return save(...args) }
    const first = f.collaboration.saveCurrentPage()
    const second = f.collaboration.saveCurrentPage()
    assert.equal(first, second)
    await new Promise(resolve => setTimeout(resolve, 0))
    const navigation = f.collaboration.enterPage(2)
    finish()
    await Promise.all([first, second, navigation])
    assert.equal(f.calls.filter(call => call === 'save:1').length, 1)
    assert.equal(f.server.get(1).reviewRevision, 1)
  } finally { await f.collaboration.release() }
})
