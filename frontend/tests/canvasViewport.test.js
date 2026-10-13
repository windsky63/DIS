import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useCanvasViewport } from '../src/composables/useCanvasViewport.js'

function fixture() {
  const pageSteps = []
  const captured = []
  const released = []
  const viewport = {
    clientWidth: 800,
    clientHeight: 600,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }),
    setPointerCapture: id => captured.push(id),
    releasePointerCapture: id => released.push(id),
  }
  const state = useCanvasViewport({
    canvasLayout: ref({ width: 1000, height: 500 }),
    canvasViewport: ref(viewport),
    onPageStep: direction => pageSteps.push(direction),
  })
  return { state, viewport, pageSteps, captured, released }
}

test('plain wheel changes pages while Ctrl+wheel zooms at the pointer', () => {
  const f = fixture()
  const initialZoom = f.state.zoom.value
  f.state.onWheel({ deltaY: 100, clientX: 400, clientY: 300, ctrlKey: false, timeStamp: 1000 })
  assert.deepEqual(f.pageSteps, [1])
  assert.equal(f.state.zoom.value, initialZoom)

  f.state.onWheel({ deltaY: -100, clientX: 300, clientY: 200, ctrlKey: true, timeStamp: 1200 })
  assert.deepEqual(f.pageSteps, [1])
  assert.ok(f.state.zoom.value > initialZoom)
})

test('wheel page navigation emits at most once per wheel gesture interval', () => {
  const f = fixture()
  f.state.onWheel({ deltaY: 80, clientX: 0, clientY: 0, ctrlKey: false, timeStamp: 1000 })
  f.state.onWheel({ deltaY: 60, clientX: 0, clientY: 0, ctrlKey: false, timeStamp: 1050 })
  f.state.onWheel({ deltaY: -80, clientX: 0, clientY: 0, ctrlKey: false, timeStamp: 1250 })
  assert.deepEqual(f.pageSteps, [1, -1])
})

test('left-button blank drag pans and suppresses only its following click', () => {
  const f = fixture()
  const target = f.viewport
  f.state.startPan({ button: 0, pointerId: 7, clientX: 100, clientY: 100, currentTarget: target, defaultPrevented: false })
  f.state.movePan({ pointerId: 7, clientX: 130, clientY: 115 })
  f.state.endPan({ pointerId: 7, currentTarget: target })
  assert.deepEqual(f.state.pan.value, { x: 30, y: 15 })
  assert.equal(f.state.consumePanClick(), true)
  assert.equal(f.state.consumePanClick(), false)

  f.state.startPan({ button: 0, pointerId: 8, clientX: 200, clientY: 200, currentTarget: target, defaultPrevented: false })
  f.state.endPan({ pointerId: 8, currentTarget: target })
  assert.equal(f.state.consumePanClick(), false)
})
