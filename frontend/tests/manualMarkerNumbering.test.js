import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, effectScope, ref } from 'vue'

import { useMarkerEditor } from '../src/composables/useMarkerEditor.js'

function fixture(type, existingCandidates, numbering, additionalPages = [], specialAppearance, appearances = {}) {
  const scope = effectScope()
  return scope.run(() => {
    const page = { page: 1, width: 1000, height: 500, candidates: [...existingCandidates], candidateCount: existingCandidates.length }
    const allPages = [page, ...additionalPages]
    const result = ref({ jobId: 'server-job', pages: allPages })
    const numberingSettings = ref(numbering)
    const currentPage = ref(1)
    const pageData = computed(() => allPages.find(item => item.page === currentPage.value) || page)
    const notices = []
    const selectedAppearanceTabs = []
    const editingId = ref('')
    const editingValue = ref('')
    const editingSecondaryValue = ref('')
    const editor = useMarkerEditor({
      state: {
        result, pageData, pages: computed(() => result.value.pages), candidates: computed(() => result.value.pages.flatMap(item => item.candidates)),
        selectedCandidate: ref(null), targetDocument: ref(null), currentPage,
        previewPage: ref(1), projectResults: ref([result.value]), activeProjectIndex: ref(0), manualAddMode: ref(true),
        manualAddType: ref(type), canvasSurface: ref({ getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 500 }) }),
        canvasLayout: ref({ width: 1000, height: 500, target: { x: 0, y: 0, width: 1000, height: 500 } }),
        canvasPointerPosition: ref(null), referenceFocus: ref(null), referenceShowAll: ref(false), startNumber: ref(1),
        numberPrefix: ref(''), numberSuffix: ref(''), useReferenceNumber: ref(false), selectedId: ref(''), swapSourceId: ref(''),
        editingId, editingValue, editingSecondaryValue,
        pendingHistory: ref(null), labelDragState: ref(null), anchorDragState: ref(null), groupDragState: ref(null),
        manualLeaderLength: ref(72), manualNumberingSettings: numberingSettings,
        specialMarkerAppearance: ref(specialAppearance), error: ref(''),
      },
      operations: {
        cloneValue: value => structuredClone(value), beginHistory() {}, commitHistory() {},
        reflowCurrentPageLabelPositions: () => ({}), leaderEnd: () => ({}), logAudit() {},
        showNotice: message => notices.push(message), getLeaderLine: () => null, setLeaderLine() {},
        candidateMarkerStyle: item => item.markerStyle || item.defaultMarkerStyle || { shape: 'circle' },
        manualMarkerStyle: markerType => markerType === 'other'
          ? (specialAppearance || { shape: 'split-circle' })
          : (appearances[markerType] || { shape: 'circle' }),
        selectMarkerAppearanceTab: type => selectedAppearanceTabs.push(type),
      },
    })
    return { scope, page, pages: allPages, currentPage, result, editor, notices, numberingSettings, selectedAppearanceTabs, editingId, editingValue, editingSecondaryValue }
  })
}

test('drag previews match committed marker appearance and numbering without consuming numbers', () => {
  for (const type of ['weld', 'valve', 'flange', 'support', 'other']) {
    for (const shape of ['circle', 'rectangle', 'diamond', 'split-circle']) {
      const appearance = { shape, color: '#654321', frameSize: 40, fontSize: 12, lineWidth: 2, fillOpacity: 0.3 }
      const f = fixture(type, [], { [type]: { prefix: 'TEST-', start: 7, secondaryPrefix: 'LOW-', secondaryStart: 9 } }, [], appearance, { [type]: appearance })
      try {
        const start = { x: 0.5, y: 0.5 }
        const end = { x: 0.8, y: 0.2 }
        const firstPreview = f.editor.previewManualMarker(start, end)
        const finalPreview = f.editor.previewManualMarker(start, end)
        assert.deepEqual(finalPreview, firstPreview)
        assert.equal(f.page.candidates.length, 0)
        assert.deepEqual(f.notices, [])
        f.editor.addManualAtClientPoint(500, 250, { x: 800, y: 100 })
        const { id: previewId, ...preview } = finalPreview
        const { id: committedId, ...committed } = f.page.candidates.at(-1)
        assert.notEqual(previewId, committedId)
        assert.deepEqual(preview, committed)
        assert.equal(committed.number, 'TEST-7')
        const next = f.editor.previewManualMarker(start, end)
        assert.equal(next.number, 'TEST-8')
        if (shape === 'split-circle') {
          assert.equal(preview.secondaryText, 'LOW-9')
          assert.equal(next.secondaryText, 'LOW-10')
        }
      } finally { f.scope.stop() }
    }
  }
})

