import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, ref, shallowRef } from 'vue'

import { useMarkerPresentation } from './composables/useMarkerPresentation.js'

function createPresentation(shape = 'rectangle') {
  const page = { page: 1, width: 1000, height: 500, candidates: [] }
  return useMarkerPresentation({
    markerStyle: ref({ shape, frameSize: 30, fontSize: 15, lineWidth: 2, color: '#d4143c', fillOpacity: .5 }),
    componentMarkerStyles: ref({ support: {} }),
    selectedId: ref('w1'),
    editingId: ref(''),
    swapSourceId: ref(''),
    labelDragState: shallowRef(null),
    canvasLayout: ref({ target: { x: 10, y: 20, width: 500, height: 250 } }),
    pages: ref([page]),
    pageData: computed(() => page),
  })
}

test('marker dimensions scale with the rendered page', () => {
  const presentation = createPresentation()
  const dimensions = presentation.markerDimensions({ id: 'w1', page: 1, number: 'W12' })
  assert.equal(dimensions.scale, .5)
  assert.equal(dimensions.height, 15)
  assert.ok(dimensions.width > dimensions.height)
})

test('leader ends at the marker boundary instead of its center', () => {
  const presentation = createPresentation('rectangle')
  const item = { id: 'w1', page: 1, number: '1', xNorm: .2, yNorm: .5, labelXNorm: .8, labelYNorm: .5 }
  const anchor = presentation.anchorPoint(item)
  const end = presentation.leaderEnd(item)
  const centerX = 10 + .8 * 500
  assert.equal(anchor.x, 110)
  assert.ok(end.x < centerX)
  assert.equal(end.y, 145)
})

test('editing marker receives a foreground state', () => {
  const presentation = createPresentation()
  const editingId = ref('w1')
  const page = { page: 1, width: 1000, height: 500, candidates: [] }
  const editablePresentation = useMarkerPresentation({
    markerStyle: ref({ shape: 'circle', frameSize: 30, fontSize: 15 }),
    componentMarkerStyles: ref({ support: {} }),
    selectedId: ref('w1'),
    editingId,
    swapSourceId: ref(''),
    labelDragState: shallowRef(null),
    canvasLayout: ref({ target: { x: 0, y: 0, width: 1000, height: 500 } }),
    pages: ref([page]),
    pageData: computed(() => page),
  })
  assert.equal(presentation.markerClass({ id: 'w1' }).editing, false)
  assert.equal(editablePresentation.markerClass({ id: 'w1' }).editing, true)
})

test('special marker uses its own appearance instead of the global weld appearance', () => {
  const presentation = createPresentation('circle')
  const style = presentation.candidateMarkerStyle({
    componentKind: 'special-marker', componentType: 'special',
    markerStyle: { shape: 'diamond', frameSize: 44, fontSize: 20, color: '#123456' },
  })
  assert.equal(style.shape, 'diamond')
  assert.equal(style.frameSize, 44)
  assert.equal(style.fontSize, 20)
  assert.equal(style.color, '#123456')
})

test('group drag handle is centered between the leader endpoint and label center', async () => {
  const presentation = await import('./composables/useMarkerPresentation.js')
  assert.equal(typeof presentation.midpoint, 'function')
  assert.deepEqual(presentation.midpoint({ x: 20, y: 40 }, { x: 80, y: 100 }), { x: 50, y: 70 })
})

test('group drag shifts anchor and label together while keeping both inside the page', async () => {
  const presentation = await import('./composables/useMarkerPresentation.js')
  assert.equal(typeof presentation.translateMarkerPair, 'function')
  assert.deepEqual(
    presentation.translateMarkerPair({ xNorm: .2, yNorm: .3, labelXNorm: .7, labelYNorm: .8 }, .1, -.15),
    { xNorm: .3, yNorm: .15, labelXNorm: .8, labelYNorm: .65 },
  )
  assert.deepEqual(
    presentation.translateMarkerPair({ xNorm: .1, yNorm: .2, labelXNorm: .8, labelYNorm: .9 }, .5, .5),
    { xNorm: .3, yNorm: .3, labelXNorm: 1, labelYNorm: 1 },
  )
})

test('canvas exposes a midpoint handle wired to group drag events', async () => {
  const { readFile } = await import('node:fs/promises')
  const source = await readFile(new URL('./App.vue', import.meta.url), 'utf8')
  assert.match(source, /class="marker-group-handle"/)
  assert.match(source, /@pointerdown="startGroupDrag\(\$event, item\)"/)
  assert.match(source, /@pointermove="moveGroup\(\$event, item\)"/)
  assert.match(source, /@pointerup="endGroupDrag\(\$event, item\)"/)
  assert.match(source, /'marker-group-handle--temporarily-hidden': labelDragState\?\.id === item\.id \|\| anchorDragState\?\.id === item\.id \|\| groupDragState\?\.id === item\.id/)
  assert.doesNotMatch(source, /manualAddMode && !labelDragState && !anchorDragState/)
  assert.match(source, /candidateMatchesModificationType\(candidate\)/)
  assert.match(source, /result && pageData && manualAddMode \? pageData\.candidates\.filter/)
  assert.doesNotMatch(source, /candidate\.origin === 'manual' \|\| \(manualAddMode/)
  assert.match(source, /特殊标识独立外观/)
})
