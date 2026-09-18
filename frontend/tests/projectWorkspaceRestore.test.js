import assert from 'node:assert/strict'
import test from 'node:test'

import { ref } from 'vue'

import { numberDocumentResult } from '../src/numbering.js'
import { useProjectWorkspace } from '../src/composables/useProjectWorkspace.js'


test('completed server pages are released back to lightweight summaries after navigation', () => {
  const result = ref({
    jobId: 'job-a',
    pages: [
      {
        page: 1,
        detailsLoaded: true,
        candidates: [{ id: 'p1', included: true, referenceMatched: true, referenceLabel: 'W1' }],
        layoutObstacles: { textRects: [[1, 2, 3, 4]] },
      },
      { page: 2, detailsLoaded: true, candidates: [{ id: 'p2' }], layoutObstacles: { textRects: [] } },
      { page: 3, detailsLoaded: true, candidates: [{ id: 'dirty' }], layoutObstacles: { textRects: [] } },
    ],
  })
  const workspace = useProjectWorkspace({ state: { result }, operations: {} })

  workspace.releasePageDetails(2, [3])

  assert.equal(result.value.pages[0].detailsLoaded, false)
  assert.deepEqual(result.value.pages[0].candidates, [])
  assert.equal(result.value.pages[0].layoutObstacles, undefined)
  assert.equal(result.value.pages[0].matchSummary.complete, true)
  assert.equal(result.value.pages[1].candidates[0].id, 'p2')
  assert.equal(result.value.pages[2].candidates[0].id, 'dirty')
})


test('restoring a workspace preserves existing numbers when another candidate is blank', async () => {
  const restoredResult = {
    status: 'complete',
    analyzedRange: [1, 1],
    pages: [{ page: 1, candidates: [
      { id: 'kept', page: 1, x: 1, y: 1, number: 'F1A', included: true },
      { id: 'blank', page: 1, x: 2, y: 2, number: '', included: false },
    ] }],
  }
  let numberingCalls = 0
  let recoveredDirtyPages = []
  const state = {
    projectMode: ref('single'), projectPdfs: ref([]), projectResults: ref([]), activeProjectIndex: ref(0),
    targetPdf: ref(null), targetDocument: ref(null), startPage: ref(1), endPage: ref(''), result: ref(null),
    activeFileFingerprint: ref('drawing-a'),
    pendingDraft: ref({ source: 'indexeddb', payload: { result: restoredResult, currentPage: 1 } }),
    recoveryOptions: ref([]), selectedRecoveryKey: ref('indexeddb'), draftRecoveryDialog: ref(true),
    targetPreparation: ref({ active: false, completed: 0, total: 4, message: '' }),
    referenceResearchReady: ref(false), selectedId: ref(''), swapSourceId: ref(''), previewPage: ref(1),
    currentPage: ref(1), tutorialSessionActive: ref(false), tutorialSampleLoading: ref(false),
    referenceMode: ref('none'), referenceProjectMode: ref('single'), referencePdfs: ref([]),
    activeReferenceIndex: ref(-1), referencePdf: ref(null), unavailableReferenceFiles: ref([]), pcfFiles: ref([]),
    selectedPcfFolder: ref(null), useReferenceNumber: ref(false), startNumber: ref(1),
    hydratingWorkspace: ref(false), leftPanelTab: ref('input'), markerStyle: ref({}), error: ref(''),
  }
  const workspace = useProjectWorkspace({
    state,
    operations: {
      cloneValue: value => JSON.parse(JSON.stringify(value)),
      materializeStoredFile: async value => value,
      restoreProjectSettings() {}, restoreMarkerAppearances() {}, setActiveReferencePage() {},
      numberResult(value, start) {
        numberingCalls += 1
        numberDocumentResult(value, start, { useReferenceNumber: false, formatWeldNumber: number => String(number) })
      },
      clearHistory() {}, showNotice() {}, logAudit() {}, renderCanvas: async () => {},
      markRecoveredPagesDirty(pages) { recoveredDirtyPages = pages },
      matchedReference: () => null, syncReferenceForPage: async () => {}, loadReferencePdf: async () => {},
      archiveLazyReferences() {}, cancelReferenceArchiving() {}, invalidateCanvas() {}, dismissError() {},
      clearReferenceDisplay() {}, clearReferenceFiles() {}, selectRecoveryVersion() {},
      clearReferencesOnDesignUpload: ref(true), targetPageCount: ref(1),
    },
  })

  await workspace.restoreWorkspaceDraft()

  assert.equal(numberingCalls, 0)
  assert.deepEqual(state.result.value.pages[0].candidates.map(item => item.number), ['F1A', ''])
  assert.deepEqual(recoveredDirtyPages, [1])
})

