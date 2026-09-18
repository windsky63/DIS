import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, effectScope, nextTick, ref } from 'vue'
import { useRegionDeletion } from '../src/composables/useRegionDeletion.js'
import { useWorkspaceHistory } from '../src/composables/useWorkspaceHistory.js'

function fixture() {
  const scope = effectScope()
  return scope.run(() => {
    const result = ref({ pages: [{ page: 1, width: 1000, height: 500, candidates: [
      { id: 'weld', xNorm: .2, yNorm: .2, labelXNorm: .9, labelYNorm: .9 },
      { id: 'valve', componentType: 'valve', x: 300, y: 150 },
      { id: 'special', specialMarker: true, xNorm: .4, yNorm: .4 },
      { id: 'outside', xNorm: .8, yNorm: .8, labelXNorm: .3, labelYNorm: .3 },
    ], candidateCount: 4 }, { page: 2, candidates: [{ id: 'other-page', xNorm: .3, yNorm: .3 }] }] })
    const pages = computed(() => result.value.pages)
    const pageData = computed(() => pages.value[0])
    const selectedId = ref('valve')
    const swapSourceId = ref('weld')
    const readOnly = ref(false)
    const manualAddMode = ref(true)
    const manualAddType = ref('all')
    const audits = []
    let saves = 0
    const history = useWorkspaceHistory({
      result, pages, pageData, selectedId, markerStyle: ref({}), componentMarkerStyles: ref({}),
      projectResults: ref([result.value]), activeProjectIndex: ref(0),
      cloneValue: value => JSON.parse(JSON.stringify(value)), scheduleSave: () => { saves++ },
      showNotice: () => {}, logAudit: () => {},
    })
    const canvasLayout = ref({ width: 1200, height: 700, target: { x: 100, y: 100, width: 1000, height: 500 } })
    const canvasSurface = ref({ getBoundingClientRect: () => ({ left: 30, top: 40, width: 600, height: 350 }) })
    const region = useRegionDeletion({
      pageData, canvasLayout, canvasSurface, readOnly, manualAddMode, manualAddType, selectedId, swapSourceId, editingId: ref(''),
      operations: { beginHistory: history.begin, commitHistory: history.commit, showNotice: () => {}, logAudit: (...args) => audits.push(args) },
    })
    const element = { setPointerCapture: () => {}, hasPointerCapture: () => false }
    const pointer = (x, y, pointerId = 1) => ({
      button: 0, pointerId, clientX: 30 + (100 + x * 1000) / 2, clientY: 40 + (100 + y * 500) / 2,
      currentTarget: element, preventDefault() {}, stopPropagation() {},
    })
    return { region, result, pageData, readOnly, manualAddMode, manualAddType, selectedId, swapSourceId, history, audits, pointer, scope, get saves() { return saves } }
  })
}

test('region deletion uses anchor points across marker types and records one undoable page edit', () => {
  const f = fixture()
  try {
    f.region.toggle()
    assert.equal(f.manualAddMode.value, true)
    f.region.start(f.pointer(.1, .1))
    f.region.finish(f.pointer(.5, .5))
    assert.deepEqual(f.pageData.value.candidates.map(item => item.id), ['outside'])
    assert.equal(f.pageData.value.candidateCount, 1)
    assert.equal(f.selectedId.value, '')
    assert.equal(f.swapSourceId.value, '')
    assert.equal(f.result.value.pages[1].candidates.length, 1)
    assert.equal(f.saves, 1)
    assert.equal(f.audits[0][0], 'marker.region_deleted')
    f.history.undo()
    assert.equal(f.pageData.value.candidates.length, 4)
    f.history.redo()
    assert.deepEqual(f.pageData.value.candidates.map(item => item.id), ['outside'])
  } finally { f.scope.stop() }
})

