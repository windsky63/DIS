import { nextTick, ref, shallowRef } from 'vue'

import { api } from '../api.js'
import { sameReferenceHint } from '../referenceHintIdentity.js'
import { referenceFileSlot, resolveMatchedReference, resolveReferenceFileIndex } from '../referenceIdentity.js'
import { loadPdfDocument } from '../services/pdfDocument.js'

const MAX_LAZY_REFERENCE_FILES = 5

export function useReferenceWorkspace({ projectResults, result, currentPage, selectedPcfFolder, error, operations }) {
  const mode = ref('pdf')
  const projectMode = ref('single')
  const files = ref([])
  const unavailableFiles = ref([])
  const activeIndex = ref(0)
  const researchReady = ref(false)
  const activeFile = ref(null)
  // PDF.js document proxies contain native class private fields. A normal Vue
  // ref recursively wraps the instance in a Proxy, which breaks getters such
  // as `numPages` because their `this` value is no longer the original object.
  const document = shallowRef(null)
  const activePage = ref(1)
  const pcfFiles = ref([])
  const focus = ref(null)
  const showAll = ref(false)
  const archiving = ref(false)
  let loadToken = 0
  let archiveGeneration = 0
  const lazyFileCache = new Map()

  function sameSlot(left, right, fallbackIndex = 0) {
    return Boolean(left && right)
      && referenceFileSlot(left, fallbackIndex) === referenceFileSlot(right, fallbackIndex)
  }

  function cancelArchiving() {
    archiveGeneration += 1
    archiving.value = false
  }

  function clearDisplay() {
    loadToken += 1
    operations.invalidateCanvas()
    const previousDocument = document.value
    document.value = null
    previousDocument?.destroy?.()
    activeIndex.value = -1
    activeFile.value = null
    activePage.value = 1
    focus.value = null
    showAll.value = false
    operations.clearCanvasCache()
    lazyFileCache.clear()
  }

  function clearFiles() {
    cancelArchiving()
    clearDisplay()
    files.value = []
    researchReady.value = false
    unavailableFiles.value = []
  }

  function changeMode(nextMode) {
    mode.value = nextMode
    clearFiles()
    pcfFiles.value = []
    selectedPcfFolder.value = null
    void operations.renderCanvas(true)
  }

  function changeProjectMode(nextMode) {
    projectMode.value = nextMode
    clearFiles()
    pcfFiles.value = []
    selectedPcfFolder.value = null
    void operations.renderCanvas(true)
  }

  function normalizedPdfFiles(value) {
    const rawFiles = operations.normalizeFiles(value)
    return {
      rawFiles,
      pdfs: rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pdf'))
        .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN')),
    }
  }

  function setFiles(value) {
    const { rawFiles, pdfs } = normalizedPdfFiles(value)
    clearFiles()
    if (!rawFiles.length) { void operations.renderCanvas(true); return }
    if (!pdfs.length) {
      error.value = '所选对照内容中没有 PDF 文件。'
      void operations.renderCanvas(true)
      return
    }
    files.value = pdfs
    unavailableFiles.value = []
    if (projectMode.value === 'folder') {
      activeIndex.value = -1
      activeFile.value = null
      operations.showNotice(`已登记 ${pdfs.length} 份对照 PDF；完成图元研究后将按匹配关系加载到 Canvas。`)
      void operations.renderCanvas(true)
      return
    }
    activeIndex.value = 0
    activePage.value = 1
    activeFile.value = pdfs[0]
    void loadPdf(pdfs[0])
  }

  function setFolderFiles(value) {
    const { rawFiles, pdfs } = normalizedPdfFiles(value)
    clearFiles()
    if (!rawFiles.length) { void operations.renderCanvas(true); return }
    if (!pdfs.length) {
      error.value = '所选对照 PDF 文件夹中没有 PDF 文件。'
      void operations.renderCanvas(true)
      return
    }
    files.value = pdfs
    unavailableFiles.value = []
    activeIndex.value = -1
    activeFile.value = null
    operations.showNotice(`已登记 ${pdfs.length} 份对照 PDF；PCF 请在下方单独选择文件夹。`)
    void operations.renderCanvas(true)
  }

  function setPcfFiles(value) {
    pcfFiles.value = operations.normalizeFiles(value).filter(file => file.name?.toLowerCase().endsWith('.pcf'))
  }

  function setPcfFolderFiles(value) {
    const rawFiles = operations.normalizeFiles(value)
    pcfFiles.value = rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pcf'))
      .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN'))
    if (rawFiles.length && !pcfFiles.value.length) error.value = '所选 PCF 文件夹中没有 PCF 文件。'
  }

  function matched(pageNumber) {
    const researchedPage = (result.value?.pages || []).find(page => page.page === pageNumber)
    return resolveMatchedReference(researchedPage, files.value)
  }

  async function select(index) {
    if (projectMode.value === 'folder' && !researchReady.value) return
    activeIndex.value = index
    activeFile.value = files.value[index] || null
    const match = matched(currentPage.value)
    activePage.value = match?.index === index ? match.page : 1
    await loadPdf(activeFile.value)
  }

  async function materialize(file) {
    if (!file || file instanceof Blob) return file
    if (file.storedFileKey) return operations.materializeStoredFile(file)
    if (!file.lazyReference || !file.url) return file
    const slot = referenceFileSlot(file, file.index)
    if (lazyFileCache.has(slot)) {
      const cached = lazyFileCache.get(slot)
      lazyFileCache.delete(slot)
      lazyFileCache.set(slot, cached)
      return await cached
    }
    const pending = api.getJobReferenceFile(file)
    lazyFileCache.set(slot, pending)
    let loaded
    try {
      loaded = await pending
    } catch (cause) {
      if (lazyFileCache.get(slot) === pending) lazyFileCache.delete(slot)
      throw cause
    }
    if (lazyFileCache.get(slot) === pending) lazyFileCache.set(slot, loaded)
    while (lazyFileCache.size > MAX_LAZY_REFERENCE_FILES) lazyFileCache.delete(lazyFileCache.keys().next().value)
    return loaded
  }

  async function loadPdf(file, fitAfter = true) {
    const token = ++loadToken
    if (!file) {
      await operations.renderCanvas(fitAfter)
      return
    }
    if (operations.shouldRenderReference?.() === false) {
      operations.invalidateCanvas()
      const previousDocument = document.value
      document.value = null
      previousDocument?.destroy?.()
      activeFile.value = file
      await operations.renderCanvas(fitAfter)
      return
    }
    try {
      const resolvedFile = await materialize(file)
      if (token !== loadToken) return
      const previousDocument = document.value
      if (previousDocument) {
        operations.invalidateCanvas()
        document.value = null
        previousDocument.destroy?.()
      }
      activeFile.value = resolvedFile
      const loadedDocument = await loadPdfDocument(resolvedFile)
      if (token !== loadToken) { loadedDocument.destroy?.(); return }
      document.value = loadedDocument
      await nextTick()
      await operations.renderCanvas(fitAfter)
    } catch (cause) {
      if (token === loadToken) error.value = `对照 PDF 渲染失败：${cause.message || cause}`
    }
  }

  async function syncForPage(pageNumber, fitAfter = true) {
    if (!researchReady.value) {
      await operations.renderCanvas(fitAfter)
      return
    }
    const match = matched(pageNumber)
    if (!match) {
      clearDisplay()
      await operations.renderCanvas(fitAfter)
      return
    }
    activePage.value = match.page
    const matchedPdf = files.value[match.index]
    if (sameSlot(activeFile.value, matchedPdf, match.index) && document.value) {
      activeIndex.value = match.index
      await operations.renderCanvas(fitAfter)
      return
    }
    activeIndex.value = match.index
    activeFile.value = matchedPdf
    await loadPdf(activeFile.value, fitAfter)
  }

  async function jumpToHint(item) {
    const documentInfo = operations.getHintDocument()
    const pageInfo = operations.getHintPage()
    if (!documentInfo || !pageInfo || !item?.point) return
    const nextFocus = { file: documentInfo.file, page: pageInfo.page, point: item.point, label: item.label, type: item.type }
    const alreadyFocused = sameReferenceHint(focus.value, nextFocus)
    showAll.value = false
    const index = resolveReferenceFileIndex(documentInfo.file, files.value)
    if (index < 0) { error.value = '对应的对照 PDF 当前未加载。'; return }
    activeIndex.value = index
    activePage.value = pageInfo.page
    if (!alreadyFocused) focus.value = nextFocus
    if (!sameSlot(activeFile.value, files.value[index], index) || !document.value) {
      activeFile.value = files.value[index]
      await loadPdf(activeFile.value, false)
    } else {
      await operations.renderCanvas(false)
    }
    if (!alreadyFocused) return
    operations.locateReferenceHint?.(nextFocus)
    await nextTick()
    const layout = operations.canvasLayout.value.reference
    const viewport = operations.canvasViewport.value
    if (!layout || !viewport) return
    const worldX = layout.x + Number(item.point[0]) / Math.max(1, pageInfo.width) * layout.width
    const worldY = layout.y + Number(item.point[1]) / Math.max(1, pageInfo.height) * layout.height
    const nextZoom = Math.max(2.2, operations.zoom.value)
    operations.zoom.value = nextZoom
    operations.pan.value = {
      x: viewport.clientWidth / 2 - worldX * nextZoom,
      y: viewport.clientHeight / 2 - worldY * nextZoom,
    }
    operations.scheduleResolutionRender()
  }

  function toggleAllHints() {
    focus.value = null
    showAll.value = !showAll.value
    operations.fitCanvas()
  }

  async function archiveLazyFiles() {
    const descriptors = [...files.value]
    const workspaceResults = [...projectResults.value]
    const activeResultIndex = workspaceResults.indexOf(result.value)
    if (activeResultIndex < 0 && result.value) workspaceResults.push(result.value)
    const deferredGroups = workspaceResults.map((item, projectIndex) => ({
      jobId: item?.jobId,
      projectIndex,
      pages: (item?.pages || []).filter(page => page.detailsLoaded === false && item?.jobId && item?.jobId !== 'tutorial-000207'),
    })).filter(group => group.pages.length)
    if (!descriptors.some(item => item?.lazyReference) && !deferredGroups.length) return
    const generation = ++archiveGeneration
    archiving.value = true
    const restored = new Array(descriptors.length)
    let cursor = 0
    const worker = async () => {
      while (cursor < descriptors.length && generation === archiveGeneration) {
        const index = cursor++
        restored[index] = await materialize(descriptors[index]).catch(() => descriptors[index])
      }
    }
    await new Promise(resolve => {
      if (typeof globalThis.requestIdleCallback === 'function') globalThis.requestIdleCallback(resolve, { timeout: 1500 })
      else window.setTimeout(resolve, 500)
    })
    await Promise.all([worker(), worker()])
    if (generation !== archiveGeneration) return
    if (descriptors.length) files.value = restored
    for (const group of deferredGroups) {
      if (generation !== archiveGeneration) return
      let pageCursor = 0
      const archivePage = async () => {
        while (pageCursor < group.pages.length && generation === archiveGeneration) {
          const placeholder = group.pages[pageCursor++]
          try {
            const response = await api.getJobPage(group.jobId, Number(placeholder.page))
            await operations.archiveWorkspacePage?.(group.projectIndex, response.page || response)
          } catch { /* Keep the lightweight page; a later archive pass can retry it. */ }
        }
      }
      await Promise.all([archivePage(), archivePage()])
    }
    if (generation !== archiveGeneration) return
    archiving.value = false
    operations.scheduleDraftSave()
  }

  function dispose() {
    cancelArchiving()
    loadToken += 1
    lazyFileCache.clear()
  }

  return {
    mode, projectMode, files, unavailableFiles, activeIndex, researchReady, activeFile,
    document, activePage, pcfFiles, focus, showAll, archiving,
    changeMode, changeProjectMode, setFiles, setFolderFiles, setPcfFiles, setPcfFolderFiles,
    select, jumpToHint, toggleAllHints, matched, syncForPage, materialize, archiveLazyFiles,
    loadPdf, clearDisplay, clearFiles, cancelArchiving, dispose,
  }
}