test('PDF recovery marks restored pages dirty but a recent backend batch does not', async () => {
  const restoredResult = {
    status: 'complete', jobId: 'job-a', analyzedRange: [1, 2],
    pages: [
      { page: 1, reviewRevision: 1, candidates: [{ id: 'local-safe' }] },
      { page: 2, reviewRevision: 1, candidates: [{ id: 'local-conflict' }] },
    ],
  }
  const state = {
    projectMode: ref('single'), projectPdfs: ref([]), projectResults: ref([]), activeProjectIndex: ref(0),
    targetPdf: ref(null), targetDocument: ref(null), startPage: ref(1), endPage: ref(''), result: ref(null),
    activeFileFingerprint: ref('drawing-a'),
    pendingDraft: ref({ source: 'embedded', payload: { result: restoredResult, currentPage: 1 } }),
    recoveryOptions: ref([]), selectedRecoveryKey: ref('embedded'), draftRecoveryDialog: ref(true),
    targetPreparation: ref({ active: false, completed: 0, total: 4, message: '' }),
    referenceResearchReady: ref(false), selectedId: ref(''), swapSourceId: ref(''), previewPage: ref(1),
    currentPage: ref(1), tutorialSessionActive: ref(false), tutorialSampleLoading: ref(false),
    referenceMode: ref('none'), referenceProjectMode: ref('single'), referencePdfs: ref([]),
    activeReferenceIndex: ref(-1), referencePdf: ref(null), unavailableReferenceFiles: ref([]), pcfFiles: ref([]),
    selectedPcfFolder: ref(null), useReferenceNumber: ref(false), startNumber: ref(1),
    hydratingWorkspace: ref(false), leftPanelTab: ref('input'), markerStyle: ref({}), error: ref(''),
  }
  const marked = []
  const events = []
  const serverConflictPage = { page: 2, reviewRevision: 4, candidates: [{ id: 'server-conflict' }] }
  const operations = {
    cloneValue: value => JSON.parse(JSON.stringify(value)), materializeStoredFile: async value => value,
    restoreProjectSettings() {}, restoreMarkerAppearances() {}, setActiveReferencePage() {},
    clearHistory() {}, showNotice() {}, logAudit() {}, renderCanvas: async () => { events.push('render') },
    reconcileRecovery: async () => ({
      choice: 'draft', safePages: [1], conflictPages: [2], unavailablePages: [],
      serverByPage: new Map([[2, serverConflictPage]]),
    }),
    markRecoveredPagesDirty(pages) { marked.push(pages); events.push(`dirty:${pages.join(',')}`) },
    overwriteRecoveredPages: async pages => { events.push(`overwrite:${pages.join(',')}`) },
    activateRecoveredPage: async () => { events.push('activate') },
    matchedReference: () => null, syncReferenceForPage: async () => {}, loadReferencePdf: async () => {},
    archiveLazyReferences() {}, cancelReferenceArchiving() {}, invalidateCanvas() {}, dismissError() {},
    clearReferenceDisplay() {}, clearReferenceFiles() {}, selectRecoveryVersion() {},
    clearReferencesOnDesignUpload: ref(true), targetPageCount: ref(2),
  }
  const workspace = useProjectWorkspace({ state, operations })

  await workspace.restoreWorkspaceDraft()
  assert.deepEqual(marked, [[1, 2]])
  assert.ok(events.indexOf('dirty:1,2') < events.indexOf('render'))
  assert.ok(events.indexOf('overwrite:2') > events.indexOf('render'))
  assert.equal(state.result.value.pages[1].candidates[0].id, 'local-conflict')

  state.pendingDraft.value = { source: 'recent-batch', payload: { result: restoredResult, currentPage: 1 } }
  await workspace.restoreWorkspaceDraft()
  assert.deepEqual(marked, [[1, 2]])
})