test('reverse drags normalize the rectangle with transformed canvas offsets', () => {
  const f = fixture()
  try {
    f.region.toggle()
    f.region.start(f.pointer(.5, .5))
    f.region.move(f.pointer(.1, .1))
    assert.deepEqual(f.region.rectangleStyle.value, { left: '200px', top: '150px', width: '400px', height: '200px' })
    f.region.finish(f.pointer(.1, .1))
    assert.equal(f.pageData.value.candidates.length, 1)
  } finally { f.scope.stop() }
})

for (const [type, removedId] of [['weld', 'weld'], ['valve', 'valve'], ['flange', 'flange'], ['support', 'support']]) {
  test(`region deletion in ${type} mode only deletes that type`, () => {
    const f = fixture()
    try {
      f.pageData.value.candidates.push(
        { id: 'flange', componentType: 'flange', xNorm: .3, yNorm: .3 },
        { id: 'support', componentType: 'support', xNorm: .3, yNorm: .3 },
      )
      f.manualAddType.value = type
      f.region.toggle()
      assert.equal(f.manualAddMode.value, true)
      f.region.start(f.pointer(.1, .1)); f.region.finish(f.pointer(.5, .5))
      assert.deepEqual(f.audits[0][1].ids, [removedId])
      assert.ok(f.pageData.value.candidates.some(item => item.id === 'special'))
      f.history.undo()
      assert.ok(f.pageData.value.candidates.some(item => item.id === removedId))
    } finally { f.scope.stop() }
  })
}

test('default mode deletes all types regardless of last selected modification type', () => {
  const f = fixture()
  try {
    f.manualAddMode.value = false
    f.manualAddType.value = 'valve'
    f.region.toggle()
    f.region.start(f.pointer(.1, .1)); f.region.finish(f.pointer(.5, .5))
    assert.deepEqual(f.pageData.value.candidates.map(item => item.id), ['outside'])
  } finally { f.scope.stop() }
})

test('switching modification type during a drag cancels the pending rectangle', () => {
  const f = fixture()
  try {
    f.region.toggle(); f.region.start(f.pointer(.1, .1))
    f.manualAddType.value = 'valve'
    f.region.finish(f.pointer(.5, .5))
    assert.equal(f.pageData.value.candidates.length, 4)
  } finally { f.scope.stop() }
})

test('clicks, pointer cancellation, and drags starting outside the drawing do not delete', () => {
  const f = fixture()
  try {
    f.region.toggle()
    f.region.start(f.pointer(.2, .2)); f.region.finish(f.pointer(.2, .2))
    f.region.start(f.pointer(-.1, .2)); f.region.finish(f.pointer(.5, .5))
    f.region.start(f.pointer(.1, .1)); f.region.move(f.pointer(.5, .5)); f.region.cancelSelection()
    f.region.finish(f.pointer(.5, .5))
    assert.equal(f.pageData.value.candidates.length, 4)
    assert.equal(f.saves, 0)
  } finally { f.scope.stop() }
})

test('losing the page lock cancels the operation, including before the watcher runs', async () => {
  const f = fixture()
  try {
    f.region.toggle(); f.region.start(f.pointer(.1, .1))
    f.readOnly.value = true
    f.region.finish(f.pointer(.5, .5))
    await nextTick()
    assert.equal(f.region.active.value, false)
    assert.equal(f.pageData.value.candidates.length, 4)
    f.region.toggle()
    assert.equal(f.region.active.value, false)
  } finally { f.scope.stop() }
})

test('changing the page during a drag cannot delete from either page', async () => {
  const f = fixture()
  try {
    f.region.toggle(); f.region.start(f.pointer(.1, .1))
    f.result.value.pages.reverse()
    f.region.finish(f.pointer(.5, .5))
    await nextTick()
    assert.equal(f.region.active.value, false)
    assert.equal(f.result.value.pages[1].candidates.length, 4)
    assert.equal(f.saves, 0)
  } finally { f.scope.stop() }
})