test('drag placement preserves the anchor and explicit label endpoint for every marker type', () => {
  for (const type of ['weld', 'valve', 'flange', 'support', 'other']) {
    const f = fixture(type, [], {})
    try {
      f.editor.addManualAtClientPoint(500, 250, { x: 800, y: 100 })
      const item = f.page.candidates.at(-1)
      assert.equal(item.x, 500)
      assert.equal(item.y, 250)
      assert.equal(item.labelX, 800)
      assert.equal(item.labelY, 100)
      assert.equal(item.labelXNorm, 0.8)
      assert.equal(item.labelYNorm, 0.2)
      assert.equal(item.componentType || 'weld', type === 'other' ? 'special' : type)
    } finally { f.scope.stop() }
  }
})

test('the first manual marker starts at the configured number regardless of recognized markers', () => {
  const f = fixture('valve', [{ id: 'existing', componentType: 'valve', included: true }], {
    weld: { prefix: '', suffix: '', start: 1 },
    valve: { prefix: 'XV-', suffix: '-A', start: 6 },
    flange: { prefix: 'FL', suffix: '', start: 1 },
    support: { prefix: 'SP', suffix: '', start: 1 },
  })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'XV-6-A')
    assert.equal(f.page.candidates.at(-1).autoNumberPrefix, 'XV-')

    f.editor.addManualAtClientPoint(600, 250)
    assert.equal(f.page.candidates.at(-1).number, 'XV-7-A')
  } finally { f.scope.stop() }
})

test('manual weld numbering has no default prefix and accepts a custom suffix and start', () => {
  const f = fixture('weld', [], {
    weld: { prefix: '', suffix: '-W', start: 20 },
    valve: { prefix: 'V', suffix: '', start: 1 },
    flange: { prefix: 'FL', suffix: '', start: 1 },
    support: { prefix: 'SP', suffix: '', start: 1 },
  })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, '20-W')
  } finally { f.scope.stop() }
})

test('manual numbering allows the numeric part to be blank', () => {
  const f = fixture('weld', [], {
    weld: { prefix: 'W-', suffix: '-END', start: null },
  })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    f.editor.addManualAtClientPoint(600, 250)
    assert.deepEqual(f.page.candidates.map(item => item.number), ['W--END', 'W--END'])
    assert.equal(f.numberingSettings.value.weld.start, null)
  } finally { f.scope.stop() }
})

test('opening a task starts from the configured number without scanning existing markers', () => {
  const f = fixture('valve', [
    { id: 'recognized-valve', componentType: 'valve', included: true },
    { id: 'included-valve', componentType: 'valve', included: true, origin: 'manual', number: 'V1', autoNumberPrefix: 'V' },
    { id: 'excluded-valve', componentType: 'valve', included: false, origin: 'manual', number: 'V2', autoNumberPrefix: 'V' },
    { id: 'flange', componentType: 'flange', included: true },
    { id: 'weld', included: true },
    { id: 'special', specialMarker: true, included: true },
  ], undefined)
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'V1')
  } finally { f.scope.stop() }
})

