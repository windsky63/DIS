import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useCanvasRenderer } from '../src/composables/useCanvasRenderer.js'

function deferred() {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}

function fakeCanvas(width = 10, height = 10) {
  const draws = []
  const context = {
    setTransform() {}, fillRect() {}, drawImage(...args) { draws.push(args) }, fillText() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
  }
  return { width, height, style: {}, draws, getContext: () => context, toBlob: callback => callback(null) }
}

function fakePage({ width = 100, height = 80, onRender } = {}) {
  return {
    getViewport: () => ({ width, height }),
    render: options => {
      onRender?.(options)
      return { promise: Promise.resolve(), cancel() {} }
    },
  }
}

function rendererFixture({
  targetDocument, referenceDocument = null, shownPage = 1, referencePage = 1,
  scheduleIdle = () => 0, scheduleDelay, performanceProfile,
} = {}) {
  const visibleCanvas = fakeCanvas(17, 19)
  const originalDocument = globalThis.document
  const originalWindow = globalThis.window
  globalThis.document = { createElement: () => fakeCanvas() }
  globalThis.window = { devicePixelRatio: 1 }
  let fitCalls = 0
  let state
  const renderer = useCanvasRenderer({
    state: (state = {
      targetDocument: ref(targetDocument), referenceDocument: ref(referenceDocument),
      targetPdf: ref({ name: 'target.pdf' }), referencePdf: ref(referenceDocument ? { name: 'reference.pdf' } : null),
      activeProjectIndex: ref(0), activeReferenceIndex: ref(0), shownPage: ref(shownPage), activeReferencePage: ref(referencePage),
      referencePdfs: ref([]), pdfCanvas: ref(visibleCanvas), canvasLayout: ref({ width: 1, height: 1 }),
      zoom: ref(1), performanceProfile: ref(performanceProfile || { key: 'balanced', maxDimension: 4096, maxPixels: 8_000_000, cacheBytes: 96 * 1024 * 1024 }),
      error: ref(''), displayReference: ref(Boolean(referenceDocument)),
    }),
    fitCanvas() { fitCalls += 1 }, scheduleIdle, ...(scheduleDelay ? { scheduleDelay } : {}),
  })
  return {
    renderer,
    visibleCanvas,
    state, fitCalls: () => fitCalls,
    restore() { renderer.dispose(); globalThis.document = originalDocument; globalThis.window = originalWindow },
  }
}

test('target and reference PDF page objects start loading in parallel', async () => {
  const targetGate = deferred()
  const calls = []
  const targetDocument = { numPages: 3, getPage: async page => { calls.push(`target:${page}`); await targetGate.promise; return fakePage() } }
  const referenceDocument = { numPages: 2, getPage: async page => { calls.push(`reference:${page}`); return fakePage() } }
  const fixture = rendererFixture({ targetDocument, referenceDocument })
  try {
    const rendering = fixture.renderer.render()
    await Promise.resolve()
    const startedTogether = calls.includes('reference:1')
    targetGate.resolve()
    await rendering
    assert.equal(startedTogether, true)
  } finally {
    fixture.restore()
  }
})

test('design paints while the reference page object is still loading and keeps its geometry and view', async () => {
  const gate = deferred()
  const fixture = rendererFixture({
    targetDocument: { numPages: 1, getPage: async () => fakePage() },
    referenceDocument: { numPages: 1, getPage: async () => { await gate.promise; return fakePage({ width: 900, height: 500 }) } },
  })
  try {
    const rendering = fixture.renderer.render(true)
    await new Promise(resolve => setImmediate(resolve))
    assert.ok(fixture.visibleCanvas.draws.length > 0, 'slow reference must not block the first design frame')
    const targetGeometry = { ...fixture.state.canvasLayout.value.target }
    fixture.state.zoom.value = 2
    gate.resolve(); await rendering
    assert.deepEqual(fixture.state.canvasLayout.value.target, targetGeometry)
    assert.equal(fixture.state.canvasLayout.value.reference.width, 900)
    assert.equal(fixture.state.zoom.value, 2)
    assert.equal(fixture.fitCalls(), 1, 'late reference must not refit the viewport')
    assert.ok(fixture.visibleCanvas.width * fixture.visibleCanvas.height <= 8_000_000)
  } finally { gate.resolve(); fixture.restore() }
})

test('a reference page-load failure preserves the design bitmap and reports the error', async () => {
  const fixture = rendererFixture({
    targetDocument: { numPages: 1, getPage: async () => fakePage() },
    referenceDocument: { numPages: 1, getPage: async () => { throw new Error('reference offline') } },
  })
  try {
    await fixture.renderer.render()
    assert.ok(fixture.visibleCanvas.draws.length > 0)
    assert.match(fixture.state.error.value, /reference offline/)
    assert.equal(fixture.renderer.rendering.value, false)
  } finally { fixture.restore() }
})