test('choosing the server version replaces only conflicting recovered pages', async () => {
  const restoredResult = {
    status: 'complete', jobId: 'job-a', analyzedRange: [1, 2],
    pages: [
      { page: 1, reviewRevision: 2, candidates: [{ id: 'local-safe' }] },
      { page: 2, reviewRevision: 1, candidates: [{ id: 'local-conflict' }] },
    ],
  }
  const state = {
    projectMode: ref('single'), projectPdfs: ref([]), projectResults: ref([]), activeProjectIndex: ref(0),
    targetPdf: ref(null), targetDocument: ref(null), startPage: ref(1), endPage: ref(''), result: ref(null),
    activeFileFingerprint: ref('drawing-a'), pendingDraft: ref({ source: 'indexeddb', payload: { result: restoredResult, currentPage: 1 } }),
    recoveryOptions: ref([]), selectedRecoveryKey: ref('indexeddb'), draftRecoveryDialog: ref(true),
    targetPreparation: ref({ active: false, completed: 0, total: 4, message: '' }), referenceResearchReady: ref(false),
    selectedId: ref(''), swapSourceId: ref(''), previewPage: ref(1), currentPage: ref(1), tutorialSessionActive: ref(false),
    tutorialSampleLoading: ref(false), referenceMode: ref('none'), referenceProjectMode: ref('single'), referencePdfs: ref([]),
    activeReferenceIndex: ref(-1), referencePdf: ref(null), unavailableReferenceFiles: ref([]), pcfFiles: ref([]),
    selectedPcfFolder: ref(null), useReferenceNumber: ref(false), hydratingWorkspace: ref(false), leftPanelTab: ref('input'),
    markerStyle: ref({}), error: ref(''),
  }
  const dirty = []
  const serverPage = { page: 2, reviewRevision: 5, candidates: [{ id: 'server-kept' }] }
  const operations = {
    cloneValue: value => JSON.parse(JSON.stringify(value)), materializeStoredFile: async value => value,
    restoreProjectSettings() {}, restoreMarkerAppearances() {}, setActiveReferencePage() {}, clearHistory() {}, showNotice() {}, logAudit() {},
    renderCanvas: async () => {}, reconcileRecovery: async () => ({ choice: 'server', safePages: [1], conflictPages: [2], unavailablePages: [], serverByPage: new Map([[2, serverPage]]) }),
    markRecoveredPagesDirty: pages => dirty.push(...pages), activateRecoveredPage: async () => {},
    matchedReference: () => null, syncReferenceForPage: async () => {}, loadReferencePdf: async () => {}, archiveLazyReferences() {},
    cancelReferenceArchiving() {}, invalidateCanvas() {}, dismissError() {}, clearReferenceDisplay() {}, clearReferenceFiles() {}, selectRecoveryVersion() {},
    clearReferencesOnDesignUpload: ref(true), targetPageCount: ref(2),
  }

  await useProjectWorkspace({ state, operations }).restoreWorkspaceDraft()

  assert.equal(state.result.value.pages[0].candidates[0].id, 'local-safe')
  assert.equal(state.result.value.pages[1].candidates[0].id, 'server-kept')
  assert.deepEqual(dirty, [1])
})
