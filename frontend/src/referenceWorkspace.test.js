import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useReferenceWorkspace } from './composables/useReferenceWorkspace.js'

function createWorkspace() {
  const renders = []
  const notices = []
  const result = ref({ pages: [] })
  const workspace = useReferenceWorkspace({
    projectResults: ref([]),
    result,
    currentPage: ref(1),
    selectedPcfFolder: ref('folder-a'),
    error: ref(''),
    operations: {
      normalizeFiles: value => Array.isArray(value) ? value.filter(Boolean) : value ? [value] : [],
      showNotice: value => notices.push(value),
      materializeStoredFile: value => value,
      invalidateCanvas() {},
      clearCanvasCache() {},
      renderCanvas: fit => { renders.push(fit) },
      getHintDocument: () => null,
      getHintPage: () => null,
      canvasLayout: ref({ reference: null }),
      canvasViewport: ref(null),
      zoom: ref(1),
      pan: ref({ x: 0, y: 0 }),
      scheduleResolutionRender() {},
      fitCanvas() {},
      scheduleDraftSave() {},
    },
  })
  return { workspace, result, renders, notices }
}

test('reference folder registration filters and sorts PDF files', () => {
  const { workspace, notices } = createWorkspace()
  workspace.setFolderFiles([
    { name: 'B.pdf' },
    { name: 'notes.txt' },
    { name: 'A.PDF' },
  ])
  assert.deepEqual(workspace.files.value.map(file => file.name), ['A.PDF', 'B.pdf'])
  assert.equal(workspace.activeIndex.value, -1)
  assert.match(notices.at(-1), /2 份对照 PDF/)
})

test('clearing references resets document selection and research state', () => {
  const { workspace } = createWorkspace()
  workspace.files.value = [{ name: 'reference.pdf' }]
  workspace.activeIndex.value = 0
  workspace.researchReady.value = true
  workspace.activePage.value = 8
  workspace.clearFiles()
  assert.deepEqual(workspace.files.value, [])
  assert.equal(workspace.activeIndex.value, -1)
  assert.equal(workspace.activePage.value, 1)
  assert.equal(workspace.researchReady.value, false)
})

test('keeps PDF document instances unproxied so private-field getters remain usable', () => {
  class PdfDocumentLike {
    #pagesNumber = 20
    get numPages() { return this.#pagesNumber }
  }
  const { workspace } = createWorkspace()
  const pdfDocument = new PdfDocumentLike()

  workspace.document.value = pdfDocument

  assert.equal(workspace.document.value, pdfDocument)
  assert.equal(workspace.document.value.numPages, 20)
})

test('page matching resolves the registered reference slot', () => {
  const { workspace, result } = createWorkspace()
  workspace.files.value = [{ name: 'line-a.pdf', index: 0 }]
  result.value.pages = [{
    page: 3,
    reference: { referenceFile: 'line-a.pdf', referencePage: 7 },
  }]
  const match = workspace.matched(3)
  assert.equal(match?.index, 0)
  assert.equal(match?.page, 7)
})

test('an unmatched design page clears the previously displayed reference page', async () => {
  const { workspace, result, renders } = createWorkspace()
  let destroyed = false
  workspace.files.value = [{ name: 'line-a.pdf', index: 0 }]
  workspace.researchReady.value = true
  workspace.activeIndex.value = 0
  workspace.activeFile.value = workspace.files.value[0]
  workspace.activePage.value = 7
  workspace.document.value = { destroy: () => { destroyed = true } }
  result.value.pages = [{ page: 2, reference: {} }]

  await workspace.syncForPage(2, false)

  assert.equal(destroyed, true)
  assert.equal(workspace.document.value, null)
  assert.equal(workspace.activeFile.value, null)
  assert.equal(workspace.activeIndex.value, -1)
  assert.equal(workspace.activePage.value, 1)
  assert.equal(renders.at(-1), false)
})
