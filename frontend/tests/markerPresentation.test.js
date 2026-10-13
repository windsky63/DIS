import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { useMarkerPresentation } from '../src/composables/useMarkerPresentation.js'
import { firstInheritedMarkerStyle } from '../src/markerSettingsScope.js'
import { markerGeometry } from '../src/markerGeometry.js'

function fixture(pages, pageData) {
  return useMarkerPresentation({
    markerStyle: ref({ shape: 'circle', frameSize: 28, fontSize: 12 }), componentMarkerStyles: ref({}),
    selectedId: ref(''), editingId: ref(''), swapSourceId: ref(''), labelDragState: ref(null),
    canvasLayout: ref({ target: { x: 0, y: 34, width: 1000, height: 500 } }), pages, pageData,
  })
}

test('current-page marker sizing does not scan the task page list', () => {
  const pages = { get value() { throw new Error('unnecessary full page scan') } }
  const presentation = fixture(pages, ref({ page: 250, width: 1000 }))
  const item = { page: 250, number: '1', xNorm: .2, yNorm: .2, labelXNorm: .4, labelYNorm: .4 }
  const expected = markerGeometry(item, { shape: 'circle', fontSize: 12 })
  assert.equal(presentation.markerDimensions(item).width, expected.width)
  assert.equal(presentation.labelPosition(item).width, `${expected.width}px`)
  const end = presentation.leaderEnd(item)
  assert.ok(end.x < 400 && end.y < 234)
})

test('markers from another page and changing page sizes retain the correct drawing scale', () => {
  const pages = ref([{ page: 1, width: 1000 }, { page: 2, width: 2000 }])
  const pageData = ref(pages.value[0])
  const presentation = fixture(pages, pageData)
  const width = markerGeometry({ number: '1' }, { shape: 'circle', fontSize: 12 }).width
  assert.equal(presentation.markerDimensions({ page: 1, number: '1' }).width, width)
  assert.equal(presentation.markerDimensions({ page: 2, number: '1' }).width, width / 2)
  pageData.value = pages.value[1]
  assert.equal(presentation.markerDimensions({ page: 2, number: '1' }).width, width / 2)
})

test('batch appearance overrides affect only that marker and preserve legacy task-wide appearance behavior', () => {
  const presentation = fixture(ref([{ page: 1, width: 1000 }]), ref({ page: 1, width: 1000 }))
  const ordinary = { page: 1, number: '1', markerStyle: { shape: 'rectangle', frameSize: 40 } }
  const overridden = { ...ordinary, markerStyleOverride: true, markerStyle: { shape: 'diamond', frameSize: 40, color: '#123456' } }
  assert.equal(presentation.candidateMarkerStyle(ordinary).shape, 'circle')
  assert.equal(presentation.candidateMarkerStyle(overridden).shape, 'diamond')
  assert.equal(presentation.candidateMarkerStyle(overridden).color, '#123456')
  assert.equal(presentation.markerDimensions(overridden).width, markerGeometry(overridden, { shape: 'diamond', frameSize: 40, fontSize: 12 }).width)
  assert.equal(presentation.markerDimensions(ordinary).width, markerGeometry(ordinary, { shape: 'circle', fontSize: 12 }).width)
})

test('restoring a task never promotes an individual batch appearance to the whole marker category', () => {
  const override = { shape: 'diamond', color: '#123456' }
  const inherited = { shape: 'circle', color: '#d4143c' }
  const task = { pages: [{ candidates: [
    { markerStyle: override, markerStyleOverride: true }, { markerStyle: inherited },
    { componentType: 'valve', markerStyle: override, markerStyleOverride: true },
    { componentType: 'valve', markerStyle: inherited },
  ] }] }
  assert.equal(firstInheritedMarkerStyle(task), inherited)
  assert.equal(firstInheritedMarkerStyle(task, 'valve'), inherited)
  assert.equal(firstInheritedMarkerStyle({ pages: [{ candidates: [{ markerStyle: override, markerStyleOverride: true }] }] }), null)
})
