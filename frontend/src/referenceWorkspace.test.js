import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useReferenceWorkspace } from './composables/useReferenceWorkspace.js'

function createWorkspace(options = {}) {
  const renders = []
  const notices = []
  const result = ref({ pages: [] })
  const error = ref('')
  const workspace = useReferenceWorkspace({
    projectResults: ref([]),
    result,
    currentPage: ref(1),
    selectedPcfFolder: ref('folder-a'),
    error,
    loadDocument: options.loadDocument,
    operations: {
      normalizeFiles: value => Array.isArray(value) ? value.filter(Boolean) : value ? [value] : [],
      showNotice: value => notices.push(value),
      materializeStoredFile: value => value,
      invalidateCanvas() {},
      clearCanvasCache() {},
      renderCanvas: fit => { renders.push(fit); return options.renderCanvas?.(fit) },
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
  return { workspace, result, renders, notices, error }
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

test('switching single and folder input modes keeps references linked for a loaded task', () => {
  const { workspace, result } = createWorkspace()
  const references = [{ name: 'line-a.pdf' }, { name: 'line-b.pdf' }]
  result.value = {
    jobId: 'loaded-task',
    pages: [{ page: 1, reference: { referenceFile: 'line-b.pdf', referencePage: 4 } }],
  }
  workspace.projectMode.value = 'folder'
  workspace.files.value = references
  workspace.activeIndex.value = 1
  workspace.activeFile.value = references[1]
  workspace.activePage.value = 4
  workspace.researchReady.value = true

  workspace.changeProjectMode('single')
  assert.equal(workspace.projectMode.value, 'single')
  assert.deepEqual(workspace.files.value.map(file => file.name), ['line-a.pdf', 'line-b.pdf'])
  assert.equal(workspace.activeFile.value.name, 'line-b.pdf')
  assert.equal(workspace.activePage.value, 4)
  assert.equal(workspace.researchReady.value, true)

  workspace.changeProjectMode('folder')
  assert.deepEqual(workspace.files.value.map(file => file.name), ['line-a.pdf', 'line-b.pdf'])
  assert.equal(workspace.activeFile.value.name, 'line-b.pdf')
})

test('switching reference usage tabs keeps reference files linked for a loaded task', () => {
  const { workspace, result } = createWorkspace()
  const reference = { name: 'line-a.pdf' }
  result.value = { jobId: 'loaded-task', pages: [{ page: 1 }] }
  workspace.mode.value = 'pdf'
  workspace.files.value = [reference]
  workspace.activeIndex.value = 0
  workspace.activeFile.value = reference
  workspace.researchReady.value = true

  workspace.changeMode('none')

  assert.equal(workspace.mode.value, 'none')
  assert.deepEqual(workspace.files.value, [reference])
  assert.equal(workspace.activeFile.value.name, reference.name)
  assert.equal(workspace.researchReady.value, true)
})

test('switching reference input modes still clears incomplete pre-analysis input', () => {
  const { workspace, result } = createWorkspace()
  result.value = null
  workspace.files.value = [{ name: 'temporary.pdf' }]
  workspace.researchReady.value = true

  workspace.changeProjectMode('folder')

  assert.deepEqual(workspace.files.value, [])
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

test('switching reference files displays the design while the new PDF opens without refitting on arrival', async () => {
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const { workspace, renders } = createWorkspace({ loadDocument: () => pending })
  const loaded = { numPages: 1, destroy() {} }
  const loading = workspace.loadPdf({ name: 'new.pdf', url: '/new.pdf' }, true)
  await Promise.resolve()
  assert.deepEqual(renders, [true])
  assert.equal(workspace.document.value, null)
  finish(loaded); await loading
  assert.equal(workspace.document.value, loaded)
  assert.deepEqual(renders, [true, false])
})

test('reference opening failure leaves the design-only render available', async () => {
  const { workspace, renders, error } = createWorkspace({ loadDocument: async () => { throw new Error('reference unavailable') } })
  await workspace.loadPdf({ name: 'new.pdf', url: '/new.pdf' }, true)
  assert.deepEqual(renders, [true])
  assert.equal(workspace.document.value, null)
  assert.match(error.value, /reference unavailable/)
})

test('a slow reference from an earlier file is discarded after switching to a newer file', async () => {
  let finish
  const pending = new Promise(resolve => { finish = resolve })
  const current = { numPages: 2, destroy() {} }
  let oldDestroyed = false
  const old = { numPages: 1, destroy() { oldDestroyed = true } }
  const { workspace, renders } = createWorkspace({ loadDocument: file => file.name === 'old.pdf' ? pending : Promise.resolve(current) })
  const first = workspace.loadPdf({ name: 'old.pdf', url: '/old.pdf' })
  await workspace.loadPdf({ name: 'current.pdf', url: '/current.pdf' })
  const renderCount = renders.length
  finish(old); await first
  assert.equal(oldDestroyed, true)
  assert.equal(workspace.document.value, current)
  assert.equal(renders.length, renderCount)
})
