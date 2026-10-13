import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, effectScope, ref } from 'vue'
import { useWorkspaceHistory } from '../src/composables/useWorkspaceHistory.js'

function fixture() {
  const scope = effectScope()
  const result = ref({ jobId: 'task', pages: [{ page: 1, candidates: [{ id: 'w1', number: '1' }],
    layoutObstacles: { textRects: Array.from({ length: 10000 }, (_, index) => [index, 0, index + 1, 1]) },
  }] })
  const pages = computed(() => result.value.pages)
  const pageData = computed(() => pages.value[0])
  let geometryClones = 0
  const history = scope.run(() => useWorkspaceHistory({
    result, pages, pageData, markerStyle: ref({}), componentMarkerStyles: ref({}),
    projectResults: ref([result.value]), activeProjectIndex: ref(0), selectedId: ref(''),
    cloneValue(value) {
      if (value?.layoutObstacles) geometryClones += 1
      return JSON.parse(JSON.stringify(value))
    },
    scheduleSave() {}, showNotice() {}, logAudit() {},
  }))
  return { scope, pageData, history, geometryClones: () => geometryClones }
}

test('30 undo entries do not clone or retain duplicate PDF obstacle arrays', () => {
  const f = fixture()
  try {
    for (let edit = 1; edit <= 30; edit += 1) {
      f.history.begin('number edit')
      f.pageData.value.candidates[0].number = String(edit + 1)
      f.history.commit()
    }
    assert.equal(f.history.undoStack.value.length, 30)
    assert.equal(f.geometryClones(), 0)
    for (const snapshot of f.history.undoStack.value) assert.equal(Object.hasOwn(snapshot.value, 'layoutObstacles'), false)
    assert.ok(JSON.stringify(f.history.undoStack.value).length < JSON.stringify(f.pageData.value.layoutObstacles).length)
  } finally { f.scope.stop() }
})

test('undo and redo preserve the currently loaded PDF geometry for layout optimization', () => {
  const f = fixture()
  try {
    f.history.begin('edit')
    f.pageData.value.candidates[0].number = '2'
    f.history.commit()
    f.pageData.value.layoutObstacles = { textRects: [[20, 30, 40, 50]], processSegments: [] }
    const obstacles = f.pageData.value.layoutObstacles
    f.history.undo()
    assert.equal(f.pageData.value.candidates[0].number, '1')
    assert.equal(f.pageData.value.layoutObstacles, obstacles)
    f.history.redo()
    assert.equal(f.pageData.value.candidates[0].number, '2')
    assert.equal(f.pageData.value.layoutObstacles, obstacles)
    assert.equal(f.geometryClones(), 0)
  } finally { f.scope.stop() }
})

test('PDF geometry hydration alone does not create an edit history entry', () => {
  const f = fixture()
  try {
    f.history.begin('unchanged marker')
    f.pageData.value.layoutObstacles = { textRects: [[1, 2, 3, 4]] }
    f.history.commit()
    assert.equal(f.history.canUndo.value, false)
  } finally { f.scope.stop() }
})
