import assert from 'node:assert/strict'
import test from 'node:test'
import { useBatchMarkerEditor } from '../src/composables/useBatchMarkerEditor.js'

const style = { shape: 'circle', color: '#d4143c', frameSize: 28, fontSize: 15, lineWidth: 1.35, fillOpacity: 0 }
function fixture(options = {}) {
  const pages = [1, 2].map(page => ({ page, reviewRevision: 0, candidates: Array.from({ length: 60 }, (_, index) => ({ id: `p${page}-${index}`, number: `W${page}-${index}` })) }))
  let applying
  const editor = useBatchMarkerEditor({
    getJobId: () => 'task', getPages: () => pages, getPage: options.getPage || (async (_job, page) => ({ page: pages[page - 1] })),
    styleFor: () => style, prepare: async () => {},
    apply: async (_job, changes) => {
      applying = changes
      return options.response || { saved: [...new Set(changes.map(change => change.page))].map(page => ({ page, revision: 1, ids: changes.filter(change => change.page === page).map(change => change.id) })), errors: [], warnings: [] }
    },
  })
  return { editor, pages, applying: () => applying }
}

test('table renders one page, selection crosses table pages and numbering follows stable drawing order', async () => {
  const { editor } = fixture()
  await editor.show()
  assert.equal(editor.rows.value.length, 120)
  assert.equal(editor.visibleRows.value.length, 15)
  assert.equal(editor.pageCount.value, 8)
  editor.tablePage.value = 2
  assert.equal(editor.visibleRows.value[0].id, 'p1-15')
  editor.tablePage.value = 1
  editor.pageFilter.value = 2
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 60)
  editor.numberSelected({ prefix: 'FL-', start: 7, step: 2, suffix: '-A' })
  assert.equal(editor.changedRows.value.length, 60)
  assert.equal(editor.rows.value[60].values.number, 'FL-7-A')
  assert.equal(editor.rows.value[119].values.number, 'FL-125-A')
  assert.equal(editor.rows.value[0].values.number, 'W1-0')
  editor.restoreChanges()
  assert.equal(editor.changedRows.value.length, 0)
  editor.close()
  assert.equal(editor.rows.value.length, 0)
})

test('header selection applies to all filtered rows across table pages and preserves other selections', async () => {
  const { editor } = fixture()
  await editor.show()
  editor.tablePage.value = 2
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 120)
  editor.toggleFiltered(false)
  assert.equal(editor.selectedCount.value, 0)
  editor.toggleRow(editor.rows.value[0], true)
  editor.pageFilter.value = 2
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 61)
  editor.toggleFiltered(false)
  assert.equal(editor.selectedCount.value, 1)
  editor.search.value = 'W2-1'
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 12)
  editor.toggleFiltered(false)
  assert.equal(editor.selectedCount.value, 1)
  editor.typeFilter.value = 'no-matching-type'
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 1)
  editor.typeFilter.value = 'all'
  editor.saving.value = true
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 1)
})

test('page ranges combine with type and search filters and reject invalid bounds before selection', async () => {
  const { editor, pages } = fixture()
  pages[1].candidates[0].componentType = 'valve'
  await editor.show()
  editor.pageFilter.value = 'range'
  assert.equal(editor.filteredRows.value.length, 120)
  editor.pageRangeStart.value = '2'
  editor.pageRangeEnd.value = '2'
  assert.equal(editor.filteredRows.value.length, 60)
  editor.typeFilter.value = 'valve'
  assert.equal(editor.filteredRows.value.length, 1)
  editor.search.value = 'W2-0'
  editor.toggleFiltered(true)
  assert.equal(editor.selectedCount.value, 1)
  editor.toggleFiltered(false)
  for (const [start, end] of [['', 2], [1.5, 2], [0, 2], [1, 3], [2, 1], [null, 2]]) {
    editor.pageRangeStart.value = start; editor.pageRangeEnd.value = end
    assert.ok(editor.pageRangeError.value)
    assert.equal(editor.filteredRows.value.length, 0)
    editor.toggleFiltered(true)
    assert.equal(editor.selectedCount.value, 0)
  }
  editor.pageFilter.value = 'all'
  assert.equal(editor.pageRangeError.value, '')
  assert.equal(editor.filteredRows.value.length, 1)
  await editor.load()
  assert.equal(editor.pageRangeStart.value, 1)
  assert.equal(editor.pageRangeEnd.value, 2)
  assert.equal(editor.pageFilter.value, 'all')
})

test('uniform appearance changes only requested fields of selected markers', async () => {
  const { editor } = fixture()
  await editor.show()
  editor.toggleRow(editor.rows.value[0], true)
  editor.setSelectedFields({ color: '#123456', fontSize: '', fillOpacity: 0.5 })
  assert.equal(editor.changedRows.value.length, 1)
  assert.equal(editor.rows.value[0].values.fontSize, 15)
  assert.equal(editor.rows.value[0].values.color, '#123456')
  assert.equal(editor.rows.value[1].values.color, '#d4143c')
})

test('partial save accepts successful rows, advances the page revision and retains failed rows for retry', async () => {
  const { editor } = fixture({ response: { saved: [{ page: 1, revision: 2, ids: ['p1-0'] }], errors: [{ page: 2, message: 'locked' }], warnings: [] } })
  await editor.show()
  editor.updateRow(editor.rows.value[0], 'number', ' NEW1 ')
  editor.updateRow(editor.rows.value[60], 'number', 'NEW2')
  await editor.save()
  assert.equal(editor.rows.value[0].values.number, 'NEW1')
  assert.equal(editor.rows.value[0].changed, false)
  assert.equal(editor.rows.value[1].revision, 2)
  assert.equal(editor.changedRows.value.length, 1)
  assert.equal(editor.rows.value[60].values.number, 'NEW2')
  assert.equal(editor.rows.value[60].changed, true)
  assert.equal(editor.rows.value[60].error, 'locked')
})

test('invalid values do not invoke the save operation', async () => {
  const f = fixture()
  await f.editor.show()
  f.editor.updateRow(f.editor.rows.value[0], 'fontSize', '')
  await f.editor.save()
  assert.equal(f.applying(), undefined)
  assert.match(f.editor.messages.value[0], /字号/)
})

test('closing during loading ignores late responses and releases the compact rows', async () => {
  const completions = []
  const f = fixture({ getPage: async (_job, page) => new Promise(resolve => { completions.push({ page, resolve }) }) })
  const loading = f.editor.show()
  await new Promise(resolve => setImmediate(resolve))
  f.editor.close()
  completions.forEach(({ page, resolve }) => resolve({ page: f.pages[page - 1] }))
  await loading
  assert.equal(f.editor.open.value, false)
  assert.equal(f.editor.rows.value.length, 0)
})
