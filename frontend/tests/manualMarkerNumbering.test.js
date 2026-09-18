import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, effectScope, ref } from 'vue'

import { useMarkerEditor } from '../src/composables/useMarkerEditor.js'

function fixture(type, existingCandidates, numbering, additionalPages = []) {
  const scope = effectScope()
  return scope.run(() => {
    const page = { page: 1, width: 1000, height: 500, candidates: [...existingCandidates], candidateCount: existingCandidates.length }
    const allPages = [page, ...additionalPages]
    const result = ref({ pages: allPages })
    const numberingSettings = ref(numbering)
    const currentPage = ref(1)
    const pageData = computed(() => allPages.find(item => item.page === currentPage.value) || page)
    const notices = []
    const selectedAppearanceTabs = []
    const editor = useMarkerEditor({
      state: {
        result, pageData, pages: computed(() => result.value.pages), candidates: computed(() => result.value.pages.flatMap(item => item.candidates)),
        selectedCandidate: ref(null), targetDocument: ref(null), currentPage,
        previewPage: ref(1), projectResults: ref([result.value]), activeProjectIndex: ref(0), manualAddMode: ref(true),
        manualAddType: ref(type), canvasSurface: ref({ getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 500 }) }),
        canvasLayout: ref({ width: 1000, height: 500, target: { x: 0, y: 0, width: 1000, height: 500 } }),
        canvasPointerPosition: ref(null), referenceFocus: ref(null), referenceShowAll: ref(false), startNumber: ref(1),
        numberPrefix: ref(''), numberSuffix: ref(''), useReferenceNumber: ref(false), selectedId: ref(''), swapSourceId: ref(''),
        editingId: ref(''), editingValue: ref(''),
        pendingHistory: ref(null), labelDragState: ref(null), anchorDragState: ref(null), groupDragState: ref(null),
        manualLeaderLength: ref(72), manualNumberingSettings: numberingSettings, error: ref(''),
      },
      operations: {
        cloneValue: value => structuredClone(value), beginHistory() {}, commitHistory() {},
        reflowCurrentPageLabelPositions: () => ({}), leaderEnd: () => ({}), logAudit() {},
        showNotice: message => notices.push(message), getLeaderLine: () => null, setLeaderLine() {},
        selectMarkerAppearanceTab: type => selectedAppearanceTabs.push(type),
      },
    })
    return { scope, page, pages: allPages, currentPage, result, editor, notices, numberingSettings, selectedAppearanceTabs }
  })
}

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

    await f.editor.toggleManualAddMode('all')
    assert.deepEqual(f.selectedAppearanceTabs, ['valve', 'flange', 'support', 'weld'])
  } finally { f.scope.stop() }
})

test('special markers keep their existing independent M numbering', () => {
  const f = fixture('all', [{ id: 'special', specialMarker: true, included: true }], undefined)
  try {
    f.editor.addManualAtClientPoint(500, 250)
    assert.equal(f.page.candidates.at(-1).number, 'M2')
  } finally { f.scope.stop() }
})