test('manual numbering keeps independent sequences for different prefixes and suffixes', () => {
  const f = fixture('weld', [], {
    weld: { prefix: 'F', suffix: '', start: 20 },
  })
  try {
    f.editor.addManualAtClientPoint(300, 250)
    f.editor.addManualAtClientPoint(400, 250)
    assert.deepEqual(f.page.candidates.map(item => item.number), ['F20', 'F21'])

    f.numberingSettings.value.weld = { prefix: 'FS', suffix: '', start: 30 }
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'FS30')

    f.numberingSettings.value.weld = { prefix: 'F', suffix: '-A', start: 40 }
    f.editor.addManualAtClientPoint(600, 250)
    assert.equal(f.page.candidates.at(-1).number, 'F40-A')

    f.numberingSettings.value.weld = { prefix: 'F', suffix: '', start: 20 }
    f.editor.addManualAtClientPoint(700, 250)
    assert.equal(f.page.candidates.at(-1).number, 'F20')
  } finally { f.scope.stop() }
})

test('manual numbering starts a separate sequence when only the starting number changes', () => {
  const f = fixture('weld', [], {
    weld: { prefix: 'F', suffix: '', start: 30 },
  })
  try {
    f.editor.addManualAtClientPoint(300, 250)
    f.editor.addManualAtClientPoint(400, 250)
    assert.deepEqual(f.page.candidates.map(item => item.number), ['F30', 'F31'])

    f.numberingSettings.value.weld = { prefix: 'F', suffix: '', start: 40 }
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'F40')

    f.editor.addManualAtClientPoint(600, 250)
    assert.equal(f.page.candidates.at(-1).number, 'F41')

    f.numberingSettings.value.weld = { prefix: 'F', suffix: '', start: 30 }
    f.editor.addManualAtClientPoint(700, 250)
    assert.equal(f.page.candidates.at(-1).number, 'F30')
  } finally { f.scope.stop() }
})

test('manual numbering continues across pages while the rule and task stay unchanged', () => {
  const secondPage = { page: 2, width: 1000, height: 500, candidates: [], candidateCount: 0 }
  const f = fixture('weld', [], { weld: { prefix: '', suffix: '', start: 20 } }, [secondPage])
  try {
    for (let index = 0; index < 11; index += 1) f.editor.addManualAtClientPoint(100 + index * 20, 250)
    assert.equal(f.page.candidates.at(-1).number, '30')

    f.currentPage.value = 2
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(secondPage.candidates.at(-1).number, '31')
  } finally { f.scope.stop() }
})

for (const type of ['valve', 'flange', 'support']) {
  test(`${type} numbering restarts on each page and keeps its same-page sequence`, () => {
    const secondPage = { page: 2, width: 1000, height: 500, candidates: [], candidateCount: 0 }
    const prefixes = { valve: 'V', flange: 'FL', support: 'SP' }
    const f = fixture(type, [], { [type]: { prefix: prefixes[type], suffix: '', start: 5 } }, [secondPage])
    try {
      f.editor.addManualAtClientPoint(300, 250)
      f.editor.addManualAtClientPoint(400, 250)
      assert.deepEqual(f.page.candidates.map(item => item.number), [`${prefixes[type]}5`, `${prefixes[type]}6`])

      f.currentPage.value = 2
      f.editor.addManualAtClientPoint(500, 250)
      assert.equal(secondPage.candidates.at(-1).number, `${prefixes[type]}5`)

      f.currentPage.value = 1
      f.editor.addManualAtClientPoint(600, 250)
      assert.equal(f.page.candidates.at(-1).number, `${prefixes[type]}7`)
    } finally { f.scope.stop() }
  })
}

