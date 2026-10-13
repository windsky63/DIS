import assert from 'node:assert/strict'
import test from 'node:test'
import { createPageSaver } from '../src/pageSaveRequest.js'

const page = () => ({ page: 1, candidates: [{ id: 'w1', number: '1' }] })
const failed = () => Object.assign(new Error('connection reset'), { outcomeUnknown: true })
const receipt = (id = 'request-1', revision = 1) => ({ page: 1, reviewRevision: revision, reviewSaveRequestId: id, reviewedAt: 'now' })
test('lost save response is confirmed by its exact server receipt without a second PUT', async () => {
  let writes = 0
  const saver = createPageSaver({ makeId: () => 'request-1',
    request: async (_url, options) => { writes++; assert.equal(JSON.parse(options.body).saveRequestId, 'request-1'); throw failed() },
    getPage: async () => ({ page: receipt() }),
  })
  const result = await saver('job', 1, page(), 0, 'tab', 'lock')
  assert.equal(result.reviewRevision, 1)
  assert.equal(writes, 1)
})
test('offline confirmation preserves the pending request and checks it before any later retry', async () => {
  let offline = true, writes = 0
  const saver = createPageSaver({ makeId: () => 'request-1', request: async () => { writes++; throw failed() },
    getPage: async () => { if (offline) throw new Error('offline'); return { page: receipt() } },
  })
  await assert.rejects(saver('job', 1, page(), 0, 'tab', 'lock'), error => error.code === 'SAVE_OUTCOME_UNKNOWN')
  offline = false
  assert.equal((await saver('job', 1, page(), 0, 'tab', 'lock')).reviewRevision, 1)
  assert.equal(writes, 1)
})
test('another page or operation receipt cannot be mistaken for this save', async () => {
  for (const latest of [receipt('someone-else'), receipt('request-1', 2), { ...receipt(), page: 2 }]) {
    const saver = createPageSaver({ makeId: () => 'request-1', request: async () => { throw failed() }, getPage: async () => ({ page: latest }) })
    await assert.rejects(saver('job', 1, page(), 0, 'tab', 'lock'), error => error.outcomeUnknown)
  }
})
test('changed edits are retained when confirming an earlier save and are never silently marked saved', async () => {
  let latest = { page: 1, reviewRevision: 0 }, writes = 0
  const saver = createPageSaver({ makeId: () => 'request-1', request: async () => { writes++; throw failed() }, getPage: async () => ({ page: latest }) })
  await assert.rejects(saver('job', 1, page(), 0, 'tab', 'lock'))
  latest = receipt()
  const changed = page(); changed.candidates[0].number = '2'
  await assert.rejects(saver('job', 1, changed, 0, 'tab', 'lock'), error => error.code === 'SAVE_CONTENT_CHANGED' && error.confirmedSave.reviewRevision === 1)
  assert.equal(changed.candidates[0].number, '2')
  assert.equal(writes, 1)
})
test('an explicit manual retry uses the same ID and reconciles a late first save instead of duplicating it', async () => {
  let reads = 0, writes = 0
  const ids = []
  const saver = createPageSaver({ makeId: () => 'request-1',
    request: async (_url, options) => { writes++; ids.push(JSON.parse(options.body).saveRequestId); if (writes === 1) throw failed(); throw Object.assign(new Error('already saved'), { status: 409 }) },
    getPage: async () => ({ page: ++reads >= 3 ? receipt() : { page: 1, reviewRevision: 0 } }),
  })
  await assert.rejects(saver('job', 1, page(), 0, 'tab', 'lock'))
  assert.equal((await saver('job', 1, page(), 0, 'tab', 'lock')).reviewRevision, 1)
  assert.deepEqual(ids, ['request-1', 'request-1'])
})

test('a late old write cannot confirm newer content submitted by a deliberate manual save', async () => {
  let writes = 0, reads = 0, nextId = 0
  const saver = createPageSaver({ makeId: () => `request-${++nextId}`,
    request: async () => { if (++writes === 1) throw failed(); throw Object.assign(new Error('revision conflict'), { status: 409 }) },
    getPage: async () => ({ page: ++reads >= 3 ? receipt('request-1') : { page: 1, reviewRevision: 0 } }),
  })
  await assert.rejects(saver('job', 1, page(), 0, 'tab', 'lock'))
  const newer = page(); newer.candidates[0].number = '2'
  await assert.rejects(saver('job', 1, newer, 0, 'tab', 'lock'), error => error.code === 'SAVE_CONTENT_CHANGED' && error.confirmedSave.reviewRevision === 1)
  assert.equal(writes, 2)
  assert.equal(newer.candidates[0].number, '2')
})

test('the submitted content remains frozen while a previous save is being verified', async () => {
  let finish, reads = 0, writes = 0, sent
  const gate = new Promise(resolve => { finish = resolve })
  const saver = createPageSaver({ makeId: () => 'request-1',
    getPage: async () => ++reads === 2 ? gate : { page: { page: 1, reviewRevision: 0 } },
    request: async (_url, options) => { if (++writes === 1) throw failed(); sent = JSON.parse(options.body); return { saved: true, reviewRevision: 1 } },
  })
  const data = page()
  await assert.rejects(saver('job', 1, data, 0, 'tab', 'lock'))
  const pending = saver('job', 1, data, 0, 'tab', 'lock')
  data.candidates[0].number = 'edited-during-verification'
  finish({ page: { page: 1, reviewRevision: 0 } }); await pending
  assert.equal(sent.page.candidates[0].number, '1')
  assert.equal(data.candidates[0].number, 'edited-during-verification')
})
