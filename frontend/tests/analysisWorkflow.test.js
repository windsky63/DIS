import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useAnalysisWorkflow } from '../src/composables/useAnalysisWorkflow.js'


function createWorkflow({ referenceMode = 'pdf', referencePdfs = [], existingJob = null } = {}) {
  const error = ref('')
  const workflow = useAnalysisWorkflow({
    workspace: {
      targetPdf: ref(new File(['design'], 'design.pdf', { type: 'application/pdf' })),
      projectPdfs: ref([]), projectResults: ref([]), activeProjectIndex: ref(0), projectMode: ref('single'),
      startPage: ref(1), endPage: ref(null), referenceMode: ref(referenceMode), referencePdfs: ref(referencePdfs),
      pcfFiles: ref([]), selectedPcfFolder: ref(null), weldSymbolConfig: ref({ referenceRules: [] }),
      activeRecognitionProject: ref({ id: 'project', name: 'Project', description: '', recognitionRules: {} }),
      useReferenceNumber: ref(true), startNumber: ref(1), numberPrefix: ref(''), numberSuffix: ref(''),
      result: ref(existingJob), referenceResearchReady: ref(false), currentPage: ref(1), selectedId: ref(''),
      swapSourceId: ref(''), undoStack: ref([]), redoStack: ref([]), uploadCommitPending: ref(false), error,
    },
    operations: {
      dismissError() {}, clearNotice() {}, showNotice() {}, logAudit() {},
    },
  })
  return { workflow, error }
}


test('PDF comparison mode silently refuses queue submission until a comparison PDF is selected', async () => {
  const { workflow, error } = createWorkflow()

  await workflow.enqueue()

  assert.equal(workflow.enqueueing.value, false)
  assert.equal(workflow.referenceInputMissing.value, true)
  assert.equal(error.value, '')
})

test('existing server task blocks every task creation workflow', async () => {
  const { workflow, error } = createWorkflow({ existingJob: { jobId: 'existing-job', status: 'complete' } })

  await workflow.directPlacement()
  assert.match(error.value, /已有任务/)
  error.value = ''

  await workflow.enqueue()
  assert.match(error.value, /已有任务/)
  error.value = ''

  await workflow.analyze()
  assert.match(error.value, /已有任务/)
  assert.equal(workflow.loading.value, false)
  assert.equal(workflow.enqueueing.value, false)
})