test('entering a typed modification mode selects its matching appearance tab', async () => {
  const f = fixture('weld', [], undefined)
  try {
    await f.editor.toggleManualAddMode('valve')
    await f.editor.toggleManualAddMode('flange')
    await f.editor.toggleManualAddMode('support')
    await f.editor.toggleManualAddMode('weld')
    assert.deepEqual(f.selectedAppearanceTabs, ['valve', 'flange', 'support', 'weld'])

    await f.editor.toggleManualAddMode('other')
    assert.deepEqual(f.selectedAppearanceTabs, ['valve', 'flange', 'support', 'weld', 'other'])
  } finally { f.scope.stop() }
})

test('manual placement refuses a workspace without a server job id', async () => {
  const f = fixture('weld', [], undefined)
  try {
    f.result.value.jobId = null
    const enabled = await f.editor.ensureManualWorkspace()
    assert.equal(enabled, false)
    assert.equal(f.result.value.jobId, null)
  } finally { f.scope.stop() }
})

test('M mode creates a split-circle marker with independent upper and lower content', () => {
  const f = fixture('other', [], {
    other: {
      prefix: 'UP-', suffix: '-A', start: 3,
      secondaryPrefix: 'DOWN-', secondarySuffix: '-B', secondaryStart: 8,
    },
  })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    const marker = f.page.candidates.at(-1)
    assert.equal(marker.number, 'UP-3-A')
    assert.equal(marker.secondaryText, 'DOWN-8-B')
    assert.equal(marker.markerStyle.shape, 'split-circle')

    f.editor.addManualAtClientPoint(600, 250)
    assert.equal(f.page.candidates.at(-1).number, 'UP-4-A')
    assert.equal(f.page.candidates.at(-1).secondaryText, 'DOWN-9-B')
  } finally { f.scope.stop() }
})

test('a regular marker uses separate upper and lower numbering when its appearance is split-circle', () => {
  const f = fixture('valve', [], {
    valve: {
      prefix: 'V', suffix: '', start: 5,
      secondaryPrefix: 'L-', secondarySuffix: '-X', secondaryStart: 20,
    },
  }, [], undefined, { valve: { shape: 'split-circle' } })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'V5')
    assert.equal(f.page.candidates.at(-1).secondaryText, 'L-20-X')
  } finally { f.scope.stop() }
})

test('split-circle numbering allows either numeric part to be blank', () => {
  const f = fixture('valve', [], {
    valve: {
      prefix: 'TOP', suffix: '', start: null,
      secondaryPrefix: 'BOTTOM', secondarySuffix: '', secondaryStart: null,
    },
  }, [], undefined, { valve: { shape: 'split-circle' } })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'TOP')
    assert.equal(f.page.candidates.at(-1).secondaryText, 'BOTTOM')
  } finally { f.scope.stop() }
})

test('M mode uses the configured other marker appearance', () => {
  const f = fixture('other', [], undefined, [], {
    shape: 'split-circle', frameSize: 44, fontSize: 13, lineWidth: 2,
    color: '#228866', fillOpacity: 0.25,
  })
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.deepEqual(f.page.candidates.at(-1).markerStyle, {
      shape: 'split-circle', frameSize: 44, fontSize: 13, lineWidth: 2,
      color: '#228866', fillOpacity: 0.25,
    })
  } finally { f.scope.stop() }
})

test('double-click editing updates both split-circle fields together', () => {
  const marker = { id: 'special', number: 'M1', secondaryText: 'A', included: true, markerStyle: { shape: 'split-circle' } }
  const f = fixture('other', [marker], undefined)
  try {
    f.editor.beginInlineEdit({ button: 0, stopPropagation() {} }, marker)
    assert.equal(f.editingValue.value, 'M1')
    assert.equal(f.editingSecondaryValue.value, 'A')
    f.editingValue.value = 'M2'
    f.editingSecondaryValue.value = 'B'
    f.editor.commitInlineEdit(marker)
    assert.equal(marker.number, 'M2')
    assert.equal(marker.secondaryText, 'B')
    assert.equal(f.editingId.value, '')
  } finally { f.scope.stop() }
})
