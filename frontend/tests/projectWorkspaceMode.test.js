import assert from 'node:assert/strict'
import test from 'node:test'
import { ref, shallowRef } from 'vue'

import { useProjectWorkspace } from '../src/composables/useProjectWorkspace.js'

function createLoadedWorkspace() {
  const target = { name: 'loaded.pdf' }
  const state = {
    projectMode: ref('single'), projectPdfs: ref([target]), projectResults: ref([]), activeProjectIndex: ref(0),
    targetPdf: ref(target), targetDocument: shallowRef(null), startPage: ref(3), endPage: ref(7),
    result: ref({ jobId: 'loaded-task', pages: [{ page: 3 }] }), activeFileFingerprint: ref('loaded'),
    pendingDraft: ref(null), recoveryOptions: ref([]), selectedRecoveryKey: ref(''), draftRecoveryDialog: ref(false),
    targetPreparation: ref({ active: false }), referenceResearchReady: ref(true), selectedId: ref('marker-1'),
    swapSourceId: ref(''), previewPage: ref(3), currentPage: ref(3), tutorialSessionActive: ref(false),
    tutorialSampleLoading: ref(false), referenceMode: ref('pdf'), referenceProjectMode: ref('single'),
    referencePdfs: ref([]), activeReferenceIndex: ref(-1), referencePdf: ref(null), unavailableReferenceFiles: ref([]),
    pcfFiles: ref([]), selectedPcfFolder: ref(null), useReferenceNumber: ref(true), hydratingWorkspace: ref(false),
    leftPanelTab: ref('input'), markerStyle: ref({}), error: ref(''),
  }
  let draftSaves = 0
  const workspace = useProjectWorkspace({
    state,
    operations: {
      scheduleDraftSave: () => { draftSaves += 1 },
      cancelReferenceArchiving() {}, invalidateCanvas() {}, clearHistory() {}, dismissError() {}, clearReferenceDisplay() {},
    },
  })
  return { state, workspace, target, draftSaves: () => draftSaves }
}

test('switching design input tabs preserves the loaded task and page state', () => {
  const { state, workspace, target, draftSaves } = createLoadedWorkspace()

  workspace.changeProjectMode('folder')

  assert.equal(state.projectMode.value, 'folder')
  assert.equal(state.targetPdf.value.name, target.name)
  assert.equal(state.result.value.jobId, 'loaded-task')
  assert.equal(state.currentPage.value, 3)
  assert.equal(state.selectedId.value, 'marker-1')
  assert.equal(state.startPage.value, 3)
  assert.equal(state.endPage.value, 7)
  assert.equal(draftSaves(), 1)
})