test('late reference results from an old page cannot repaint or report errors on the current page', async () => {
  const gate = deferred()
  const fixture = rendererFixture({
    targetDocument: { numPages: 2, getPage: async number => fakePage({ width: number * 100 }) },
    referenceDocument: { numPages: 2, getPage: async number => {
      if (number === 1) { await gate.promise; throw new Error('old reference offline') }
      return fakePage()
    } },
  })
  try {
    const first = fixture.renderer.render()
    await new Promise(resolve => setImmediate(resolve))
    fixture.state.shownPage.value = 2; fixture.state.activeReferencePage.value = 2
    await fixture.renderer.render()
    const draws = fixture.visibleCanvas.draws.length
    gate.resolve(); await first
    assert.equal(fixture.visibleCanvas.draws.length, draws)
    assert.equal(fixture.state.canvasLayout.value.target.width, 200)
    assert.equal(fixture.state.error.value, '')
  } finally { gate.resolve(); fixture.restore() }
})

test('visible canvas keeps its previous bitmap until the replacement bitmap is ready', async () => {
  const renderGate = deferred()
  const page = fakePage()
  page.render = () => ({ promise: renderGate.promise, cancel() {} })
  const fixture = rendererFixture({ targetDocument: { numPages: 1, getPage: async () => page } })
  try {
    const rendering = fixture.renderer.render()
    await Promise.resolve()
    await Promise.resolve()
    assert.deepEqual([fixture.visibleCanvas.width, fixture.visibleCanvas.height], [17, 19])
    renderGate.resolve()
    await rendering
    assert.notDeepEqual([fixture.visibleCanvas.width, fixture.visibleCanvas.height], [17, 19])
  } finally {
    fixture.restore()
  }
})

test('target bitmap becomes visible before reference rasterization completes', async () => {
  const referenceGate = deferred()
  const targetPage = fakePage()
  const referencePage = fakePage()
  referencePage.render = () => ({ promise: referenceGate.promise, cancel() {} })
  const fixture = rendererFixture({
    targetDocument: { numPages: 1, getPage: async () => targetPage },
    referenceDocument: { numPages: 1, getPage: async () => referencePage },
  })
  try {
    const rendering = fixture.renderer.render()
    await new Promise(resolve => setImmediate(resolve))
    assert.ok(fixture.visibleCanvas.draws.length >= 1)
    referenceGate.resolve()
    await rendering
    assert.ok(fixture.visibleCanvas.draws.length >= 3)
  } finally {
    fixture.restore()
  }
})

test('large uncached pages show a reduced preview before the high-resolution bitmap', async () => {
  const factors = []
  let releaseUpgrade
  const page = fakePage({ width: 3000, height: 2500, onRender: options => factors.push(options.transform[0]) })
  const fixture = rendererFixture({
    targetDocument: { numPages: 1, getPage: async () => page },
    scheduleDelay: callback => { releaseUpgrade = callback; return 1 },
  })
  try {
    const rendering = fixture.renderer.render()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(factors.length, 1)
    assert.ok(factors[0] < 1)
    assert.equal(typeof releaseUpgrade, 'function')
    releaseUpgrade()
    await rendering
    assert.equal(factors.length, 2)
    assert.equal(factors[1], 1)
  } finally {
    fixture.restore()
  }
})

test('completed render schedules adjacent target pages for idle prefetch', async () => {
  const calls = []
  let idleTask
  const targetDocument = { numPages: 3, getPage: async page => { calls.push(page); return fakePage() } }
  const fixture = rendererFixture({ targetDocument, scheduleIdle: callback => { idleTask = callback; return 1 } })
  try {
    await fixture.renderer.render()
    assert.equal(typeof idleTask, 'function')
    await idleTask()
    assert.deepEqual(calls, [1, 2])
  } finally {
    fixture.restore()
  }
})

test('reference prefetch waits for settled navigation and only fetches the next page', async () => {
  const calls = []
  let idleTask
  let settledTask
  const targetDocument = { numPages: 3, getPage: async page => { calls.push(`target:${page}`); return fakePage() } }
  const referenceDocument = { numPages: 3, getPage: async page => { calls.push(`reference:${page}`); return fakePage() } }
  const fixture = rendererFixture({
    targetDocument, referenceDocument, shownPage: 2, referencePage: 2,
    scheduleIdle: callback => { idleTask = callback; return 1 },
    scheduleDelay: callback => { settledTask = callback; return 2 },
  })
  try {
    await fixture.renderer.render()
    await idleTask()
    assert.deepEqual(calls, ['target:2', 'reference:2', 'target:3'])
    assert.equal(typeof settledTask, 'function')
    settledTask()
    await idleTask()
    assert.deepEqual(calls, [
      'target:2', 'reference:2',
      'target:3', 'reference:3',
    ])
  } finally {
    fixture.restore()
  }
})
