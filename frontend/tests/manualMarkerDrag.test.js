import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, ref } from 'vue'
import { useManualMarkerDrag } from '../src/composables/useManualMarkerDrag.js'
import { useCanvasViewport } from '../src/composables/useCanvasViewport.js'

function fixture() {
  const scope = effectScope()
  const inputs = {
    mode: ref('drag'), editing: ref(true), type: ref('weld'),
    pageData: ref({ page: 1, width: 1000, height: 500 }),
    canvasSurface: ref({ getBoundingClientRect: () => ({ left: 20, top: 30, width: 600, height: 300 }) }),
    canvasLayout: ref({ width: 1200, height: 600, target: { x: 200, y: 100, width: 1000, height: 500 } }),
    readOnly: ref(false), regionDeleting: ref(false),
  }
  const additions = []
  const captures = new Set()
  const element = {
    setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id),
    releasePointerCapture: id => captures.delete(id),
  }
  const drag = scope.run(() => useManualMarkerDrag({ ...inputs, addMarker: (...args) => additions.push(args) }))
  const event = (clientX = 220, clientY = 180, extra = {}) => ({
    clientX, clientY, pointerId: 1, button: 0, currentTarget: element, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true }, ...extra,
  })
  return { scope, inputs, drag, additions, captures, event }
}

test('drag creates only on release, using transformed target coordinates and the dragged label position', () => {
  const f = fixture()
  try {
    f.drag.start(f.event())
    f.drag.move(f.event(370, 230))
    assert.deepEqual(f.additions, [])
    assert.deepEqual(f.drag.preview.value, { x1: 400, y1: 300, x2: 700, y2: 400 })
    f.drag.finish(f.event(370, 230))
    assert.deepEqual(f.additions, [[220, 180, { x: 500, y: 300 }]])
    assert.equal(f.drag.preview.value, null)
    assert.equal(f.captures.size, 0)
  } finally { f.scope.stop() }
})

test('clicks, cancelled gestures and a different pointer do not create a marker', () => {
  const f = fixture()
  try {
    f.drag.start(f.event())
    f.drag.finish(f.event(222, 181))
    f.drag.start(f.event())
    f.drag.finish(f.event(400, 200, { pointerId: 2 }))
    assert.notEqual(f.drag.preview.value, null)
    f.drag.cancel()
    assert.deepEqual(f.additions, [])
    assert.equal(f.captures.size, 0)
  } finally { f.scope.stop() }
})

test('gesture must start in the target PDF and release coordinates are clamped to its bounds', () => {
  const f = fixture()
  try {
    f.drag.start(f.event(40, 50))
    assert.equal(f.drag.preview.value, null)
    f.drag.start(f.event())
    f.drag.finish(f.event(1000, -100))
    assert.deepEqual(f.additions, [[220, 180, { x: 1000, y: 0 }]])
  } finally { f.scope.stop() }
})

test('keyboard mode, read-only pages, deletion mode, existing marker drags and middle button skip creation', () => {
  for (const [key, value] of [['mode', 'keyboard'], ['editing', false], ['readOnly', true], ['regionDeleting', true]]) {
    const f = fixture()
    try {
      f.inputs[key].value = value
      f.drag.start(f.event())
      f.drag.finish(f.event(400, 200))
      assert.deepEqual(f.additions, [])
    } finally { f.scope.stop() }
  }
  const f = fixture()
  try {
    f.drag.start(f.event(220, 180, { defaultPrevented: true }))
    f.drag.start(f.event(220, 180, { button: 1 }))
    assert.equal(f.drag.preview.value, null)
  } finally { f.scope.stop() }
})

test('changing page, type, mode or access cancels an unfinished gesture', () => {
  for (const [key, value] of [['pageData', { page: 2, width: 1000, height: 500 }], ['type', 'valve'], ['mode', 'keyboard'], ['editing', false], ['readOnly', true], ['regionDeleting', true]]) {
    const f = fixture()
    try {
      f.drag.start(f.event())
      f.inputs[key].value = value
      f.drag.finish(f.event(400, 200))
      assert.equal(f.drag.preview.value, null)
      assert.equal(f.captures.size, 0)
      assert.deepEqual(f.additions, [])
    } finally { f.scope.stop() }
  }
})

test('drag edit mode blocks left pan, retains middle pan and restores left pan on exit', () => {
  const blocked = ref(true)
  const viewport = useCanvasViewport({ blockLeftPan: () => blocked.value })
  const event = { pointerId: 1, clientX: 10, clientY: 20, currentTarget: {} }
  viewport.startPan({ ...event, button: 0 })
  assert.equal(viewport.panState.value, null)
  viewport.startPan({ ...event, button: 1 })
  viewport.movePan({ ...event, clientX: 40, clientY: 50 })
  assert.deepEqual(viewport.pan.value, { x: 30, y: 30 })
  viewport.endPan(event)
  blocked.value = false
  viewport.startPan({ ...event, button: 0 })
  assert.notEqual(viewport.panState.value, null)
})
