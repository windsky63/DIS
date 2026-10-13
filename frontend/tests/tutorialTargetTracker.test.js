import assert from 'node:assert/strict'
import test from 'node:test'
import { createTutorialTargetTracker } from '../src/tutorialTargetTracker.js'

function fixture() {
  let nextId = 0, ready = true, animation = false
  let rect = { left: 100, top: 80, width: 400, height: 300 }
  const callbacks = new Map(), published = []
  const ancestor = { getAnimations: () => animation ? [{ playState: 'running', effect: { getComputedTiming: () => ({ iterations: 1 }) } }] : [] }
  let target = { parentElement: ancestor, getBoundingClientRect: () => rect }
  const tracker = createTutorialTargetTracker({
    findTarget: () => target, isReady: () => ready, onTarget: value => published.push(value),
    requestFrame: callback => { callbacks.set(++nextId, callback); return nextId },
    cancelFrame: id => callbacks.delete(id),
  })
  return { tracker, published, callbacks,
    tick(time) { const pending = [...callbacks.values()]; callbacks.clear(); pending.forEach(callback => callback(time)) },
    ready(value) { ready = value }, animate(value) { animation = value },
    rect(value) { rect = value }, target(value) { target = value },
  }
}

test('dialog bounds are published only after ancestor opening animation and geometry settle', () => {
  const f = fixture()
  f.animate(true); f.tracker.start()
  f.tick(0); f.tick(200); f.tick(400)
  assert.equal(f.published.filter(Boolean).length, 0)
  f.animate(false); f.tick(450); f.tick(550)
  assert.equal(f.published.filter(Boolean).length, 0)
  f.tick(580)
  assert.equal(f.published.at(-1).rect.width, 400)
  f.tracker.stop()
})

test('stable bounds do not become ready before asynchronous content finishes loading', () => {
  const f = fixture()
  f.ready(false); f.tracker.start(); f.tick(0); f.tick(4000)
  assert.equal(f.published.filter(Boolean).length, 0)
  f.ready(true); f.tick(4100); f.tick(4250)
  assert.equal(f.published.at(-1).rect.height, 300)
  f.tracker.stop()
})

test('late lazy targets are still located after the old two-second timeout', () => {
  const f = fixture()
  const target = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 100 }) }
  f.target(null); f.tracker.start(); f.tick(0); f.tick(5000)
  f.target(target); f.tick(5100); f.tick(5250)
  assert.equal(f.published.at(-1).target, target)
  f.tracker.stop()
})

test('content resize or scroll discards stale focus until the new position is stable', () => {
  const f = fixture()
  f.tracker.start(); f.tick(0); f.tick(130)
  const count = f.published.length
  f.tick(200)
  assert.equal(f.published.length, count, 'stationary frames must not write reactive state')
  f.rect({ left: 100, top: 45, width: 400, height: 500 }); f.tick(220)
  assert.equal(f.published.at(-1), null)
  f.tick(350)
  assert.equal(f.published.at(-1).rect.top, 45)
  assert.equal(f.published.at(-1).rect.height, 500)
  f.tracker.stop()
})

test('closing and changing steps cancel old animation callbacks', () => {
  const f = fixture()
  f.tracker.start()
  const oldCallback = [...f.callbacks.values()][0]
  f.tracker.stop()
  const count = f.published.length
  oldCallback(1000)
  assert.equal(f.published.length, count)
  assert.equal(f.callbacks.size, 0)
  f.tracker.start(); oldCallback(2000); f.tick(2100); f.tick(2250)
  assert.equal(f.published.filter(Boolean).length, 1)
  f.tracker.stop()
})

test('infinite decorative animations do not prevent target readiness', () => {
  const f = fixture()
  f.target({ getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 100 }),
    getAnimations: () => [{ playState: 'running', effect: { getComputedTiming: () => ({ iterations: Infinity }) } }] })
  f.tracker.start(); f.tick(0); f.tick(130)
  assert.equal(f.published.filter(Boolean).length, 1)
  f.tracker.stop()
})
