import assert from 'node:assert/strict'
import test from 'node:test'
import { effectScope, ref, watch } from 'vue'
import { useMarkerEditor } from '../src/composables/useMarkerEditor.js'

function fixture() {
  const scope = effectScope()
  const frames = new Map()
  let nextFrame = 1, commits = 0, updates = 0
  const pageData = ref({ page: 1, width: 1000, height: 500 })
  const item = ref({ id: 'w1', included: true, xNorm: .2, yNorm: .2, x: 200, y: 100,
    labelXNorm: .3, labelYNorm: .3, labelX: 300, labelY: 150 }).value
  const captures = new Set()
  const element = { setPointerCapture: id => captures.add(id), hasPointerCapture: id => captures.has(id), releasePointerCapture: id => captures.delete(id) }
  const state = {
    pageData, selectedId: ref(''), manualAddMode: ref(true), manualAddType: ref('weld'),
    anchorDragState: ref(null), groupDragState: ref(null), labelDragState: ref(null),
    canvasSurface: ref({ getBoundingClientRect: () => ({ left: 0, top: 0, width: 500, height: 250 }) }),
    canvasLayout: ref({ width: 1000, height: 500, target: { x: 0, y: 0, width: 1000, height: 500 } }),
  }
  const editor = scope.run(() => {
    watch(() => item.x, () => updates += 1, { flush: 'sync' })
    return useMarkerEditor({ state, operations: { beginHistory() {}, commitHistory() { commits += 1 }, logAudit() {} },
      requestFrame: callback => { const id = nextFrame++; frames.set(id, callback); return id },
      cancelFrame: id => frames.delete(id),
    })
  })
  const event = (clientX, clientY) => ({ button: 0, pointerId: 1, clientX, clientY, currentTarget: element, preventDefault() {}, stopPropagation() {} })
  const frame = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()) }
  return { scope, state, item, editor, event, frame, frames, captures, commits: () => commits, updates: () => updates }
}

for (const kind of ['Anchor', 'Group']) {
  test(`${kind} drag coalesces pointer moves per frame and flushes the final position before saving`, () => {
    const f = fixture()
    try {
      f.editor[`start${kind}Drag`](f.event(100, 50), f.item)
      for (let position = 101; position <= 200; position += 1) {
        f.editor[kind === 'Anchor' ? 'moveAnchor' : 'moveGroup'](f.event(position, 100), f.item)
      }
      assert.equal(f.frames.size, 1)
      assert.equal(f.updates(), 0)
      f.frame()
      assert.equal(f.updates(), 1)
      assert.equal(f.item.xNorm, .4)
      assert.equal(f.item.yNorm, .4)
      if (kind === 'Group') {
        assert.equal(f.item.labelXNorm, .5)
        assert.equal(f.item.labelYNorm, .5)
      }
      f.editor[kind === 'Anchor' ? 'moveAnchor' : 'moveGroup'](f.event(225, 125), f.item)
      f.editor[`end${kind}Drag`](f.event(225, 125), f.item)
      assert.equal(f.frames.size, 0)
      assert.equal(f.item.xNorm, .45)
      assert.equal(f.item.yNorm, .5)
      assert.equal(f.commits(), 1)
      assert.equal(f.captures.size, 0)
    } finally { f.scope.stop() }
  })

  test(`${kind} drag cancels pending frames on page change without editing another page`, () => {
    const f = fixture()
    try {
      f.editor[`start${kind}Drag`](f.event(100, 50), f.item)
      f.editor[kind === 'Anchor' ? 'moveAnchor' : 'moveGroup'](f.event(200, 100), f.item)
      f.state.pageData.value = { page: 2, width: 1000, height: 500 }
      f.frame()
      assert.equal(f.item.xNorm, .2)
      assert.equal(f.frames.size, 0)
      assert.equal(f.captures.size, 0)
      assert.equal(f.commits(), 0)
    } finally { f.scope.stop() }
  })
}
