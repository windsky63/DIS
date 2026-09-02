<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { api, encodeFile } from './api'
import { encodeCsv } from './csv'
import { deleteDraft, fileFingerprint, listDrafts, loadDraft, loadSnapshot, saveDraft, saveSnapshot } from './workspaceStorage'

let pdfjsModulePromise
let renderTasks = []
let renderGeneration = 0
let resolutionTimer
let markerReflowTimer
let draftSaveTimer
let duplicateJobResolver
const pageBitmapCache = new Map()

function loadPdfjs() {
  if (!pdfjsModulePromise) {
    pdfjsModulePromise = Promise.all([
      import('pdfjs-dist'),
      import('pdfjs-dist/build/pdf.worker.min.mjs?url')
    ]).then(([pdfjs, worker]) => {
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default
      return pdfjs
    })
  }
  return pdfjsModulePromise
}

const projectMode = ref('single')
const projectPdfs = ref([])
const projectResults = ref([])
const activeProjectIndex = ref(0)
const targetPdf = ref(null)
const targetDocument = shallowRef(null)
const targetLoadToken = ref(0)
const referenceMode = ref('pdf')
const referenceProjectMode = ref('single')
const referencePdfs = ref([])
const activeReferenceIndex = ref(0)
const referenceResearchReady = ref(false)
const referencePdf = ref(null)
const referenceDocument = shallowRef(null)
const referenceLoadToken = ref(0)
const activeReferencePage = ref(1)
const pcfFiles = ref([])
const pcfFolderOptions = ref([])
const selectedPcfFolder = ref(null)
const startPage = ref(1)
const endPage = ref('')
const startNumber = ref(1)
const numberPrefix = ref('')
const numberSuffix = ref('')
const useReferenceNumber = ref(true)
const detectionMode = ref('placement')
const loading = ref(false)
const activeAnalysisJobId = ref('')
const cancellingAnalysis = ref(false)
const duplicateJobDialog = ref(false)
const duplicateJobInfo = ref(null)
const progressPanelOpen = ref(true)
const rendering = ref(false)
const leftDrawerOpen = ref(true)
const leftPanelTab = ref('input')
const reviewDrawerOpen = ref(true)
const systemSettingsDialog = ref(false)
const analysisProgress = ref({ fileIndex: 0, fileCount: 1, fileName: '', completedPages: 0, totalPages: 1, completedReferenceFiles: 0, totalReferenceFiles: 0, progressCompletedUnits: 0, progressTotalUnits: 1, message: '准备图元研究' })
const error = ref('')
const notice = ref('')
const health = ref('checking')
const mineruHealth = ref('checking')
const mineruStatusText = ref('正在连接')
let healthTimer
const result = ref(null)
const currentPage = ref(1)
const previewPage = ref(1)
const selectedId = ref('')
const editingId = ref('')
const editingValue = ref('')
const manualAddMode = ref(false)
const manualAddType = ref('weld')
const undoStack = ref([])
const redoStack = ref([])
const pendingHistory = ref(null)
const applyingHistory = ref(false)
const activeFileFingerprint = ref('')
const pendingDraft = ref(null)
const recoveryOptions = ref([])
const selectedRecoveryKey = ref('')
const draftRecoveryDialog = ref(false)
const draftManagerDialog = ref(false)
const draftManagerLoading = ref(false)
const draftEntries = ref([])
const draftDeleteTarget = ref(null)
const renumberConfirmDialog = ref(false)
const pendingRenumberId = ref('')
const anchorDragState = ref(null)
const canvasViewport = ref(null)
const canvasSurface = ref(null)
const pdfCanvas = ref(null)
const pcfSelector = ref(null)
const pcfSelectorWidth = ref(0)
const appViewport = ref({ width: window.innerWidth, height: window.innerHeight })
const leftDrawerWidth = computed(() => Math.round(appViewport.value.width * 0.15))
const reviewDrawerWidth = computed(() => Math.round(appViewport.value.width * 0.15))
const appHeaderHeight = computed(() => Math.round(appViewport.value.height * 0.06))
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const canvasPanState = ref(null)
const referenceFocus = ref(null)
const labelDragState = ref(null)
const canvasLayout = ref({ width: 1, height: 1, target: { x: 0, y: 34, width: 1, height: 1 }, reference: null })
const markerStyle = ref({ shape: 'rectangle', frameSize: 30, fontSize: 18, lineWidth: 1.35, color: '#d4143c', fillOpacity: 0.58 })
const componentMarkerStyles = ref({
  valve: { shape: 'rectangle', frameSize: 30, fontSize: 18, lineWidth: 1.35, color: '#1769d2', fillOpacity: 0.58 },
  flange: { shape: 'rectangle', frameSize: 30, fontSize: 18, lineWidth: 1.35, color: '#1769d2', fillOpacity: 0.58 },
  support: { shape: 'rectangle', frameSize: 30, fontSize: 18, lineWidth: 1.35, color: '#1769d2', fillOpacity: 0.58 }
})
const markerAppearanceTab = ref('weld')
const symbolConfigDialog = ref(false)
const shortcutMenu = ref(false)
const swapSourceId = ref('')
const shortcutDefaults = Object.freeze({ analyze: 'Ctrl+Enter', save: 'Ctrl+S', addWeld: 'W', addValve: 'V', addFlange: 'F', addSupport: 'S', swapWeld: 'X', deleteWeld: 'Delete' })
function loadShortcutSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem('weld-marker.shortcuts') || '{}')
    const legacyAddDefaults = stored.addWeld === 'A' && (!stored.addValve || stored.addValve === 'V') && (!stored.addFlange || stored.addFlange === 'L') && (!stored.addSupport || stored.addSupport === 'P')
    return { ...shortcutDefaults, ...stored, ...(legacyAddDefaults ? { addWeld: 'W', addValve: 'V', addFlange: 'F', addSupport: 'S' } : {}) }
  } catch { return { ...shortcutDefaults } }
}
const shortcutSettings = ref(loadShortcutSettings())
const shortcutSettingRows = Object.freeze([
  { action: 'analyze', label: '开始智能编号' },
  { action: 'save', label: '保存校对结果' },
  { action: 'addWeld', label: '人工添加焊口' },
  { action: 'addValve', label: '人工增加阀门' },
  { action: 'addFlange', label: '人工增加法兰' },
  { action: 'addSupport', label: '人工增加支架' },
  { action: 'swapWeld', label: '交换焊口编号' },
  { action: 'deleteWeld', label: '删除选中焊口' }
])
const shortcutModifierOptions = Object.freeze([
  { title: '无', value: '' },
  { title: 'Ctrl', value: 'Ctrl' },
  { title: 'Alt', value: 'Alt' },
  { title: 'Shift', value: 'Shift' },
  { title: 'Meta', value: 'Meta' },
  { title: 'Ctrl + Shift', value: 'Ctrl+Shift' },
  { title: 'Ctrl + Alt', value: 'Ctrl+Alt' },
  { title: 'Alt + Shift', value: 'Alt+Shift' },
  { title: 'Ctrl + Alt + Shift', value: 'Ctrl+Alt+Shift' }
])
const shortcutEditors = ref(Object.fromEntries(
  shortcutSettingRows.map(({ action }) => [action, shortcutParts(shortcutSettings.value[action])])
))
const tooltipsEnabled = ref(localStorage.getItem('weld-marker.tooltips-enabled') === 'true')
const weldSymbolConfig = ref({
  fixedSymbolPolicy: 'complete-signature',
  markerPolicy: 'strong-process-projection',
  minimumConfidence: 0,
  placementSymbols: {
    blackCircleEnabled: true,
    baseVectorShape: 'circle',
    approximateCircleEnabled: false,
    plainCircleEnabled: true,
    prefabricatedXEnabled: true,
    socketThreadBracketEnabled: true,
    mainPipeOnly: true,
    includeResearchFallback: false,
    minimumDiameter: 1.5,
    maximumDiameter: 14,
    darkThreshold: 0.25,
    mainPipeTolerance: 3.5
  },
  comparisonSymbols: {
    redFrameEnabled: true,
    redLabelPattern: '(?:F|FS|RP|S)\\d+',
    minimumConfidence: 0.75
  }
})

const pages = computed(() => result.value?.pages || [])
const pageData = computed(() => pages.value.find(item => item.page === currentPage.value) || pages.value[0] || null)
const candidates = computed(() => pages.value.flatMap(page => page.candidates || []))
const activeCandidates = computed(() => candidates.value.filter(item => item.included !== false))
const selectedPcfFolderInfo = computed(() => pcfFolderOptions.value.find(item => item.value === selectedPcfFolder.value) || null)
const canUndo = computed(() => undoStack.value.length > 0)
const canRedo = computed(() => redoStack.value.length > 0)
function pcfOption(item) { return item?.raw && typeof item.raw === 'object' ? item.raw : (item || {}) }
function pcfSelectionLabel(item) {
  const option = pcfOption(item)
  const title = option.title || option.value || selectedPcfFolder.value
  return title ? `${title}${Number.isFinite(Number(option.count)) ? ` · ${option.count} 个 PCF` : ''}` : ''
}
function selectRecoveryVersion(key) {
  selectedRecoveryKey.value = key
  pendingDraft.value = recoveryOptions.value.find(item => item.key === key) || recoveryOptions.value[0] || null
}
function recoveryOption(item) { return item?.raw && typeof item.raw === 'object' ? item.raw : (item || {}) }
function syncPcfMenuWidth() {
  void nextTick(() => {
    const element = pcfSelector.value?.$el || pcfSelector.value
    const width = Math.round(element?.getBoundingClientRect?.().width || 0)
    if (width > 0) pcfSelectorWidth.value = width
  })
}
const selectedCandidate = computed(() => candidates.value.find(item => item.id === selectedId.value) || null)
const pendingRenumberCandidate = computed(() => candidates.value.find(item => item.id === pendingRenumberId.value) || null)
const pendingRenumberCount = computed(() => {
  const candidate = pendingRenumberCandidate.value
  return candidate ? (pages.value.find(page => page.page === candidate.page)?.candidates || []).filter(item => item.included !== false && sameNumberingGroup(item, candidate)).length : 0
})
const matchedCount = computed(() => candidates.value.filter(item => item.referenceMatched).length)
const validatedCount = computed(() => candidates.value.filter(item => item.glyphValidated).length)
const designComponentCount = computed(() => candidates.value.filter(item => isDesignComponent(item)).length)
const weldCandidateCount = computed(() => candidates.value.length - designComponentCount.value)
const referenceHintDocument = computed(() => {
  const documents = result.value?.ep3dReferenceInventory?.documents || []
  const fileName = referencePdf.value?.name || ''
  return documents.find(document => document.file === fileName || document.file.endsWith(`__${fileName}`))
    || documents.find(document => document.file === pageData.value?.reference?.referenceFile)
    || null
})
const referenceHintPage = computed(() => referenceHintDocument.value?.pages?.find(page => page.page === activeReferencePage.value) || null)
const referenceHintGroups = computed(() => {
  const definitions = [
    ['weld', '焊口', '#d4143c'], ['valve', '阀门', '#1769d2'],
    ['flange', '法兰', '#1769d2'], ['support', '支架', '#1769d2']
  ]
  const items = referenceHintPage.value?.items || []
  return definitions.map(([key, title, color]) => ({ key, title, color, items: items.filter(item => item.type === key) }))
})
const referenceFocusStyle = computed(() => {
  const focus = referenceFocus.value
  const page = referenceHintPage.value
  const layout = canvasLayout.value.reference
  if (!focus || !page || !layout || focus.page !== page.page || focus.file !== referenceHintDocument.value?.file) return { display: 'none' }
  return {
    left: `${layout.x + Number(focus.point[0]) / Math.max(1, page.width) * layout.width}px`,
    top: `${layout.y + Number(focus.point[1]) / Math.max(1, page.height) * layout.height}px`
  }
})
const projectProgress = computed(() => projectResults.value.filter(item => item && item.status !== 'processing').length)

watch(() => result.value?.ep3dReferenceInventory, inventory => {
  if (targetPdf.value && inventory?.documents?.length) leftPanelTab.value = 'reference'
})
const analysisProgressPercent = computed(() => {
  const state = analysisProgress.value
  const files = Math.max(1, Number(state.fileCount) || 1)
  const finishedBefore = Math.max(0, Number(state.fileIndex) || 0)
  const totalUnits = Math.max(1, Number(state.progressTotalUnits) || Number(state.totalPages) || 1)
  const completedUnits = Number.isFinite(Number(state.progressCompletedUnits))
    ? Number(state.progressCompletedUnits)
    : Number(state.completedPages) || 0
  const currentFraction = Math.max(0, Math.min(1, completedUnits / totalUnits))
  return Math.max(0, Math.min(100, (finishedBefore + currentFraction) / files * 100))
})
const shownPage = computed(() => result.value ? currentPage.value : previewPage.value)
const targetPageCount = computed(() => targetDocument.value?.numPages || 0)
const errorOpen = computed({ get: () => Boolean(error.value), set: value => { if (!value) error.value = '' } })
const noticeOpen = computed({ get: () => Boolean(notice.value), set: value => { if (!value) notice.value = '' } })
const canvasTransform = computed(() => ({ transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${zoom.value})` }))
const canvasSurfaceStyle = computed(() => ({ width: `${canvasLayout.value.width}px`, height: `${canvasLayout.value.height}px`, ...canvasTransform.value }))
const shapeOptions = [
  { title: '方形框（默认）', value: 'rectangle' },
  { title: '圆形', value: 'circle' },
  { title: '菱形', value: 'diamond' },
]
const markerAppearanceGroups = computed(() => [
  { key: 'weld', title: '焊口标识', subtitle: '默认红色方形框', style: markerStyle.value },
  { key: 'valve', title: '阀门标识', subtitle: '编号前缀 V', style: componentMarkerStyles.value.valve },
  { key: 'flange', title: '法兰标识', subtitle: '编号前缀 FL', style: componentMarkerStyles.value.flange },
  { key: 'support', title: '支架标识', subtitle: '编号前缀 SP', style: componentMarkerStyles.value.support }
])
const activeMarkerAppearanceGroup = computed(() => markerAppearanceGroups.value.find(group => group.key === markerAppearanceTab.value) || markerAppearanceGroups.value[0])
const symbolPolicyOptions = [
  { title: '完整图元签名（推荐）', value: 'complete-signature' },
  { title: '允许部分图元证据', value: 'partial-evidence' }
]
const markerPolicyOptions = [
  { title: '强过程线投影（研究默认）', value: 'strong-process-projection' },
  { title: '全部紧凑填充标记', value: 'all-compact-filled' }
]
const shortcutConflict = computed(() => {
  const entries = Object.entries(shortcutSettings.value).map(([action, value]) => [action, normalizeShortcut(value)]).filter(([, value]) => value)
  const duplicate = entries.find(([, value], index) => entries.findIndex(([, other]) => other === value) !== index)
  return duplicate ? `快捷键 ${duplicate[1]} 被重复使用，请修改后再操作。` : ''
})
watch(
  () => markerAppearanceGroups.value.flatMap(group => [group.style.frameSize, group.style.fontSize, group.style.shape]),
  () => scheduleMarkerReflow()
)
watch(shortcutSettings, value => localStorage.setItem('weld-marker.shortcuts', JSON.stringify(value)), { deep: true })
watch(tooltipsEnabled, value => {
  localStorage.setItem('weld-marker.tooltips-enabled', String(value))
  document.body.classList.toggle('tooltips-disabled', !value)
}, { immediate: true })
watch(() => result.value?.pages, () => scheduleDraftSave(), { deep: true })
watch(
  [referenceMode, referenceProjectMode, activeReferenceIndex, activeReferencePage, selectedPcfFolder, useReferenceNumber],
  () => scheduleDraftSave()
)
watch(
  () => [referencePdfs.value, pcfFiles.value].map(files => files.map(file => `${file.name}:${file.size}:${file.lastModified}`).join('|')),
  () => scheduleDraftSave()
)

function cloneValue(value) { return value == null ? value : JSON.parse(JSON.stringify(value)) }
function storeWorkspaceFile(file) {
  if (!(file instanceof Blob)) return null
  return {
    blob: file.slice(0, file.size, file.type || 'application/octet-stream'),
    name: file.name || 'file',
    type: file.type || '',
    lastModified: Number(file.lastModified) || Date.now(),
    relativePath: file.webkitRelativePath || ''
  }
}
function restoreWorkspaceFile(saved) {
  if (!saved?.blob || !(saved.blob instanceof Blob)) return null
  const file = new File([saved.blob], saved.name || 'file', { type: saved.type || saved.blob.type, lastModified: Number(saved.lastModified) || Date.now() })
  if (saved.relativePath) Object.defineProperty(file, 'webkitRelativePath', { configurable: true, value: saved.relativePath })
  return file
}
function firstSavedStyle(documentResult, componentType = '') {
  return (documentResult?.pages || []).flatMap(page => page.candidates || []).find(item => (
    componentType ? item.componentType === componentType : !isDesignComponent(item)
  ))?.markerStyle || null
}
function restoreMarkerAppearances(payload) {
  const documentResult = payload?.result
  const savedGroups = payload?.markerStyles || documentResult?.markerStyles || {}
  markerStyle.value = cloneValue(savedGroups.weld || payload?.markerStyle || documentResult?.markerStyle || firstSavedStyle(documentResult) || markerStyle.value)
  const savedComponents = payload?.componentMarkerStyles || savedGroups.components || documentResult?.componentMarkerStyles || {}
  componentMarkerStyles.value = Object.fromEntries(['valve', 'flange', 'support'].map(componentType => [
    componentType,
    cloneValue(savedComponents[componentType] || firstSavedStyle(documentResult, componentType) || componentMarkerStyles.value[componentType])
  ]))
}
function currentMarkerAppearancePayload() {
  const weld = cloneValue(markerStyle.value)
  const components = cloneValue(componentMarkerStyles.value)
  return { markerStyle: weld, componentMarkerStyles: components, markerStyles: { weld, components } }
}
function logAudit(action, details = {}) { void api.audit(action, { jobId: result.value?.jobId || null, file: targetPdf.value?.name || null, page: currentPage.value, ...details }).catch(() => {}) }
function beginHistory(description) {
  if (applyingHistory.value || pendingHistory.value || !result.value) return
  pendingHistory.value = { description, pages: cloneValue(pages.value), markerStyle: cloneValue(markerStyle.value), componentMarkerStyles: cloneValue(componentMarkerStyles.value) }
}
function commitHistory() {
  if (!pendingHistory.value || !result.value) return
  const before = pendingHistory.value
  pendingHistory.value = null
  if (
    JSON.stringify(before.pages) === JSON.stringify(pages.value)
    && JSON.stringify(before.markerStyle) === JSON.stringify(markerStyle.value)
    && JSON.stringify(before.componentMarkerStyles) === JSON.stringify(componentMarkerStyles.value)
  ) return
  undoStack.value.push(before)
  if (undoStack.value.length > 50) undoStack.value.shift()
  redoStack.value = []
  scheduleDraftSave()
}
function applyWorkspaceSnapshot(snapshot) {
  if (!result.value || !snapshot) return
  applyingHistory.value = true
  result.value.pages = cloneValue(snapshot.pages)
  markerStyle.value = cloneValue(snapshot.markerStyle || markerStyle.value)
  componentMarkerStyles.value = cloneValue(snapshot.componentMarkerStyles || componentMarkerStyles.value)
  projectResults.value[activeProjectIndex.value] = result.value
  selectedId.value = ''
  applyingHistory.value = false
}
function undo() {
  const snapshot = undoStack.value.pop()
  if (!snapshot || !result.value) return
  redoStack.value.push({ description: snapshot.description, pages: cloneValue(pages.value), markerStyle: cloneValue(markerStyle.value), componentMarkerStyles: cloneValue(componentMarkerStyles.value) })
  applyWorkspaceSnapshot(snapshot)
  notice.value = `已撤销：${snapshot.description}`
  logAudit('undo', { description: snapshot.description })
}
function redo() {
  const snapshot = redoStack.value.pop()
  if (!snapshot || !result.value) return
  undoStack.value.push({ description: snapshot.description, pages: cloneValue(pages.value), markerStyle: cloneValue(markerStyle.value), componentMarkerStyles: cloneValue(componentMarkerStyles.value) })
  applyWorkspaceSnapshot(snapshot)
  notice.value = `已重做：${snapshot.description}`
  logAudit('redo', { description: snapshot.description })
}
function scheduleDraftSave() {
  clearTimeout(draftSaveTimer)
  if (!activeFileFingerprint.value || !result.value || result.value.status === 'processing' || applyingHistory.value) return
  draftSaveTimer = setTimeout(() => {
    const payload = {
      result: cloneValue(result.value),
      projectResults: cloneValue(projectResults.value),
      activeProjectIndex: activeProjectIndex.value,
      markerStyle: cloneValue(markerStyle.value),
      componentMarkerStyles: cloneValue(componentMarkerStyles.value),
      currentPage: currentPage.value,
      fileName: targetPdf.value?.name || '',
      targetFile: storeWorkspaceFile(targetPdf.value),
      projectFiles: projectPdfs.value.map(storeWorkspaceFile).filter(Boolean),
      detectionMode: detectionMode.value,
      referenceMode: referenceMode.value,
      referenceProjectMode: referenceProjectMode.value,
      referenceFiles: referencePdfs.value.map(storeWorkspaceFile).filter(Boolean),
      pcfFiles: pcfFiles.value.map(storeWorkspaceFile).filter(Boolean),
      selectedPcfFolder: selectedPcfFolder.value,
      referenceResearchReady: referenceResearchReady.value,
      activeReferenceIndex: activeReferenceIndex.value,
      activeReferencePage: activeReferencePage.value,
      useReferenceNumber: useReferenceNumber.value
    }
    void saveDraft(activeFileFingerprint.value, payload).then(() => logAudit('draft.autosaved')).catch(() => {})
  }, 900)
}

function draftCandidateCount(entry) {
  return (entry?.payload?.result?.pages || []).reduce((total, page) => total + (page.candidates || []).length, 0)
}
function draftSavedAt(entry) {
  const timestamp = Date.parse(entry?.savedAt || '')
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString('zh-CN', { hour12: false }) : '时间未知'
}
function draftHasStoredTarget(entry) {
  return Boolean(entry?.payload?.targetFile?.blob || entry?.payload?.projectFiles?.some(item => item?.blob))
}
function canOpenManagedDraft(entry) {
  return draftHasStoredTarget(entry) || Boolean(targetPdf.value && targetPdf.value.name === entry?.payload?.fileName)
}
async function openDraftManager() {
  systemSettingsDialog.value = false
  draftManagerDialog.value = true
  draftManagerLoading.value = true
  try { draftEntries.value = await listDrafts() }
  catch (cause) { error.value = `读取浏览器草稿失败：${cause.message || cause}` }
  finally { draftManagerLoading.value = false }
}
async function openManagedDraft(entry) {
  const payload = entry?.payload
  if (!payload?.result) { error.value = '该草稿没有可恢复的识别结果。'; return }
  const storedProjectFiles = (payload.projectFiles || []).map(restoreWorkspaceFile).filter(Boolean)
  const storedTargetFile = restoreWorkspaceFile(payload.targetFile)
  const availableFiles = storedProjectFiles.length ? storedProjectFiles : (storedTargetFile ? [storedTargetFile] : [])
  if (availableFiles.length) {
    projectPdfs.value = availableFiles
    projectMode.value = availableFiles.length > 1 ? 'folder' : 'single'
    activeProjectIndex.value = Math.max(0, Math.min(Number(payload.activeProjectIndex) || 0, availableFiles.length - 1))
    targetPdf.value = availableFiles[activeProjectIndex.value]
    projectResults.value = Array.isArray(payload.projectResults) ? cloneValue(payload.projectResults) : new Array(availableFiles.length).fill(null)
    await loadTargetPdf(targetPdf.value, true, false)
  } else if (!targetPdf.value || targetPdf.value.name !== payload.fileName) {
    error.value = `旧草稿“${payload.fileName || '未命名图纸'}”未保存主图文件，请先在工程输入中选择同名 PDF 后再恢复。`
    return
  }
  activeFileFingerprint.value = entry.key
  pendingDraft.value = { key: entry.key, title: payload.fileName || '浏览器草稿', source: 'indexeddb', priority: 1, payload, savedAt: entry.savedAt }
  draftManagerDialog.value = false
  await restoreWorkspaceDraft()
}
async function deleteManagedDraft() {
  const entry = draftDeleteTarget.value
  if (!entry) return
  try {
    await deleteDraft(entry.key)
    draftEntries.value = draftEntries.value.filter(item => item.key !== entry.key)
    draftDeleteTarget.value = null
    notice.value = `已删除草稿“${entry.payload?.fileName || '未命名图纸'}”。`
  } catch (cause) { error.value = `删除草稿失败：${cause.message || cause}` }
}

async function refreshServiceHealth() {
  try {
    const status = await api.health()
    health.value = status.status === 'ready' ? 'ready' : 'offline'
    mineruHealth.value = status.mineru?.status || 'offline'
    mineruStatusText.value = status.mineru?.message || (mineruHealth.value === 'ready' ? '服务就绪' : '服务未就绪')
  } catch {
    health.value = 'offline'
    mineruHealth.value = 'offline'
    mineruStatusText.value = '本地后端未连接，无法检测 MinerU'
  }
}

onMounted(async () => {
  window.addEventListener('resize', syncAppViewport)
  await refreshServiceHealth()
  try {
    const response = await api.getPcfFolders()
    pcfFolderOptions.value = Array.isArray(response?.folders)
      ? response.folders.filter(item => item && typeof item === 'object' && item.value && item.title)
      : []
  } catch { pcfFolderOptions.value = [] }
  healthTimer = window.setInterval(refreshServiceHealth, 10000)
  window.addEventListener('keydown', handleShortcut)
})

onBeforeUnmount(() => {
  clearTimeout(resolutionTimer)
  clearTimeout(markerReflowTimer)
  clearTimeout(draftSaveTimer)
  clearInterval(healthTimer)
  window.removeEventListener('keydown', handleShortcut)
  window.removeEventListener('resize', syncAppViewport)
  cancelRenderTasks()
  targetDocument.value?.destroy?.()
  referenceDocument.value?.destroy?.()
  duplicateJobResolver?.('cancel')
})

function syncAppViewport() {
  appViewport.value = { width: window.innerWidth, height: window.innerHeight }
}

function confirmDuplicateJob(job, fileName) {
  duplicateJobInfo.value = { ...job, fileName }
  duplicateJobDialog.value = true
  return new Promise(resolve => { duplicateJobResolver = resolve })
}

function resolveDuplicateJob(choice) {
  duplicateJobDialog.value = false
  duplicateJobInfo.value = null
  const resolve = duplicateJobResolver
  duplicateJobResolver = null
  resolve?.(choice)
}

function normalizeShortcut(value) {
  const parts = String(value || '').split('+').map(part => part.trim()).filter(Boolean)
  if (!parts.length) return ''
  const key = parts.pop()
  const modifiers = new Set(parts.map(part => part.toLowerCase()))
  return [modifiers.has('ctrl') ? 'Ctrl' : '', modifiers.has('alt') ? 'Alt' : '', modifiers.has('shift') ? 'Shift' : '', modifiers.has('meta') ? 'Meta' : '', key.length === 1 ? key.toUpperCase() : key].filter(Boolean).join('+')
}

function shortcutFromEvent(event) {
  const key = event.key === ' ' ? 'Space' : event.key === '+' ? 'Plus' : event.key === '-' ? 'Minus' : event.key.length === 1 ? event.key.toUpperCase() : event.key
  return [event.ctrlKey ? 'Ctrl' : '', event.altKey ? 'Alt' : '', event.shiftKey ? 'Shift' : '', event.metaKey ? 'Meta' : '', key].filter(Boolean).join('+')
}

function matchesShortcut(event, configured) { return normalizeShortcut(shortcutFromEvent(event)) === normalizeShortcut(configured) }
function shortcutParts(value) {
  const parts = normalizeShortcut(value).split('+').filter(Boolean)
  const key = parts.pop() || ''
  return { modifier: parts.join('+'), key }
}
function commitShortcutEditor(action) {
  const editor = shortcutEditors.value[action] || { modifier: '', key: '' }
  shortcutSettings.value[action] = normalizeShortcut([editor.modifier, editor.key].filter(Boolean).join('+'))
}
function updateShortcutModifier(action, modifier) {
  shortcutEditors.value[action].modifier = modifier || ''
  commitShortcutEditor(action)
}
function restoreDefaultShortcuts() {
  shortcutSettings.value = { ...shortcutDefaults }
  shortcutEditors.value = Object.fromEntries(shortcutSettingRows.map(({ action }) => [action, shortcutParts(shortcutDefaults[action])]))
  notice.value = '快捷键已恢复为系统默认值。'
}
function captureShortcutKey(event, action) {
  if (event.key === 'Tab') return
  event.preventDefault()
  if (event.key === 'Backspace') {
    shortcutEditors.value[action].key = ''
    commitShortcutEditor(action)
    return
  }
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(event.key)) return
  shortcutEditors.value[action].key = event.key === ' ' ? 'Space' : event.key === '+' ? 'Plus' : event.key === '-' ? 'Minus' : event.key.length === 1 ? event.key.toUpperCase() : event.key
  commitShortcutEditor(action)
}

function handleShortcut(event) {
  const tag = event.target?.tagName?.toLowerCase()
  const isEditing = ['input', 'textarea', 'select'].includes(tag) || event.target?.isContentEditable
  if (event.key === 'F1') { event.preventDefault(); shortcutMenu.value = !shortcutMenu.value; return }
  if (isEditing && event.key !== 'Escape') return
  if ((draftRecoveryDialog.value || draftManagerDialog.value || draftDeleteTarget.value || renumberConfirmDialog.value || systemSettingsDialog.value || symbolConfigDialog.value) && event.key !== 'Escape') return
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') { event.preventDefault(); undo(); return }
  if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || (event.shiftKey && event.key.toLowerCase() === 'z'))) { event.preventDefault(); redo(); return }
  if (matchesShortcut(event, shortcutSettings.value.analyze)) { event.preventDefault(); if (!shortcutConflict.value && !loading.value && targetPdf.value) void analyze(); return }
  if (matchesShortcut(event, shortcutSettings.value.save)) { event.preventDefault(); if (!shortcutConflict.value && result.value) void save(); return }
  if (matchesShortcut(event, shortcutSettings.value.addWeld)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode(); return }
  if (matchesShortcut(event, shortcutSettings.value.addValve)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('valve'); return }
  if (matchesShortcut(event, shortcutSettings.value.addFlange)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('flange'); return }
  if (matchesShortcut(event, shortcutSettings.value.addSupport)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('support'); return }
  if (matchesShortcut(event, shortcutSettings.value.swapWeld)) { event.preventDefault(); if (!shortcutConflict.value) swapSelectedWeldNumbers(); return }
  if (matchesShortcut(event, shortcutSettings.value.deleteWeld)) { event.preventDefault(); if (!shortcutConflict.value) deleteSelectedWeld(); return }
  if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.25); return }
  if (event.key === '-') { event.preventDefault(); zoomBy(.8); return }
  if (event.key === '0') { event.preventDefault(); fitCanvas(); return }
  if (event.key === 'ArrowLeft') { event.preventDefault(); stepPage(-1); return }
  if (event.key === 'ArrowRight') { event.preventDefault(); stepPage(1); return }
  if (event.key === 'Escape') { cancelInlineEdit(); selectedId.value = ''; swapSourceId.value = ''; shortcutMenu.value = false; manualAddMode.value = false }
}

function stepPage(direction) {
  if (result.value) {
    const pageNumbers = pages.value.map(page => page.page)
    const index = pageNumbers.indexOf(currentPage.value)
    const next = pageNumbers[Math.max(0, Math.min(pageNumbers.length - 1, index + direction))]
    if (next) void changePage(next)
    return
  }
  const next = Math.max(1, Math.min(targetPageCount.value, previewPage.value + direction))
  if (next !== previewPage.value) void changePage(next)
}

function normalizeFiles(value) {
  if (!value) return []
  return Array.isArray(value) ? value.filter(Boolean) : [value]
}

function clearProject() {
  targetLoadToken.value += 1
  targetDocument.value?.destroy?.()
  targetDocument.value = null
  projectPdfs.value = []
  projectResults.value = []
  activeProjectIndex.value = 0
  targetPdf.value = null
  result.value = null
  activeFileFingerprint.value = ''
  pendingDraft.value = null
  recoveryOptions.value = []
  selectedRecoveryKey.value = ''
  draftRecoveryDialog.value = false
  undoStack.value = []
  redoStack.value = []
  referenceResearchReady.value = false
  selectedId.value = ''
  swapSourceId.value = ''
  previewPage.value = 1
  error.value = ''
}

function changeProjectMode(mode) {
  projectMode.value = mode
  startPage.value = 1
  endPage.value = ''
  clearProject()
}

async function setProjectFiles(value) {
  const rawFiles = normalizeFiles(value)
  const pdfs = rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pdf'))
    .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN'))
  clearProject()
  if (!rawFiles.length) return
  if (!pdfs.length) { error.value = '所选内容中没有 PDF 文件。'; return }
  projectPdfs.value = pdfs
  projectResults.value = new Array(pdfs.length).fill(null)
  targetPdf.value = pdfs[0]
  await loadTargetPdf(pdfs[0], true)
}

async function loadTargetPdf(file, fitAfter = true, checkRecovery = true) {
  const token = targetLoadToken.value + 1
  targetLoadToken.value = token
  targetDocument.value?.destroy?.()
  targetDocument.value = null
  if (!file) return
  try {
    const pdfjs = await loadPdfjs()
    const document = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise
    if (token !== targetLoadToken.value) { document.destroy(); return }
    targetDocument.value = document
    activeFileFingerprint.value = await fileFingerprint(file)
    previewPage.value = 1
    await nextTick()
    await renderUnifiedCanvas(fitAfter)
    if (!checkRecovery) return
    const options = []
    const attachments = await document.getAttachments?.()
    for (const editableAttachment of Object.values(attachments || {})) {
      if (!editableAttachment?.content) continue
      try {
        const embedded = JSON.parse(new TextDecoder().decode(editableAttachment.content))
        if (embedded?.schema === 'weld-marker.editable.v1' && embedded.pages?.length) {
          options.push({ key: 'embedded', title: 'PDF 内嵌编辑数据', source: 'embedded', priority: 1, payload: { result: embedded, markerStyles: embedded.markerStyles, markerStyle: embedded.markerStyle || markerStyle.value, componentMarkerStyles: embedded.componentMarkerStyles, currentPage: embedded.analyzedRange?.[0] || 1 }, savedAt: '随当前 PDF 保存的可编辑版本' })
          break
        }
      } catch { /* attachment is not an editable result */ }
    }
    const draft = await loadDraft(activeFileFingerprint.value).catch(() => null)
    if (draft?.payload?.result?.pages?.length) {
      options.push({ ...draft, key: 'indexeddb', title: '浏览器自动草稿', source: 'indexeddb', priority: 2 })
    }
    const recent = await api.getRecentBatch().catch(() => null)
    const recentJobs = Array.isArray(recent?.jobs) ? recent.jobs.filter(item => item?.pages?.length) : []
    const byName = new Map(recentJobs.filter(item => item.originalTargetName).map(item => [item.originalTargetName, item]))
    const restoredProjects = projectPdfs.value.map(projectFile => byName.get(projectFile.name) || null)
    const preferredIndex = restoredProjects[activeProjectIndex.value]
      ? activeProjectIndex.value
      : restoredProjects.findIndex(Boolean)
    if (preferredIndex >= 0 && restoredProjects[preferredIndex]) {
      const restoredResult = restoredProjects[preferredIndex]
      options.push({
        key: 'recent-batch',
        title: '后端最近解析批次',
        source: 'recent-batch',
        savedAt: '后端最近解析批次',
        priority: 3,
        batchId: recent.batchId || null,
        payload: {
          result: restoredResult,
          projectResults: restoredProjects,
          activeProjectIndex: preferredIndex,
          markerStyles: restoredResult.markerStyles,
          markerStyle: restoredResult.markerStyle || markerStyle.value,
          componentMarkerStyles: restoredResult.componentMarkerStyles,
          currentPage: restoredResult.analyzedRange?.[0] || restoredResult.pages?.[0]?.page || 1,
        },
      })
    }
    if (options.length) {
      recoveryOptions.value = options.sort((a, b) => a.priority - b.priority)
      selectRecoveryVersion(recoveryOptions.value[0].key)
      draftRecoveryDialog.value = true
    }
  } catch (cause) {
    if (token === targetLoadToken.value) error.value = `PDF 渲染失败：${cause.message || cause}`
  }
}

async function restoreWorkspaceDraft() {
  const recovery = pendingDraft.value
  const payload = pendingDraft.value?.payload
  if (!payload?.result) return
  if (Array.isArray(payload.projectResults)) {
    projectResults.value = cloneValue(payload.projectResults)
    activeProjectIndex.value = Number(payload.activeProjectIndex) || 0
    targetPdf.value = projectPdfs.value[activeProjectIndex.value] || targetPdf.value
  }
  result.value = cloneValue(payload.result)
  restoreMarkerAppearances(payload)
  if (payload.detectionMode) detectionMode.value = payload.detectionMode
  if (payload.referenceMode) referenceMode.value = payload.referenceMode
  if (payload.referenceProjectMode) referenceProjectMode.value = payload.referenceProjectMode
  const restoredReferenceFiles = (payload.referenceFiles || []).map(restoreWorkspaceFile).filter(Boolean)
  const restoredPcfFiles = (payload.pcfFiles || []).map(restoreWorkspaceFile).filter(Boolean)
  if (restoredReferenceFiles.length) referencePdfs.value = restoredReferenceFiles
  if (restoredPcfFiles.length) pcfFiles.value = restoredPcfFiles
  selectedPcfFolder.value = payload.selectedPcfFolder || selectedPcfFolder.value
  useReferenceNumber.value = payload.useReferenceNumber ?? useReferenceNumber.value
  referenceResearchReady.value = payload.referenceResearchReady ?? true
  activeReferenceIndex.value = Math.max(0, Math.min(Number(payload.activeReferenceIndex) || 0, Math.max(0, referencePdfs.value.length - 1)))
  activeReferencePage.value = Math.max(1, Number(payload.activeReferencePage) || 1)
  referencePdf.value = referencePdfs.value[activeReferenceIndex.value] || null
  if (resultHasMissingNumbers(result.value)) {
    numberResult(result.value, Math.max(1, Number(startNumber.value) || 1))
    if (detectionMode.value === 'placement') reflowLabelPositions(result.value.pages || [])
    if (result.value.jobId) {
      const saved = await api.saveJob(result.value.jobId, result.value.pages, currentMarkerAppearancePayload(), result.value.revision ?? 0).catch(() => null)
      if (saved) result.value.revision = saved.revision
    }
  }
  currentPage.value = Number(payload.currentPage) || result.value.analyzedRange?.[0] || 1
  projectResults.value[activeProjectIndex.value] = result.value
  referenceResearchReady.value = payload.referenceResearchReady ?? true
  pendingDraft.value = null
  recoveryOptions.value = []
  selectedRecoveryKey.value = ''
  draftRecoveryDialog.value = false
  undoStack.value = []
  redoStack.value = []
  notice.value = '已恢复可编辑标识数据，可继续校对、移动和导出。'
  logAudit(recovery?.source === 'recent-batch' ? 'batch.recent_restored' : 'draft.restored', { batchId: recovery?.batchId || null })
  await nextTick()
  if (recovery?.source === 'recent-batch') await loadTargetPdf(targetPdf.value, true, false)
  if (referenceMode.value === 'pdf' && referencePdfs.value.length) {
    if (referenceProjectMode.value === 'folder' || matchedReference(currentPage.value)) await syncReferenceForPage(currentPage.value, true)
    else await loadReferencePdf(referencePdf.value, true)
  }
  else await renderUnifiedCanvas(true)
}

async function discardWorkspaceDraft() {
  if (activeFileFingerprint.value && pendingDraft.value?.source === 'indexeddb') await deleteDraft(activeFileFingerprint.value).catch(() => {})
  pendingDraft.value = null
  recoveryOptions.value = []
  selectedRecoveryKey.value = ''
  draftRecoveryDialog.value = false
  logAudit('draft.discarded')
}

function changeReferenceMode(mode) {
  referenceMode.value = mode
  referenceLoadToken.value += 1
  referenceDocument.value?.destroy?.()
  referenceDocument.value = null
  referencePdfs.value = []
  referenceResearchReady.value = false
  activeReferenceIndex.value = 0
  referencePdf.value = null
  activeReferencePage.value = 1
  pcfFiles.value = []
  selectedPcfFolder.value = null
  void renderUnifiedCanvas(true)
}

function clearReferenceFiles() {
  referenceLoadToken.value += 1
  referenceDocument.value?.destroy?.()
  referenceDocument.value = null
  referencePdfs.value = []
  referenceResearchReady.value = false
  activeReferenceIndex.value = -1
  referencePdf.value = null
}

function changeReferenceProjectMode(mode) {
  referenceProjectMode.value = mode
  clearReferenceFiles()
  pcfFiles.value = []
  selectedPcfFolder.value = null
  void renderUnifiedCanvas(true)
}

function setReferenceFiles(value) {
  const rawFiles = normalizeFiles(value)
  const pdfs = rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pdf'))
    .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN'))
  clearReferenceFiles()
  if (!rawFiles.length) { void renderUnifiedCanvas(true); return }
  if (!pdfs.length) { error.value = '所选对照内容中没有 PDF 文件。'; void renderUnifiedCanvas(true); return }
  referencePdfs.value = pdfs
  if (referenceProjectMode.value === 'folder') {
    activeReferenceIndex.value = -1
    referencePdf.value = null
    notice.value = `已登记 ${pdfs.length} 份对照 PDF；完成图元研究后将按匹配关系加载到 Canvas。`
    void renderUnifiedCanvas(true)
    return
  }
  activeReferenceIndex.value = 0
  activeReferencePage.value = 1
  referencePdf.value = pdfs[0]
  void loadReferencePdf(pdfs[0])
}

function setReferenceFolderFiles(value) {
  const rawFiles = normalizeFiles(value)
  const pdfs = rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pdf'))
    .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN'))
  clearReferenceFiles()
  if (!rawFiles.length) { void renderUnifiedCanvas(true); return }
  if (!pdfs.length) { error.value = '所选对照 PDF 文件夹中没有 PDF 文件。'; void renderUnifiedCanvas(true); return }
  referencePdfs.value = pdfs
  activeReferenceIndex.value = -1
  referencePdf.value = null
  notice.value = `已登记 ${pdfs.length} 份对照 PDF；PCF 请在下方单独选择文件夹。`
  void renderUnifiedCanvas(true)
}

function setPcfFiles(value) {
  pcfFiles.value = normalizeFiles(value).filter(file => file.name?.toLowerCase().endsWith('.pcf'))
}

function setPcfFolderFiles(value) {
  const rawFiles = normalizeFiles(value)
  pcfFiles.value = rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pcf'))
    .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN'))
  if (rawFiles.length && !pcfFiles.value.length) error.value = '所选 PCF 文件夹中没有 PCF 文件。'
}

async function selectReference(index) {
  if (referenceProjectMode.value === 'folder' && !referenceResearchReady.value) return
  activeReferenceIndex.value = index
  referencePdf.value = referencePdfs.value[index] || null
  const match = matchedReference(currentPage.value)
  activeReferencePage.value = match?.index === index ? match.page : 1
  await loadReferencePdf(referencePdf.value)
}

async function jumpToReferenceHint(item) {
  const documentInfo = referenceHintDocument.value
  const pageInfo = referenceHintPage.value
  if (!documentInfo || !pageInfo || !item?.point) return
  const index = referencePdfs.value.findIndex(file => documentInfo.file === file.name || documentInfo.file.endsWith(`__${file.name}`))
  if (index < 0) { error.value = '对应的对照 PDF 当前未加载。'; return }
  activeReferenceIndex.value = index
  activeReferencePage.value = pageInfo.page
  referenceFocus.value = { file: documentInfo.file, page: pageInfo.page, point: item.point, label: item.label, type: item.type }
  if (referencePdf.value !== referencePdfs.value[index] || !referenceDocument.value) {
    referencePdf.value = referencePdfs.value[index]
    await loadReferencePdf(referencePdf.value, false)
  } else {
    await renderUnifiedCanvas(false)
  }
  await nextTick()
  const layout = canvasLayout.value.reference
  const viewport = canvasViewport.value
  if (!layout || !viewport) return
  const worldX = layout.x + Number(item.point[0]) / Math.max(1, pageInfo.width) * layout.width
  const worldY = layout.y + Number(item.point[1]) / Math.max(1, pageInfo.height) * layout.height
  const nextZoom = Math.max(2.2, zoom.value)
  zoom.value = nextZoom
  pan.value = {
    x: viewport.clientWidth / 2 - worldX * nextZoom,
    y: viewport.clientHeight / 2 - worldY * nextZoom
  }
  scheduleResolutionRender()
}

function matchedReference(pageNumber) {
  const researchedPage = pages.value.find(page => page.page === pageNumber)
  const primaryFile = researchedPage?.reference?.referenceFile
    || researchedPage?.reference?.referencePages?.[0]?.file
    || (researchedPage?.candidates || []).find(item => item.referenceFile)?.referenceFile
  if (!primaryFile) return null
  const index = referencePdfs.value.findIndex(file => primaryFile === file.name || primaryFile.endsWith(`__${file.name}`))
  if (index < 0) return null
  const page = Number(researchedPage?.reference?.referencePage
    || researchedPage?.reference?.referencePages?.find(item => item.file === primaryFile)?.page
    || (researchedPage?.candidates || []).find(item => item.referenceFile === primaryFile)?.referencePage
    || 1)
  return { index, page: Math.max(1, page) }
}

async function syncReferenceForPage(pageNumber, fitAfter = true) {
  if (!referenceResearchReady.value) {
    await renderUnifiedCanvas(fitAfter)
    return
  }
  const match = matchedReference(pageNumber)
  if (!match && referenceProjectMode.value === 'folder') {
    referenceLoadToken.value += 1
    referenceDocument.value?.destroy?.()
    referenceDocument.value = null
    activeReferenceIndex.value = -1
    referencePdf.value = null
    await renderUnifiedCanvas(fitAfter)
    return
  }
  if (!match) { await renderUnifiedCanvas(fitAfter); return }
  activeReferencePage.value = match.page
  if (referenceProjectMode.value !== 'folder' || (referencePdf.value === referencePdfs.value[match.index] && referenceDocument.value)) {
    activeReferenceIndex.value = match.index
    await renderUnifiedCanvas(fitAfter)
    return
  }
  activeReferenceIndex.value = match.index
  referencePdf.value = referencePdfs.value[match.index]
  await loadReferencePdf(referencePdf.value, fitAfter)
}

async function loadReferencePdf(file, fitAfter = true) {
  const token = referenceLoadToken.value + 1
  referenceLoadToken.value = token
  referenceDocument.value?.destroy?.()
  referenceDocument.value = null
  if (!file) { await renderUnifiedCanvas(fitAfter); return }
  try {
    const pdfjs = await loadPdfjs()
    const document = await pdfjs.getDocument({ data: await file.arrayBuffer() }).promise
    if (token !== referenceLoadToken.value) { document.destroy(); return }
    referenceDocument.value = document
    await nextTick()
    await renderUnifiedCanvas(fitAfter)
  } catch (cause) {
    if (token === referenceLoadToken.value) error.value = `对照 PDF 渲染失败：${cause.message || cause}`
  }
}

async function selectProject(index) {
  activeProjectIndex.value = index
  targetPdf.value = projectPdfs.value[index] || null
  result.value = projectResults.value[index] || null
  currentPage.value = result.value?.analyzedRange?.[0] || 1
  previewPage.value = 1
  selectedId.value = ''
  swapSourceId.value = ''
  if (referenceProjectMode.value === 'folder') {
    referenceLoadToken.value += 1
    referenceDocument.value?.destroy?.()
    referenceDocument.value = null
    referencePdf.value = null
    activeReferenceIndex.value = -1
  }
  await loadTargetPdf(targetPdf.value, true, !result.value)
  if (result.value && referenceMode.value === 'pdf') await syncReferenceForPage(currentPage.value, true)
}

function cancelRenderTasks() {
  renderTasks.forEach(task => { try { task.cancel() } catch { /* already complete */ } })
  renderTasks = []
}

function bitmapCacheKey(kind, file, pageNumber, viewport, pixelFactor) {
  return `${kind}:${file?.name || 'pdf'}:${file?.size || 0}:${file?.lastModified || 0}:p${pageNumber}:${Math.ceil(viewport.width * pixelFactor)}x${Math.ceil(viewport.height * pixelFactor)}`
}

async function renderPageOffscreen(page, viewport, pixelFactor, cacheKey) {
  const width = Math.ceil(viewport.width * pixelFactor)
  const height = Math.ceil(viewport.height * pixelFactor)
  if (pageBitmapCache.has(cacheKey)) return pageBitmapCache.get(cacheKey)
  const persisted = await loadSnapshot(cacheKey).catch(() => null)
  if (persisted?.blob && persisted.width === width && persisted.height === height) {
    const bitmap = await createImageBitmap(persisted.blob)
    const restored = document.createElement('canvas')
    restored.width = width; restored.height = height
    restored.getContext('2d', { alpha: false }).drawImage(bitmap, 0, 0)
    bitmap.close?.()
    pageBitmapCache.set(cacheKey, restored)
    if (pageBitmapCache.size > 12) pageBitmapCache.delete(pageBitmapCache.keys().next().value)
    return restored
  }
  const buffer = document.createElement('canvas')
  buffer.width = width
  buffer.height = height
  const task = page.render({
    canvasContext: buffer.getContext('2d', { alpha: false }),
    viewport,
    transform: [pixelFactor, 0, 0, pixelFactor, 0, 0]
  })
  renderTasks.push(task)
  await task.promise
  pageBitmapCache.set(cacheKey, buffer)
  if (pageBitmapCache.size > 12) pageBitmapCache.delete(pageBitmapCache.keys().next().value)
  buffer.toBlob(blob => { if (blob) void saveSnapshot(cacheKey, blob, width, height).catch(() => {}) }, 'image/webp', .88)
  return buffer
}

async function renderUnifiedCanvas(fitAfter = false) {
  if (!targetDocument.value || !pdfCanvas.value) return
  const generation = ++renderGeneration
  cancelRenderTasks()
  rendering.value = true
  try {
    const pageNumber = Math.max(1, Math.min(targetDocument.value.numPages, Number(shownPage.value) || 1))
    const targetPage = await targetDocument.value.getPage(pageNumber)
    const referencePageNumber = referenceDocument.value ? Math.min(Math.max(1, activeReferencePage.value), referenceDocument.value.numPages) : 0
    const referencePage = referencePageNumber ? await referenceDocument.value.getPage(referencePageNumber) : null
    if (generation !== renderGeneration) return

    const baseScale = 1.35
    const targetViewport = targetPage.getViewport({ scale: baseScale })
    const referenceViewport = referencePage?.getViewport({ scale: baseScale }) || null
    const headerHeight = 34
    const gap = referenceViewport ? 28 : 0
    const referenceX = referenceViewport ? targetViewport.width + gap : 0
    const logicalWidth = targetViewport.width + (referenceViewport ? gap + referenceViewport.width : 0)
    const logicalHeight = headerHeight + Math.max(targetViewport.height, referenceViewport?.height || 0)
    const resolutionScale = Math.max(1, Math.min(4, zoom.value))
    const desiredPixelFactor = (window.devicePixelRatio || 1) * resolutionScale
    const dimensionLimit = 16384 / Math.max(logicalWidth, logicalHeight)
    const areaLimit = Math.sqrt(64_000_000 / Math.max(logicalWidth * logicalHeight, 1))
    const pixelFactor = Math.max(.5, Math.min(desiredPixelFactor, dimensionLimit, areaLimit))
    const canvas = pdfCanvas.value
    canvas.width = Math.ceil(logicalWidth * pixelFactor)
    canvas.height = Math.ceil(logicalHeight * pixelFactor)
    canvas.style.width = `${logicalWidth}px`
    canvas.style.height = `${logicalHeight}px`
    canvasLayout.value = {
      width: logicalWidth,
      height: logicalHeight,
      target: { x: 0, y: headerHeight, width: targetViewport.width, height: targetViewport.height },
      reference: referenceViewport ? { x: referenceX, y: headerHeight, width: referenceViewport.width, height: referenceViewport.height } : null
    }

    // PDF.js clears its target canvas at the start of every page render. Render
    // each document offscreen first, then composite both bitmaps into the one
    // visible canvas so the reference page cannot erase the ISO page.
    const [targetBitmap, referenceBitmap] = await Promise.all([
      renderPageOffscreen(targetPage, targetViewport, pixelFactor, bitmapCacheKey('target', targetPdf.value, pageNumber, targetViewport, pixelFactor)),
      referencePage && referenceViewport ? renderPageOffscreen(referencePage, referenceViewport, pixelFactor, bitmapCacheKey('reference', referencePdf.value, referencePageNumber, referenceViewport, pixelFactor)) : null
    ])
    if (generation !== renderGeneration) return
    const context = canvas.getContext('2d', { alpha: false })
    context.setTransform(1, 0, 0, 1, 0, 0)
    context.fillStyle = '#dfe5e8'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.drawImage(targetBitmap, 0, headerHeight * pixelFactor)
    if (referenceBitmap) context.drawImage(referenceBitmap, referenceX * pixelFactor, headerHeight * pixelFactor)
    context.setTransform(pixelFactor, 0, 0, pixelFactor, 0, 0)
    context.fillStyle = '#173e57'
    context.font = '600 13px Microsoft YaHei UI, sans-serif'
    context.fillText('待标识 ISO PDF', 10, 22)
    if (referenceViewport) {
      context.fillStyle = '#2d8b89'
      const referenceCaption = referencePdfs.value.length > 1
        ? `对照 PDF ${activeReferenceIndex.value + 1}/${referencePdfs.value.length} · P${referencePageNumber} · ${referencePdf.value?.name || ''}`
        : `对照 PDF · P${referencePageNumber}`
      context.fillText(referenceCaption, referenceX + 10, 22)
      context.strokeStyle = '#8798a2'
      context.lineWidth = 1
      context.beginPath(); context.moveTo(targetViewport.width + gap / 2, 0); context.lineTo(targetViewport.width + gap / 2, logicalHeight); context.stroke()
    }
    if (fitAfter) await nextTick().then(fitCanvas)
  } catch (cause) {
    if (cause?.name !== 'RenderingCancelledException') error.value = `Canvas 渲染失败：${cause.message || cause}`
  } finally {
    if (generation === renderGeneration) rendering.value = false
  }
}

function scheduleResolutionRender() {
  clearTimeout(resolutionTimer)
  resolutionTimer = setTimeout(() => { void renderUnifiedCanvas(false) }, 140)
}

function fitCanvas() {
  const viewport = canvasViewport.value
  const layout = canvasLayout.value
  if (!viewport || !layout.width || !layout.height) return
  const scale = Math.max(.1, Math.min(2, Math.min((viewport.clientWidth - 40) / layout.width, (viewport.clientHeight - 40) / layout.height)))
  zoom.value = scale
  pan.value = { x: (viewport.clientWidth - layout.width * scale) / 2, y: (viewport.clientHeight - layout.height * scale) / 2 }
  scheduleResolutionRender()
}

function zoomAt(clientX, clientY, nextZoom) {
  const viewport = canvasViewport.value
  if (!viewport) return
  const bounds = viewport.getBoundingClientRect()
  const localX = clientX - bounds.left
  const localY = clientY - bounds.top
  const bounded = Math.max(.1, Math.min(6, nextZoom))
  const worldX = (localX - pan.value.x) / zoom.value
  const worldY = (localY - pan.value.y) / zoom.value
  pan.value = { x: localX - worldX * bounded, y: localY - worldY * bounded }
  zoom.value = bounded
  scheduleResolutionRender()
}

function onCanvasWheel(event) { zoomAt(event.clientX, event.clientY, zoom.value * (event.deltaY < 0 ? 1.12 : .89)) }
function zoomBy(factor) {
  const bounds = canvasViewport.value?.getBoundingClientRect()
  if (bounds) zoomAt(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2, zoom.value * factor)
}
function startCanvasPan(event) {
  if (event.button !== 1) return
  event.preventDefault()
  canvasPanState.value = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, panX: pan.value.x, panY: pan.value.y }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}
function moveCanvasPan(event) {
  const state = canvasPanState.value
  if (state?.pointerId === event.pointerId) pan.value = { x: state.panX + event.clientX - state.startX, y: state.panY + event.clientY - state.startY }
}
function endCanvasPan(event) {
  if (canvasPanState.value?.pointerId !== event.pointerId) return
  event.currentTarget.releasePointerCapture?.(event.pointerId)
  canvasPanState.value = null
}

async function changePage(pageNumber) {
  const requested = result.value?.pages?.find(item => item.page === pageNumber)
  if (result.value?.jobId && requested && !requested.layoutObstacles) {
    try {
      const hydrated = await api.getJobPage(result.value.jobId, pageNumber)
      Object.assign(requested, hydrated.page || hydrated)
    } catch { /* existing light page record remains usable */ }
  }
  if (result.value) currentPage.value = pageNumber
  else previewPage.value = Math.max(1, Math.min(targetPageCount.value, pageNumber))
  selectedId.value = ''
  if (result.value && referenceMode.value === 'pdf') await syncReferenceForPage(currentPage.value, true)
  else await renderUnifiedCanvas(true)
}

async function ensureManualWorkspace() {
  if (result.value && pageData.value) return true
  if (!targetDocument.value) { error.value = '请先选择并加载待标识 ISO PDF。'; return false }
  const generatedPages = await Promise.all(
    Array.from({ length: targetDocument.value.numPages }, async (_, index) => {
      const pdfPage = await targetDocument.value.getPage(index + 1)
      const viewport = pdfPage.getViewport({ scale: 1 })
      return {
        page: index + 1,
        width: viewport.width,
        height: viewport.height,
        candidates: [],
        candidateCount: 0,
        weldCandidateCount: 0,
        designComponentCount: 0,
        layoutObstacles: { textRects: [], processSegments: [] }
      }
    })
  )
  result.value = {
    schema: 'weld-marker.topology.v1',
    status: 'manual',
    jobId: null,
    pages: generatedPages,
    analyzedRange: [1, generatedPages.length],
    manualWorkspace: true
  }
  currentPage.value = Math.max(1, Math.min(generatedPages.length, previewPage.value))
  projectResults.value[activeProjectIndex.value] = result.value
  await nextTick()
  return true
}

async function toggleManualAddMode(type = 'weld') {
  if (!await ensureManualWorkspace()) return
  const sameActiveType = manualAddMode.value && manualAddType.value === type
  manualAddType.value = type
  manualAddMode.value = !sameActiveType
  const title = ({ weld: '焊口', valve: '阀门', flange: '法兰', support: '支架' })[type]
  notice.value = manualAddMode.value ? `人工增加${title}已开启：请在待标识 PDF 上单击对应位置。` : '已退出人工增加模式。'
}

function mergeStagedManualCandidates(snapshot, stagedPages) {
  if (!snapshot?.pages?.length || !stagedPages?.length) return snapshot
  const stagedByPage = new Map(stagedPages.map(page => [page.page, (page.candidates || []).filter(item => item.origin === 'manual')]))
  snapshot.pages.forEach(page => {
    const existingIds = new Set((page.candidates || []).map(item => item.id))
    const additions = (stagedByPage.get(page.page) || []).filter(item => !existingIds.has(item.id)).map(cloneValue)
    if (!additions.length) return
    page.candidates = [...(page.candidates || []), ...additions]
    page.candidateCount = page.candidates.length
  })
  return snapshot
}

function onCanvasClick(event) {
  if (!manualAddMode.value || !canvasSurface.value || !pageData.value) return
  const bounds = canvasSurface.value.getBoundingClientRect()
  const layout = canvasLayout.value
  const worldX = (event.clientX - bounds.left) / bounds.width * layout.width
  const worldY = (event.clientY - bounds.top) / bounds.height * layout.height
  const localX = worldX - layout.target.x
  const localY = worldY - layout.target.y
  if (localX < 0 || localY < 0 || localX > layout.target.width || localY > layout.target.height) {
    error.value = '人工标识只能添加在待标识 ISO PDF 区域内。'
    return
  }
  const page = pageData.value
  const xNorm = localX / layout.target.width
  const yNorm = localY / layout.target.height
  const x = xNorm * page.width
  const y = yNorm * page.height
  const type = manualAddType.value
  const typeTitle = ({ weld: '焊口', valve: '阀门', flange: '法兰', support: '支架' })[type]
  beginHistory(`人工增加${typeTitle}`)
  const componentType = type === 'weld' ? null : type
  const sameTypeCount = page.candidates.filter(item => item.included !== false && (componentType ? item.componentType === componentType : !isDesignComponent(item))).length
  const prefix = ({ valve: 'V', flange: 'FL', support: 'SP' })[type]
  const item = {
    id: `p${page.page}-manual-${type}-${Date.now()}`,
    page: page.page,
    x, y, xNorm, yNorm,
    labelX: Math.min(page.width, x + 36), labelY: Math.max(0, y - 28),
    labelXNorm: Math.min(1, (x + 36) / page.width), labelYNorm: Math.max(0, (y - 28) / page.height),
    confidence: 1,
    tier: 'manual',
    glyphValidated: false,
    evidence: '人工添加',
    componentKind: componentType ? 'design-component' : 'manual-weld',
    componentType,
    autoNumberPrefix: prefix,
    defaultMarkerStyle: componentType ? { shape: 'rectangle', color: '#1769d2' } : { shape: 'rectangle', color: '#d4143c' },
    symbolShape: 'manual',
    included: true,
    origin: 'manual',
    number: componentType ? `${prefix}${sameTypeCount + 1}` : formattedWeldNumber(Math.max(1, Number(startNumber.value) || 1) + sameTypeCount),
    referenceLabel: '',
    referenceMatched: false
  }
  page.candidates.push(item)
  page.candidateCount = page.candidates.length
  selectedId.value = item.id
  manualAddMode.value = false
  reflowLabelPositions([page])
  commitHistory()
  logAudit(`${type}.manual_added`, { id: item.id, number: item.number })
  notice.value = `已人工增加${typeTitle} ${item.number}；可分别拖动定位点和标识框调整引线两端。`
}

function formattedWeldNumber(number) {
  return `${numberPrefix.value || ''}${number}${numberSuffix.value || ''}`
}

function isDesignComponent(item) {
  return item?.componentKind === 'design-component' && ['valve', 'flange', 'support'].includes(item?.componentType)
}

function sameNumberingGroup(left, right) {
  if (isDesignComponent(left) || isDesignComponent(right)) {
    return isDesignComponent(left) && isDesignComponent(right) && left.componentType === right.componentType
  }
  return true
}

function candidateMarkerStyle(item) {
  const liveStyle = isDesignComponent(item)
    ? componentMarkerStyles.value[item.componentType] || componentMarkerStyles.value.support
    : markerStyle.value
  return {
    ...(item?.defaultMarkerStyle || {}),
    ...(item?.markerStyle || {}),
    ...liveStyle
  }
}

function componentNumber(item, number) {
  const prefix = item?.autoNumberPrefix || ({ valve: 'V', flange: 'FL', support: 'SP' }[item?.componentType] || 'C')
  return `${prefix}${number}`
}

function numberResult(documentResult, startingNumber) {
  ;(documentResult?.pages || []).slice().sort((a, b) => a.page - b.page).forEach(page => {
    let weldNumber = startingNumber
    const componentNumbers = { valve: 1, flange: 1, support: 1 }
    const reservedComponentNumbers = { valve: new Set(), flange: new Set(), support: new Set() }
    page.candidates.filter(isDesignComponent).forEach(item => {
      if (!useReferenceNumber.value || !item.referenceLabel) return
      const match = String(item.referenceLabel).match(/(\d+)$/)
      if (match) reservedComponentNumbers[item.componentType].add(Number(match[1]))
    })
    page.candidates.filter(item => item.included !== false).sort((a, b) => a.y - b.y || a.x - b.x).forEach(item => {
      if (isDesignComponent(item)) {
        if (useReferenceNumber.value && item.referenceLabel) {
          item.number = item.referenceLabel
        } else {
          while (reservedComponentNumbers[item.componentType].has(componentNumbers[item.componentType])) {
            componentNumbers[item.componentType] += 1
          }
          item.number = componentNumber(item, componentNumbers[item.componentType])
          componentNumbers[item.componentType] += 1
        }
      } else {
        item.number = useReferenceNumber.value && item.referenceLabel ? item.referenceLabel : formattedWeldNumber(weldNumber)
        weldNumber += 1
      }
    })
  })
}

function resultHasMissingNumbers(documentResult) {
  return (documentResult?.pages || []).some(page => (
    (page.candidates || []).some(item => !String(item.number || '').trim())
  ))
}

const wait = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds))

async function waitForJob(initialJob, onUpdate) {
  let snapshot = initialJob
  await onUpdate(snapshot)
  while (snapshot.status === 'processing' || snapshot.status === 'cancelling') {
    await wait(550)
    snapshot = await api.getJob(initialJob.jobId)
    await onUpdate(snapshot)
  }
  if (snapshot.status === 'failed') throw new Error(snapshot.error || '图元研究任务失败')
  if (snapshot.status === 'cancelled') {
    const cause = new Error('解析任务已取消')
    cause.name = 'JobCancelled'
    throw cause
  }
  return snapshot
}

async function cancelActiveAnalysis() {
  if (!activeAnalysisJobId.value || cancellingAnalysis.value) return
  cancellingAnalysis.value = true
  try {
    await api.cancelJob(activeAnalysisJobId.value)
    analysisProgress.value = { ...analysisProgress.value, message: '正在安全取消解析任务' }
    logAudit('analysis.cancel_requested', { jobId: activeAnalysisJobId.value })
  } catch (cause) {
    error.value = cause.message || String(cause)
    cancellingAnalysis.value = false
  }
}

async function analyze() {
  if (!targetPdf.value) { error.value = '请先选择待标识 ISO PDF 或工程文件夹。'; return }
  loading.value = true
  error.value = ''
  notice.value = ''
  referenceResearchReady.value = false
  analysisProgress.value = {
    fileIndex: 0,
    fileCount: projectPdfs.value.length || 1,
    fileName: targetPdf.value.name,
    completedPages: 0,
    totalPages: 1,
    completedReferenceFiles: 0,
    totalReferenceFiles: referencePdfs.value.length,
    progressCompletedUnits: 0,
    progressTotalUnits: Math.max(1, referencePdfs.value.length + 1),
    message: '正在准备图元研究'
  }
  if (referenceProjectMode.value === 'folder') {
    referenceLoadToken.value += 1
    referenceDocument.value?.destroy?.()
    referenceDocument.value = null
    referencePdf.value = null
    activeReferenceIndex.value = -1
  }
  try {
    const files = projectPdfs.value.length ? projectPdfs.value : [targetPdf.value]
    const stagedManualPages = files.map((_, index) => {
      const workspace = index === activeProjectIndex.value ? result.value : projectResults.value[index]
      return cloneValue((workspace?.pages || []).map(page => ({
        page: page.page,
        candidates: (page.candidates || []).filter(item => item.origin === 'manual')
      })))
    })
    const encodedReferences = referenceMode.value === 'pdf'
      ? await Promise.all(referencePdfs.value.map(file => encodeFile(file)))
      : []
    const encodedPcfs = referenceMode.value === 'pdf'
      ? await Promise.all(pcfFiles.value.map(file => encodeFile(file)))
      : []
    const analyzed = new Array(files.length).fill(null)
    const batchId = crypto.randomUUID?.() || `batch-${Date.now()}`
    const pageStartNumber = Math.max(1, Number(startNumber.value) || 1)
    for (let index = 0; index < files.length; index += 1) {
      analysisProgress.value = { fileIndex: index, fileCount: files.length, fileName: files[index].name, completedPages: 0, totalPages: 1, completedReferenceFiles: 0, totalReferenceFiles: encodedReferences.length, progressCompletedUnits: 0, progressTotalUnits: Math.max(1, encodedReferences.length + 1), message: '正在上传并准备图元研究' }
      const jobPayload = {
        targetPdf: await encodeFile(files[index]),
        originalTargetName: files[index].name,
        batchId,
        batchIndex: index,
        startPage: projectMode.value === 'single' ? Number(startPage.value) || 1 : 1,
        endPage: projectMode.value === 'single' && endPage.value ? Number(endPage.value) : null,
        referencePdfs: encodedReferences, pcfFiles: encodedPcfs,
        pcfFolder: referenceMode.value === 'pdf' ? selectedPcfFolder.value || null : null,
        symbolConfig: { ...weldSymbolConfig.value, detectionMode: detectionMode.value }
      }
      let createdJob = await api.createJob(jobPayload)
      if (createdJob.requiresReanalysisConfirmation) {
        const choice = await confirmDuplicateJob(createdJob, files[index].name)
        if (choice === 'cancel') {
          const cause = new Error('已取消本次解析操作')
          cause.name = 'AnalysisDialogCancelled'
          throw cause
        }
        if (choice === 'reanalyze') createdJob = await api.createJob({ ...jobPayload, forceReanalyze: true })
      }
      activeAnalysisJobId.value = createdJob.jobId
      const preserveExistingResult = createdJob.reusedExisting && createdJob.status === 'complete'
      let resultNumberingRepaired = false
      analysisProgress.value = {
        ...analysisProgress.value,
        totalPages: createdJob.totalPages || 1,
        completedReferenceFiles: createdJob.completedReferenceFiles || 0,
        totalReferenceFiles: createdJob.totalReferenceFiles ?? encodedReferences.length,
        progressCompletedUnits: createdJob.progressCompletedUnits || 0,
        progressTotalUnits: createdJob.progressTotalUnits || Math.max(1, encodedReferences.length + (createdJob.totalPages || 1)),
        message: createdJob.reusedExisting && createdJob.status === 'processing'
          ? '已接入后端正在解析的相同任务'
          : createdJob.progressMessage || '任务已创建'
      }
      let publishedPageCount = 0
      analyzed[index] = await waitForJob(createdJob, async snapshot => {
        mergeStagedManualCandidates(snapshot, stagedManualPages[index])
        const missingNumbers = resultHasMissingNumbers(snapshot)
        if (!preserveExistingResult || missingNumbers) {
          numberResult(snapshot, pageStartNumber)
          if (detectionMode.value === 'placement') reflowLabelPositions(snapshot.pages || [])
          resultNumberingRepaired ||= missingNumbers
        }
        analyzed[index] = snapshot
        projectResults.value = analyzed.slice()
        const nextPageCount = snapshot.pages?.length || 0
        analysisProgress.value = {
          fileIndex: index,
          fileCount: files.length,
          fileName: files[index].name,
          completedPages: snapshot.completedPages ?? nextPageCount,
          totalPages: snapshot.totalPages || createdJob.totalPages || Math.max(1, nextPageCount),
          completedReferenceFiles: snapshot.completedReferenceFiles || 0,
          totalReferenceFiles: snapshot.totalReferenceFiles ?? encodedReferences.length,
          progressCompletedUnits: snapshot.progressCompletedUnits ?? nextPageCount,
          progressTotalUnits: snapshot.progressTotalUnits || Math.max(1, encodedReferences.length + (snapshot.totalPages || createdJob.totalPages || Math.max(1, nextPageCount))),
          message: snapshot.progressMessage || '正在进行图元研究'
        }
        if (activeProjectIndex.value === index) {
          result.value = snapshot
          if (nextPageCount > 0) {
            referenceResearchReady.value = true
            if (!publishedPageCount) currentPage.value = snapshot.pages[0].page
            await nextTick()
            if (!publishedPageCount) {
              if (referenceMode.value === 'pdf') await syncReferenceForPage(currentPage.value, true)
              else await renderUnifiedCanvas(true)
            }
          }
        }
        publishedPageCount = nextPageCount
      })
      if (analyzed[index]?.status === 'complete' && (!preserveExistingResult || resultNumberingRepaired)) {
        const saved = await api.saveJob(createdJob.jobId, analyzed[index].pages || [], currentMarkerAppearancePayload(), analyzed[index].revision ?? 0)
        analyzed[index].revision = saved.revision
      }
      activeAnalysisJobId.value = ''
      projectResults.value = analyzed.slice()
    }
    projectResults.value = analyzed
    result.value = analyzed[activeProjectIndex.value]
    referenceResearchReady.value = true
    currentPage.value = result.value?.analyzedRange?.[0] || 1
    selectedId.value = ''
    swapSourceId.value = ''
    const total = analyzed.reduce((sum, item) => sum + (item?.pages || []).reduce((count, page) => count + page.candidates.length, 0), 0)
    const completionMessage = `已完成 ${files.length} 份 PDF 的图元分析，共识别 ${total} 个焊口及管件标识对象。`
    analysisProgress.value = { ...analysisProgress.value, fileIndex: files.length - 1, completedPages: analysisProgress.value.totalPages, completedReferenceFiles: analysisProgress.value.totalReferenceFiles, progressCompletedUnits: analysisProgress.value.progressTotalUnits, message: completionMessage }
    await nextTick()
    if (referenceMode.value === 'pdf') await syncReferenceForPage(currentPage.value, true)
    else await renderUnifiedCanvas(true)
    if (detectionMode.value === 'placement' && !result.value?.reusedExisting) reflowAllLabelPositions()
    undoStack.value = []
    redoStack.value = []
    scheduleDraftSave()
    logAudit('analysis.completed', { files: files.length, candidates: total })
  } catch (cause) {
    if (cause?.name === 'JobCancelled' || cause?.name === 'AnalysisDialogCancelled') notice.value = cause.message
    else error.value = cause.message || String(cause)
  } finally {
    activeAnalysisJobId.value = ''
    cancellingAnalysis.value = false
    loading.value = false
  }
}

function numberWelds() {
  beginHistory('全部重新编号')
  numberResult(result.value, Math.max(1, Number(startNumber.value) || 1))
  commitHistory()
  notice.value = `已重新生成 ${activeCandidates.value.length} 个焊口及管件编号。`
}

function requestRenumberFromSelected() {
  if (!selectedCandidate.value) { error.value = '请先选中一个标识对象。'; return }
  if (selectedCandidate.value.included === false) { error.value = '已排除的对象不能作为重新编号起点，请先恢复该对象。'; return }
  pendingRenumberId.value = selectedCandidate.value.id
  renumberConfirmDialog.value = true
}

function renumberFromSelected() {
  const candidate = pendingRenumberCandidate.value
  if (!candidate) { renumberConfirmDialog.value = false; error.value = '待重编的标识对象已不存在。'; return }
  const current = pages.value.find(page => page.page === candidate.page)
  const ordered = (current?.candidates || []).filter(item => item.included !== false && sameNumberingGroup(item, candidate)).sort((a, b) => a.y - b.y || a.x - b.x)
  const startIndex = ordered.findIndex(item => item.id === candidate.id)
  if (startIndex < 0) return
  beginHistory('从选中对象重新编号')
  const rotated = ordered.slice(startIndex).concat(ordered.slice(0, startIndex))
  let number = isDesignComponent(candidate) ? 1 : Math.max(1, Number(startNumber.value) || 1)
  rotated.forEach(item => {
    item.number = isDesignComponent(item)
      ? componentNumber(item, number)
      : (useReferenceNumber.value && item.referenceLabel ? item.referenceLabel : formattedWeldNumber(number))
    number += 1
  })
  commitHistory()
  selectedId.value = candidate.id
  renumberConfirmDialog.value = false
  pendingRenumberId.value = ''
  notice.value = `已在第 ${candidate.page} 页以 ${candidate.number || '?'} 为起点，重新编号同类对象 ${rotated.length} 个。`
}

function cancelRenumberConfirmation() { renumberConfirmDialog.value = false; pendingRenumberId.value = '' }

function pageMatchSummary(page) {
  const included = (page?.candidates || []).filter(item => item.included !== false && !isDesignComponent(item))
  const unmatched = included.filter(item => !item.referenceMatched || !String(item.referenceLabel || '').trim()).length
  const unresolved = Math.max(0, Number(page?.reference?.unresolvedCalloutGap) || 0)
  return {
    unmatched,
    unresolved,
    complete: included.length > 0 && unmatched === 0 && unresolved === 0
  }
}

function pageButtonColor(page) {
  const summary = pageMatchSummary(page)
  if (summary.unmatched || summary.unresolved) return 'warning'
  if (summary.complete) return 'success'
  return currentPage.value === page.page ? 'secondary' : undefined
}

function swapSelectedWeldNumbers() {
  const selected = selectedCandidate.value
  if (!selected || selected.included === false) {
    error.value = '请先选中一个参与编号的焊口，再按 X。'
    return
  }
  if (!swapSourceId.value) {
    swapSourceId.value = selected.id
    notice.value = `已暂存焊口 ${selected.number || '?'}；请选择另一个焊口并再次按 X。`
    return
  }
  if (swapSourceId.value === selected.id) {
    swapSourceId.value = ''
    notice.value = '已取消交换编号。'
    return
  }
  const source = candidates.value.find(item => item.id === swapSourceId.value && item.included !== false)
  if (!source) {
    swapSourceId.value = selected.id
    notice.value = `原交换对象已失效，现已暂存焊口 ${selected.number || '?'}。`
    return
  }
  const sourceNumber = source.number
  beginHistory('交换焊口编号')
  source.number = selected.number
  selected.number = sourceNumber
  commitHistory()
  swapSourceId.value = ''
  notice.value = `已交换焊口 ${source.number || '?'} 与 ${selected.number || '?'} 的编号。`
}

function selectCandidate(item) { selectedId.value = item.id }
function markerClass(item) {
  const style = candidateMarkerStyle(item)
  return { selected: selectedId.value === item.id, excluded: item.included === false,
    matched: item.referenceMatched, swapSource: swapSourceId.value === item.id,
    dragging: labelDragState.value?.id === item.id, [`shape-${style.shape}`]: true }
}
function markerUnitScale(item) {
  const target = canvasLayout.value.target
  const page = pages.value.find(value => value.page === item.page) || pageData.value
  return target?.width && page?.width ? target.width / page.width : 1
}
function markerDimensions(item) {
  const style = candidateMarkerStyle(item)
  const scale = markerUnitScale(item)
  const size = (Number(style.frameSize) || 30) * scale
  const textLength = String(item.number || '?').length
  const configuredFontSize = (Number(style.fontSize) || 18) * scale
  const width = style.shape === 'rectangle'
    ? Math.max(size * 1.35, 10 * scale + textLength * configuredFontSize * .68)
    : size
  const fontSize = Math.min(configuredFontSize, Math.max(4 * scale, (width - 6 * scale) / Math.max(textLength * .58, 1)))
  return { width, height: size, fontSize, scale }
}

function rectanglesOverlap(first, second, padding = 0) {
  return first.left < second.right + padding && first.right > second.left - padding && first.top < second.bottom + padding && first.bottom > second.top - padding
}

function rectangleIntersectionArea(first, second) {
  return Math.max(0, Math.min(first.right, second.right) - Math.max(first.left, second.left))
    * Math.max(0, Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top))
}

function lineSegmentsIntersect(first, second) {
  const cross = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
  const a = cross(first.start, first.end, second.start)
  const b = cross(first.start, first.end, second.end)
  const c = cross(second.start, second.end, first.start)
  const d = cross(second.start, second.end, first.end)
  return a * b < 0 && c * d < 0
}

function segmentIntersectsRectangle(segment, rectangle) {
  if ([segment.start, segment.end].some(point => point.x >= rectangle.left && point.x <= rectangle.right && point.y >= rectangle.top && point.y <= rectangle.bottom)) return true
  const topLeft = { x: rectangle.left, y: rectangle.top }
  const topRight = { x: rectangle.right, y: rectangle.top }
  const bottomRight = { x: rectangle.right, y: rectangle.bottom }
  const bottomLeft = { x: rectangle.left, y: rectangle.bottom }
  return [[topLeft, topRight], [topRight, bottomRight], [bottomRight, bottomLeft], [bottomLeft, topLeft]]
    .some(([start, end]) => lineSegmentsIntersect(segment, { start, end }))
}

function pointToSegmentDistance(point, segment) {
  const dx = segment.end.x - segment.start.x
  const dy = segment.end.y - segment.start.y
  const lengthSquared = dx * dx + dy * dy
  if (!lengthSquared) return Math.hypot(point.x - segment.start.x, point.y - segment.start.y)
  const t = Math.max(0, Math.min(1, ((point.x - segment.start.x) * dx + (point.y - segment.start.y) * dy) / lengthSquared))
  return Math.hypot(point.x - (segment.start.x + t * dx), point.y - (segment.start.y + t * dy))
}

function markerDimensionsForPage(item) {
  const style = candidateMarkerStyle(item)
  const size = Number(style.frameSize) || 30
  const configuredFontSize = Number(style.fontSize) || 18
  const textLength = String(item.number || '?').length
  const width = style.shape === 'rectangle'
    ? Math.max(size * 1.35, 10 + textLength * configuredFontSize * .68)
    : size
  return { width, height: size }
}

function reflowLabelPositions(pageList) {
  const totals = { moved: 0, placed: 0 }
  ;(pageList || []).forEach(page => {
    const rawItems = (page.candidates || []).filter(item => item.included !== false).slice()
    const anchors = rawItems.map(item => ({ x: Number(item.x), y: Number(item.y), id: item.id }))
    const textRectangles = (page.layoutObstacles?.textRects || []).map(box => ({ left: Number(box[0]), top: Number(box[1]), right: Number(box[2]), bottom: Number(box[3]) }))
    const processSegments = (page.layoutObstacles?.processSegments || []).map(segment => ({
      start: { x: Number(segment.start?.[0]), y: Number(segment.start?.[1]) },
      end: { x: Number(segment.end?.[0]), y: Number(segment.end?.[1]) }
    }))
    const congestion = item => {
      const anchor = { x: Number(item.x), y: Number(item.y) }
      const nearbyAnchors = anchors.filter(point => point.id !== item.id && Math.hypot(point.x - anchor.x, point.y - anchor.y) < 110).length
      const nearbyText = textRectangles.filter(rectangle => anchor.x > rectangle.left - 65 && anchor.x < rectangle.right + 65 && anchor.y > rectangle.top - 65 && anchor.y < rectangle.bottom + 65).length
      const nearbyPipes = processSegments.filter(segment => pointToSegmentDistance(anchor, segment) < 45).length
      return nearbyAnchors * 8 + nearbyText * 3 + nearbyPipes
    }
    const congestionScores = new Map(rawItems.map(item => [item.id, congestion(item)]))
    // Greedy placement is most stable when the hardest, most crowded anchors
    // claim clean space first instead of always processing top-to-bottom.
    const items = rawItems.sort((a, b) => congestionScores.get(b.id) - congestionScores.get(a.id) || a.y - b.y || a.x - b.x)
    const placed = []
    const placedLeaders = []
    items.forEach(item => {
      const original = { x: Number(item.labelX ?? item.x), y: Number(item.labelY ?? item.y) }
      const dimensions = markerDimensionsForPage(item)
      const anchor = { x: Number(item.x), y: Number(item.y) }
      const halfWidth = dimensions.width / 2
      const halfHeight = dimensions.height / 2
      const baseDistance = Math.hypot(halfWidth, halfHeight) + 12
      const nearestPipe = processSegments.reduce((best, segment) => {
        const distance = pointToSegmentDistance(anchor, segment)
        return !best || distance < best.distance ? { segment, distance } : best
      }, null)
      const pipeAngle = nearestPipe && nearestPipe.distance < 18
        ? Math.atan2(nearestPipe.segment.end.y - nearestPipe.segment.start.y, nearestPipe.segment.end.x - nearestPipe.segment.start.x)
        : null
      const preferredAngles = pipeAngle === null ? [] : [pipeAngle - Math.PI / 2, pipeAngle + Math.PI / 2]
      const currentAngle = Math.atan2(original.y - anchor.y, original.x - anchor.x)
      const candidateAngles = [currentAngle, ...preferredAngles, ...Array.from({ length: 20 }, (_, index) => -Math.PI + index * Math.PI / 10)]
        .filter((angle, index, values) => values.findIndex(other => Math.abs(Math.atan2(Math.sin(other - angle), Math.cos(other - angle))) < .05) === index)
      let best = null
      for (let ring = 0; ring < 12; ring += 1) {
        const distance = baseDistance + ring * Math.max(12, Math.min(dimensions.width, dimensions.height) * .42)
        candidateAngles.forEach((angle, directionIndex) => {
          const unclampedX = anchor.x + Math.cos(angle) * distance
          const unclampedY = anchor.y + Math.sin(angle) * distance
          const x = Math.max(halfWidth + 5, Math.min(page.width - halfWidth - 5, unclampedX))
          const y = Math.max(halfHeight + 5, Math.min(page.height - halfHeight - 5, unclampedY))
          const rectangle = { left: x - halfWidth, right: x + halfWidth, top: y - halfHeight, bottom: y + halfHeight }
          const labelCollisions = placed.reduce((count, other) => count + (rectanglesOverlap(rectangle, other, 5) ? 1 : 0), 0)
          const anchorCollisions = anchors.reduce((count, point) => count + (point.id !== item.id && point.x > rectangle.left - 8 && point.x < rectangle.right + 8 && point.y > rectangle.top - 8 && point.y < rectangle.bottom + 8 ? 1 : 0), 0)
          const rectangleArea = Math.max(1, dimensions.width * dimensions.height)
          const textOverlapRatio = textRectangles.reduce((sum, textRectangle) => sum + rectangleIntersectionArea(rectangle, textRectangle) / rectangleArea, 0)
          const textProximity = textRectangles.reduce((count, textRectangle) => count + (rectanglesOverlap(rectangle, textRectangle, 4) ? 1 : 0), 0)
          const pipeCollisions = processSegments.reduce((count, segment) => count + (segmentIntersectsRectangle(segment, rectangle) ? 1 : 0), 0)
          const leader = { start: anchor, end: { x, y } }
          const leaderCrossings = placedLeaders.reduce((count, other) => count + (lineSegmentsIntersect(leader, other) ? 1 : 0), 0)
          const leaderLabelCrossings = placed.reduce((count, other) => count + (segmentIntersectsRectangle(leader, other) ? 1 : 0), 0)
          const leaderTextCrossings = textRectangles.reduce((count, textRectangle) => count + (segmentIntersectsRectangle(leader, textRectangle) ? 1 : 0), 0)
          const leaderAnchorProximity = anchors.reduce((count, point) => count + (point.id !== item.id && pointToSegmentDistance(point, leader) < 7 ? 1 : 0), 0)
          const clampPenalty = Math.hypot(x - unclampedX, y - unclampedY)
          const score = labelCollisions * 10_000_000 + anchorCollisions * 8_000_000
            + textOverlapRatio * 6_000_000 + textProximity * 260_000 + pipeCollisions * 700_000
            + leaderLabelCrossings * 4_000_000 + leaderCrossings * 3_000_000 + leaderTextCrossings * 8_000
            + leaderAnchorProximity * 1_600_000 + ring * 320 + clampPenalty * 35 + directionIndex
          if (!best || score < best.score) best = { x, y, rectangle, leader, score, hard: labelCollisions + anchorCollisions + leaderLabelCrossings + leaderCrossings + leaderAnchorProximity, textOverlapRatio, pipeCollisions, textProximity }
        })
        if (best && best.hard === 0 && best.textOverlapRatio === 0 && best.pipeCollisions === 0 && best.textProximity === 0) break
      }
      if (!best) return
      item.labelX = best.x
      item.labelY = best.y
      item.labelXNorm = best.x / page.width
      item.labelYNorm = best.y / page.height
      placed.push(best.rectangle)
      placedLeaders.push(best.leader)
      totals.placed += 1
      if (Math.hypot(best.x - original.x, best.y - original.y) > 2) totals.moved += 1
    })
  })
  return totals
}

function reflowAllLabelPositions(announce = false) {
  if (!result.value) return
  beginHistory('重新优化标识位置')
  const totals = reflowLabelPositions(pages.value)
  commitHistory()
  if (announce) notice.value = totals.moved
    ? `标识避让优化完成：重新放置 ${totals.placed} 个标识，其中移动 ${totals.moved} 个。`
    : `已检查 ${totals.placed} 个标识，当前布局已经是本轮避让规则下的最优结果。`
}

function scheduleMarkerReflow() {
  clearTimeout(markerReflowTimer)
  markerReflowTimer = setTimeout(() => {
    reflowAllLabelPositions()
    if (result.value) notice.value = '已按新的标识尺寸重新排布，焊口锚点和引线起点保持不变。'
  }, 180)
}
function labelPosition(item) {
  const style = candidateMarkerStyle(item)
  const target = canvasLayout.value.target
  const dimensions = markerDimensions(item)
  const diamondSide = dimensions.width / Math.SQRT2
  const elementWidth = style.shape === 'diamond' ? diamondSide : dimensions.width
  const elementHeight = style.shape === 'diamond' ? diamondSide : dimensions.height
  return {
    left: `${target.x + (item.labelXNorm ?? item.xNorm) * target.width}px`,
    top: `${target.y + (item.labelYNorm ?? item.yNorm) * target.height}px`,
    width: `${elementWidth}px`, height: `${elementHeight}px`, borderColor: hexToRgba(style.color, .86),
    color: hexToRgba(style.color, .92), fontSize: `${dimensions.fontSize}px`,
    borderWidth: `${Math.max(.5, Number(style.lineWidth) || 1.35) * dimensions.scale}px`,
    backgroundColor: `rgba(255,255,255,${style.fillOpacity})`
  }
}
function hexToRgba(value, alpha) {
  const match = /^#([0-9a-f]{6})$/i.exec(String(value || ''))
  if (!match) return `rgba(212,20,60,${alpha})`
  const number = Number.parseInt(match[1], 16)
  return `rgba(${number >> 16},${number >> 8 & 255},${number & 255},${alpha})`
}
function anchorPoint(item) {
  const target = canvasLayout.value.target
  return { x: target.x + item.xNorm * target.width, y: target.y + item.yNorm * target.height }
}
function leaderEnd(item) {
  const style = candidateMarkerStyle(item)
  const target = canvasLayout.value.target
  const start = anchorPoint(item)
  const center = { x: target.x + (item.labelXNorm ?? item.xNorm) * target.width, y: target.y + (item.labelYNorm ?? item.yNorm) * target.height }
  const dx = center.x - start.x
  const dy = center.y - start.y
  const distance = Math.hypot(dx, dy)
  if (distance < .01) return center
  const ux = dx / distance
  const uy = dy / distance
  const dimensions = markerDimensions(item)
  let boundary = dimensions.height / 2
  if (style.shape === 'diamond') boundary = (dimensions.height / 2) / Math.max(Math.abs(ux) + Math.abs(uy), .001)
  if (style.shape === 'rectangle') boundary = Math.min(Math.abs(ux) > .001 ? dimensions.width / 2 / Math.abs(ux) : Infinity, Math.abs(uy) > .001 ? dimensions.height / 2 / Math.abs(uy) : Infinity)
  return { x: center.x - ux * boundary, y: center.y - uy * boundary }
}
function leaderStyle(item) {
  const style = candidateMarkerStyle(item)
  const scale = markerDimensions(item).scale
  return {
    stroke: style.color,
    strokeWidth: Math.max(.5, (Number(style.lineWidth) || 1.35) * .78) * scale,
    strokeOpacity: .68
  }
}
function beginInlineEdit(event, item) {
  if (event.button !== 0 || item.included === false) return
  event.stopPropagation()
  selectCandidate(item)
  beginHistory('修改标识编号')
  editingId.value = item.id
  editingValue.value = String(item.number || '')
  nextTick(() => document.querySelector('.inline-weld-input')?.select())
}
function commitInlineEdit(item) {
  if (editingId.value !== item.id) return
  item.number = editingValue.value.trim()
  editingId.value = ''
  commitHistory()
  logAudit('weld.number_changed', { id: item.id, number: item.number })
}
function cancelInlineEdit() { editingId.value = ''; editingValue.value = ''; pendingHistory.value = null }
function onApplicationPointerDown(event) {
  if (!editingId.value || event.target?.closest?.('.inline-weld-input')) return
  const item = candidates.value.find(candidate => candidate.id === editingId.value)
  if (item) commitInlineEdit(item)
  else cancelInlineEdit()
}
function startLabelDrag(event, item) {
  if (event.button !== 0 || editingId.value === item.id || item.included === false) return
  selectCandidate(item)
  beginHistory('移动焊口标识')
  labelDragState.value = { id: item.id, pointerId: event.pointerId }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}
function moveLabel(event, item) {
  if (labelDragState.value?.id !== item.id || !canvasSurface.value || !pageData.value) return
  const bounds = canvasSurface.value.getBoundingClientRect()
  const layout = canvasLayout.value
  const worldX = (event.clientX - bounds.left) / bounds.width * layout.width
  const worldY = (event.clientY - bounds.top) / bounds.height * layout.height
  const xNorm = Math.max(.015, Math.min(.985, (worldX - layout.target.x) / layout.target.width))
  const yNorm = Math.max(.015, Math.min(.985, (worldY - layout.target.y) / layout.target.height))
  item.labelXNorm = xNorm; item.labelYNorm = yNorm
  item.labelX = xNorm * pageData.value.width; item.labelY = yNorm * pageData.value.height
}
function endLabelDrag(event, item) {
  if (labelDragState.value?.id !== item.id) return
  event.currentTarget.releasePointerCapture?.(event.pointerId)
  labelDragState.value = null
  commitHistory()
  logAudit('weld.label_moved', { id: item.id })
}
function startAnchorDrag(event, item) {
  if (event.button !== 0 || item.origin !== 'manual') return
  event.preventDefault()
  event.stopPropagation()
  selectCandidate(item)
  beginHistory('移动人工焊口引线')
  anchorDragState.value = { id: item.id, pointerId: event.pointerId }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}
function moveAnchor(event, item) {
  if (anchorDragState.value?.id !== item.id || !canvasSurface.value || !pageData.value) return
  event.stopPropagation()
  const bounds = canvasSurface.value.getBoundingClientRect()
  const layout = canvasLayout.value
  const worldX = (event.clientX - bounds.left) / bounds.width * layout.width
  const worldY = (event.clientY - bounds.top) / bounds.height * layout.height
  const xNorm = Math.max(0, Math.min(1, (worldX - layout.target.x) / layout.target.width))
  const yNorm = Math.max(0, Math.min(1, (worldY - layout.target.y) / layout.target.height))
  item.xNorm = xNorm; item.yNorm = yNorm
  item.x = xNorm * pageData.value.width; item.y = yNorm * pageData.value.height
}
function endAnchorDrag(event, item) {
  if (anchorDragState.value?.id !== item.id) return
  event.stopPropagation()
  event.currentTarget.releasePointerCapture?.(event.pointerId)
  anchorDragState.value = null
  commitHistory()
  logAudit('weld.anchor_moved', { id: item.id })
}
function toggleCandidate(item) {
  const restoring = item.included === false
  beginHistory(restoring ? '恢复焊口' : '排除误识别焊口')
  if (restoring) {
    item.included = true
    if (item.numberBeforeExclusion !== undefined) item.number = item.numberBeforeExclusion
  } else {
    item.numberBeforeExclusion = item.number
    item.included = false
  }
  commitHistory()
  logAudit('weld.inclusion_changed', { id: item.id, included: item.included })
}

function deleteSelectedWeld() {
  const item = selectedCandidate.value
  if (!item) { error.value = '请先选中要删除的焊口。'; return }
  const page = pages.value.find(value => value.page === item.page)
  const index = page?.candidates?.findIndex(value => value.id === item.id) ?? -1
  if (!page || index < 0) { error.value = '选中的焊口已不存在。'; return }
  beginHistory('删除焊口')
  page.candidates.splice(index, 1)
  page.candidateCount = page.candidates.length
  selectedId.value = ''
  swapSourceId.value = swapSourceId.value === item.id ? '' : swapSourceId.value
  commitHistory()
  notice.value = `已删除焊口 ${item.number || '?'}，可使用 Ctrl+Z 撤销。`
  logAudit('weld.deleted', { id: item.id, number: item.number, origin: item.origin || 'recognized' })
}

async function save() {
  if (!result.value) return
  try {
    const saved = await api.saveJob(result.value.jobId, pages.value, currentMarkerAppearancePayload(), result.value.revision ?? 0)
    result.value.revision = saved.revision
    result.value.updatedAt = saved.updatedAt
    scheduleDraftSave()
    logAudit('job.saved')
    notice.value = '当前 PDF 校对结果已保存。'
  } catch (cause) { error.value = cause.message }
}
async function exportPdf() {
  if (!result.value) return
  loading.value = true
  try {
    const styled = activeCandidates.value.map(item => ({ ...item, markerStyle: { ...candidateMarkerStyle(item) } }))
    const exported = await api.exportPdf(result.value.jobId, styled)
    window.location.href = exported.downloadUrl
    logAudit('pdf.exported')
    notice.value = '标识 PDF 已生成。'
  } catch (cause) { error.value = cause.message } finally { loading.value = false }
}
function exportCsv() {
  const rows = [['页码', '对象类型', '编号', 'X', 'Y', '证据', '置信度', '参考匹配']]
  activeCandidates.value.forEach(item => rows.push([item.page, isDesignComponent(item) ? item.componentType : 'weld', item.number, item.x.toFixed(2), item.y.toFixed(2), item.evidence, Math.round((item.confidence || 0) * 100) + '%', item.referenceLabel || '']))
  const csv = encodeCsv(rows)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  link.download = '图纸拓扑标识结果.csv'
  link.click()
  URL.revokeObjectURL(link.href)
  logAudit('csv.exported', { candidates: activeCandidates.value.length })
}

</script>

<template>
  <v-app class="weld-app" @pointerdown.capture="onApplicationPointerDown">
    <v-app-bar color="#173e57" elevation="3" :height="appHeaderHeight">
      <v-app-bar-title class="ml-5"><div class="app-title"><span class="app-title__icon" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M5 16h7m8 0h7M12 9v14m8-14v14" /><circle cx="16" cy="16" r="5" /><path d="m13.2 13.2 5.6 5.6m0-5.6-5.6 5.6" /></svg></span><span class="app-title__copy"><span>图纸拓扑标识系统</span><small>焊口 · 阀门 · 法兰 · 支架拓扑识别</small></span></div></v-app-bar-title>
      <div class="header-icon-area mr-4">
        <button type="button" class="header-status-icon" :class="`status-${health}`" :aria-label="health === 'ready' ? '图元引擎就绪' : health === 'offline' ? '后端未连接' : '图元引擎正在连接'">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="2.2" /><circle cx="19" cy="6" r="2.2" /><circle cx="19" cy="18" r="2.2" /><path d="m7 11 9.8-4M7 13l9.8 4" /></svg><span class="status-dot" />
          <v-tooltip activator="parent" location="bottom">{{ health === 'ready' ? '图元引擎就绪' : health === 'offline' ? '后端未连接' : '图元引擎正在连接' }}</v-tooltip>
        </button>
        <button type="button" class="header-status-icon" :class="`status-${mineruHealth}`" :aria-label="mineruHealth === 'ready' ? 'MinerU 就绪' : mineruHealth === 'offline' ? 'MinerU 未就绪' : 'MinerU 检测中'">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18V6l6 6 6-6v12" /><path d="M9 18h6" /></svg><span class="status-dot" />
          <v-tooltip activator="parent" location="bottom">{{ mineruHealth === 'ready' ? 'MinerU 就绪' : mineruHealth === 'offline' ? 'MinerU 未就绪' : mineruStatusText }}</v-tooltip>
        </button>
        <v-btn class="header-icon-button project-switch-button" icon size="small" aria-label="切换项目（暂未开放）"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5h6l1.8 2h9.2v8.8a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7Z" /><path d="M7 4h10m0 0-2-2m2 2-2 2" /></svg><v-tooltip activator="parent" location="bottom">切换项目（暂未开放）</v-tooltip></v-btn>
        <v-btn class="header-icon-button detection-mode-button" :class="{ 'detection-mode-button--comparison': detectionMode === 'comparison' }" icon size="small" :aria-label="detectionMode === 'placement' ? '当前为落图模式，点击切换到对照模式' : '当前为对照模式，点击切换到落图模式'" @click="detectionMode = detectionMode === 'placement' ? 'comparison' : 'placement'">
          <svg v-if="detectionMode === 'placement'" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2.5" /><path d="M12 2v3m0 14v3M2 12h3m14 0h3" /></svg>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="7" height="11" rx="1.5" /><rect x="14" y="8" width="7" height="11" rx="1.5" /><path d="m9 19 3-3-3-3m6-8-3 3 3 3" /></svg>
          <v-tooltip activator="parent" location="bottom">{{ detectionMode === 'placement' ? '落图模式：点击切换为对照模式' : '对照模式：点击切换为落图模式' }}</v-tooltip>
        </v-btn>
        <v-menu v-model="shortcutMenu" location="bottom end" :close-on-content-click="false">
        <template #activator="{ props }"><v-btn v-bind="props" class="header-icon-button shortcut-button" icon size="small" aria-label="查看快捷键">?<v-tooltip activator="parent" location="bottom">快捷键（F1）</v-tooltip></v-btn></template>
        <v-card class="shortcut-card" min-width="330">
          <v-card-title>系统快捷键</v-card-title>
          <v-list density="compact">
            <v-list-item title="F1" subtitle="打开或关闭快捷键面板" />
            <v-list-item :title="shortcutSettings.analyze" subtitle="开始智能编号" />
            <v-list-item :title="shortcutSettings.save" subtitle="保存当前校对结果" />
            <v-list-item title="Ctrl+Z / Ctrl+Y" subtitle="撤销 / 重做最近编辑" />
            <v-list-item :title="shortcutSettings.addWeld" subtitle="进入或退出人工添加焊口模式" />
            <v-list-item :title="shortcutSettings.addValve" subtitle="进入或退出人工增加阀门模式" />
            <v-list-item :title="shortcutSettings.addFlange" subtitle="进入或退出人工增加法兰模式" />
            <v-list-item :title="shortcutSettings.addSupport" subtitle="进入或退出人工增加支架模式" />
            <v-list-item :title="shortcutSettings.deleteWeld" subtitle="删除当前选中焊口，可用 Ctrl+Z 撤销" />
            <v-list-item title="← / →" subtitle="切换上一张或下一张设计图页" />
            <v-list-item title="+ / − / 0" subtitle="放大、缩小、适合画布" />
            <v-list-item :title="`${shortcutSettings.swapWeld}（按两次）`" subtitle="暂存第一个焊口，再与第二个焊口交换编号" />
            <v-list-item title="鼠标中键拖动" subtitle="平移 PDF 画布" />
            <v-list-item title="双击焊口号 / Esc" subtitle="编辑编号 / 取消编辑与选择" />
          </v-list>
        </v-card>
        </v-menu>
        <v-btn class="header-icon-button settings-button" icon size="small" aria-label="系统设置" @click="systemSettingsDialog = true"><span class="settings-glyph" aria-hidden="true">⚙</span><v-tooltip activator="parent" location="bottom">系统设置</v-tooltip></v-btn>
      </div>
    </v-app-bar>

    <v-navigation-drawer permanent :rail="!leftDrawerOpen" :rail-width="24" :width="leftDrawerWidth" class="control-drawer">
      <button v-if="!leftDrawerOpen" type="button" class="drawer-edge-toggle drawer-edge-toggle--left" aria-label="打开识别输入区域" @click="leftDrawerOpen = true"><span>›</span></button>
      <template v-else>
        <div class="drawer-heading">
          <div><div class="drawer-heading__title">{{ leftPanelTab === 'reference' ? '对照图提示' : '识别输入与操作' }}</div></div>
          <button type="button" class="drawer-view-toggle" :class="{ 'drawer-view-toggle--active': leftPanelTab === 'reference' }" :disabled="leftPanelTab === 'input' && !result?.ep3dReferenceInventory?.documents?.length" :aria-label="leftPanelTab === 'reference' ? '切换到输入操作' : '切换到对照图提示'" @click="leftPanelTab = leftPanelTab === 'reference' ? 'input' : 'reference'">
            <svg v-if="leftPanelTab === 'input'" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="7" height="15" rx="1.5" /><rect x="13.5" y="7.5" width="7" height="12" rx="1.5" /><path d="M7 9h.01M7 13h.01M17 12h.01M17 16h.01" /></svg>
            <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z" /><path d="M8 9h8M8 13h8M8 17h5" /></svg>
            <v-tooltip activator="parent" location="bottom">{{ leftPanelTab === 'reference' ? '切换到输入操作' : '查看对照图识别提示' }}</v-tooltip>
          </button>
        </div>
        <div class="drawer-scroll pa-4">
        <template v-if="leftPanelTab === 'input'">
        <div class="section-label">01 / 工程输入</div>
        <v-btn-toggle :model-value="projectMode" mandatory divided color="secondary" density="compact" class="w-100 mb-3" @update:model-value="changeProjectMode">
          <v-btn value="single" class="flex-grow-1">单个 PDF<v-tooltip activator="parent">选择一份待标识 ISO PDF</v-tooltip></v-btn>
          <v-btn value="folder" class="flex-grow-1">文件夹 PDF<v-tooltip activator="parent">读取文件夹中的全部 PDF，切换模式不会报空文件错误</v-tooltip></v-btn>
        </v-btn-toggle>

        <v-file-input v-if="projectMode === 'single'" accept="application/pdf,.pdf" class="drop-file-input" label="点击或拖拽待标识 ISO PDF" prepend-icon="" clearable @update:model-value="setProjectFiles"><v-tooltip activator="parent">可点击选择或把 PDF 拖入此区域，选择后立即渲染</v-tooltip></v-file-input>
        <v-file-input v-else webkitdirectory directory multiple class="drop-file-input folder-file-input" label="点击或拖拽工程文件夹" prepend-icon="" clearable @update:model-value="setProjectFiles"><template #selection="{ fileNames }"><span class="folder-selection">已选择 {{ fileNames.length }} 个文件</span></template><v-tooltip activator="parent">可点击选择或拖入文件夹，自动过滤非 PDF 文件</v-tooltip></v-file-input>

        <v-list v-if="projectPdfs.length > 1" density="compact" class="project-list my-2" border>
          <v-list-item v-for="(file, index) in projectPdfs" :key="file.webkitRelativePath || file.name" :active="activeProjectIndex === index" color="secondary" @click="selectProject(index)">
            <v-list-item-title>{{ file.webkitRelativePath || file.name }}</v-list-item-title>
            <template #append><v-chip size="x-small" :color="projectResults[index] ? 'success' : 'default'">{{ projectResults[index] ? '已分析' : '待分析' }}</v-chip></template>
            <v-tooltip activator="parent">切换当前预览和编辑的 PDF</v-tooltip>
          </v-list-item>
        </v-list>

        <div v-if="projectMode === 'single'" class="parameter-grid mt-3">
          <v-text-field v-model="startPage" type="number" min="1" label="设计图起始页"><v-tooltip activator="parent">仅指定当前单个设计图 PDF 的研究起始页</v-tooltip></v-text-field>
          <v-text-field v-model="endPage" type="number" min="1" label="设计图结束页"><v-tooltip activator="parent">仅作用于当前单个设计图 PDF；留空研究到末页</v-tooltip></v-text-field>
        </div>

        <v-divider class="my-4" />
        <div class="section-label">参考输入</div>
        <v-btn-toggle :model-value="referenceMode" mandatory divided color="secondary" density="compact" class="w-100 mb-3" @update:model-value="changeReferenceMode">
          <v-btn value="none" class="flex-grow-1">仅图元<v-tooltip activator="parent">不使用外部对照数据</v-tooltip></v-btn>
          <v-btn value="pdf" class="flex-grow-1">PDF 对照<v-tooltip activator="parent">对照 PDF 与 ISO PDF 将并排渲染，PCF 可作为拓扑补充</v-tooltip></v-btn>
        </v-btn-toggle>
        <template v-if="referenceMode === 'pdf'">
          <v-btn-toggle :model-value="referenceProjectMode" mandatory divided color="secondary" density="compact" class="w-100 mb-3 reference-source-toggle" @update:model-value="changeReferenceProjectMode">
            <v-btn value="single" class="flex-grow-1">单个对照 PDF<v-tooltip activator="parent">选择一份已标识对照图</v-tooltip></v-btn>
            <v-btn value="folder" class="flex-grow-1">对照文件夹<v-tooltip activator="parent">读取文件夹下全部分管线 PDF，并共同参与拓扑匹配</v-tooltip></v-btn>
          </v-btn-toggle>
          <v-file-input v-if="referenceProjectMode === 'single'" accept="application/pdf,.pdf" class="drop-file-input" label="点击或拖拽对照 PDF" prepend-icon="" clearable @update:model-value="setReferenceFiles"><v-tooltip activator="parent">可点击选择或拖入对照 PDF，配对后显示在画布右侧</v-tooltip></v-file-input>
          <template v-else>
            <v-file-input webkitdirectory directory multiple class="drop-file-input folder-file-input" label="点击或拖拽对照 PDF 文件夹" prepend-icon="" clearable @update:model-value="setReferenceFolderFiles"><template #selection="{ fileNames }"><span class="folder-selection">已选择 {{ fileNames.length }} 个文件</span></template><v-tooltip activator="parent">仅从该文件夹读取 PDF；输入框只显示文件数量，避免多文件名挤压</v-tooltip></v-file-input>
          </template>
          <v-select ref="pcfSelector" v-model="selectedPcfFolder" :items="pcfFolderOptions" item-title="title" item-value="value" label="PCF 选择（可选）" clearable no-data-text="后端 PCF 目录中暂无可用文件夹" class="pcf-folder-selector" :menu-props="{ contentClass: 'pcf-folder-menu', width: pcfSelectorWidth || undefined, minWidth: pcfSelectorWidth || undefined, maxWidth: pcfSelectorWidth || undefined }" @click:control="syncPcfMenuWidth" @update:model-value="value => { selectedPcfFolder = value || null }" @update:menu="value => value && syncPcfMenuWidth()">
            <template #item="{ props, item }"><v-list-item v-bind="props" :subtitle="Number.isFinite(Number(pcfOption(item).count)) ? `${pcfOption(item).count} 个 PCF` : ''" /></template>
            <template #selection="{ item }"><span v-if="pcfSelectionLabel(item)">{{ pcfSelectionLabel(item) }}</span></template>
            <v-tooltip activator="parent">从服务器已有的 PCF 文件夹中选择，无需重新上传</v-tooltip>
          </v-select>
          <div v-if="referenceProjectMode === 'folder' && (referencePdfs.length || selectedPcfFolderInfo)" class="reference-file-summary mb-2">
            <v-chip size="small" color="secondary">PDF {{ referencePdfs.length }}<v-tooltip activator="parent">文件夹中识别到的对照 PDF 数量</v-tooltip></v-chip>
            <v-chip v-if="selectedPcfFolderInfo" size="small" color="primary">PCF {{ selectedPcfFolderInfo.count }}<v-tooltip activator="parent">后端文件夹中的 PCF 拓扑文件数量</v-tooltip></v-chip>
          </div>
          <v-list v-if="referencePdfs.length > 1" density="compact" class="project-list reference-list my-2" border>
            <v-list-item v-for="(file, index) in referencePdfs" :key="file.webkitRelativePath || file.name" :active="activeReferenceIndex === index" :disabled="!referenceResearchReady" color="secondary" @click="selectReference(index)">
              <v-list-item-title>{{ file.webkitRelativePath || file.name }}</v-list-item-title>
              <template #append><v-chip size="x-small">{{ referenceResearchReady ? index + 1 : '待匹配' }}</v-chip></template>
              <v-tooltip activator="parent">{{ referenceResearchReady ? '切换 Canvas 中预览的对照 PDF；分析仍使用文件夹内全部 PDF' : '完成图元研究并建立对照关系后才允许加载到 Canvas' }}</v-tooltip>
            </v-list-item>
          </v-list>
        </template>

        <v-divider class="my-4" />
        <button type="button" class="reference-number-toggle mt-1" :class="{ 'reference-number-toggle--active': useReferenceNumber }" :aria-pressed="useReferenceNumber" @click="useReferenceNumber = !useReferenceNumber">
          <span class="reference-number-toggle__icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M9.5 7.5 7 5a3.5 3.5 0 0 0-5 5l3 3a3.5 3.5 0 0 0 5 0l1-1" /><path d="m14.5 16.5 2.5 2.5a3.5 3.5 0 0 0 5-5l-3-3a3.5 3.5 0 0 0-5 0l-1 1" /><path d="m8.5 15.5 7-7" /></svg></span>
          <span class="reference-number-toggle__label">使用对照图编号</span>
          <span class="reference-number-toggle__state"><i aria-hidden="true" />{{ useReferenceNumber ? '已开启' : '未开启' }}</span>
          <v-tooltip activator="parent">开启后，已匹配焊口直接采用对照图中的编号；未匹配焊口使用系统默认顺序编号</v-tooltip>
        </button>
        <div v-if="!useReferenceNumber" class="number-format-grid mt-3">
          <v-text-field v-model="numberPrefix" label="编号前缀" maxlength="20"><v-tooltip activator="parent">例如填写 F，将生成 F1、F2、F3</v-tooltip></v-text-field>
          <v-text-field v-model="startNumber" type="number" min="1" label="编号开始数字"><v-tooltip activator="parent">每一页都从此编号独立开始，不跨页连续累加</v-tooltip></v-text-field>
          <v-text-field v-model="numberSuffix" label="编号后缀" maxlength="20"><v-tooltip activator="parent">可留空，例如填写 A 将生成 F1A、F2A</v-tooltip></v-text-field>
        </div>
        <v-btn block color="accent" size="large" :loading="loading" :disabled="!targetPdf" class="primary-action-button mt-2" @click="analyze">{{ detectionMode === 'placement' ? '智能编号' : '开始对照识别' }}<v-tooltip activator="parent">{{ detectionMode === 'placement' ? '识别焊口、阀门、法兰和支架，并按类型自动生成编号' : '识别红色编号框和引线，直接恢复已有焊口号' }}</v-tooltip></v-btn>
        </template>
        <template v-else>
          <v-alert v-if="!referenceHintPage" type="info" variant="tonal" density="compact">当前编辑页尚无可显示的对照识别结果，可切回“输入操作”完成解析或选择对照图。</v-alert>
          <template v-else>
            <div class="reference-hint-meta">
              <strong>{{ referenceHintDocument?.file }}</strong>
              <span>第 {{ referenceHintPage.page }} 页 · 点击对象可在右侧对照图中定位</span>
            </div>
            <section v-for="group in referenceHintGroups" :key="group.key" class="reference-hint-group">
              <div class="reference-hint-group__heading"><span><i :style="{ backgroundColor: group.color }" />{{ group.title }}</span><strong>{{ group.items.length }}</strong></div>
              <v-list v-if="group.items.length" density="compact" class="reference-hint-list" border>
                <v-list-item v-for="item in group.items" :key="`${group.key}-${item.label}-${item.point.join('-')}`" :active="referenceFocus?.label === item.label && referenceFocus?.type === item.type" color="secondary" @click="jumpToReferenceHint(item)">
                  <v-list-item-title>{{ item.label }}</v-list-item-title>
                  <v-list-item-subtitle>{{ item.geometryVerified === false ? '引线定位 · 几何待确认' : '已识别 · 点击定位' }}</v-list-item-subtitle>
                  <template #append><span class="reference-hint-locate" aria-hidden="true">⌖</span></template>
                </v-list-item>
              </v-list>
              <div v-else class="reference-hint-empty">未识别到{{ group.title }}</div>
            </section>
          </template>
        </template>
        </div>
        <button type="button" class="drawer-edge-toggle drawer-edge-toggle--left" aria-label="折叠识别输入区域" @click="leftDrawerOpen = false"><span>‹</span></button>
      </template>
    </v-navigation-drawer>

    <v-main class="main-area">
      <v-container fluid class="main-container pa-3">
        <v-snackbar v-model="errorOpen" color="error" location="bottom" timeout="5000">{{ error }}<template #actions><v-btn variant="text" @click="error = ''">关闭<v-tooltip activator="parent">关闭错误消息</v-tooltip></v-btn></template></v-snackbar>
        <v-snackbar v-if="!loading" v-model="noticeOpen" color="info" location="bottom" timeout="4000">{{ notice }}<template #actions><v-btn variant="text" @click="notice = ''">关闭<v-tooltip activator="parent">关闭状态消息</v-tooltip></v-btn></template></v-snackbar>
        <v-alert v-if="result?.pcf?.degraded" type="warning" variant="tonal" density="compact" class="mb-3">PCF 当前使用内置降级解析器，拓扑完整性可能受影响：{{ result.pcf.warning }}</v-alert>

        <v-card v-if="!targetPdf" class="empty-card" variant="tonal" color="primary"><v-card-title>选择 ISO PDF 或整个工程文件夹</v-card-title><v-card-text>图纸将在高分辨率 Canvas 中立即渲染。</v-card-text></v-card>

        <template v-else>
          <div v-if="loading" class="analysis-progress-shell" :class="{ 'analysis-progress-shell--collapsed': !progressPanelOpen }" role="status" aria-live="polite">
            <div v-if="progressPanelOpen" class="analysis-progress-panel">
              <div class="analysis-progress-panel__header">
                <span>{{ analysisProgress.message }}</span>
                <div class="analysis-progress-panel__actions"><strong>{{ Math.round(analysisProgressPercent) }}%</strong><button v-if="activeAnalysisJobId" type="button" class="analysis-progress-cancel" :disabled="cancellingAnalysis" @click.stop="cancelActiveAnalysis">{{ cancellingAnalysis ? '取消中' : '取消解析' }}</button></div>
              </div>
              <v-progress-linear :model-value="analysisProgressPercent" color="accent" height="8" rounded striped />
              <div class="analysis-progress-panel__meta"><span>{{ analysisProgress.fileName }}</span><span>对照 {{ analysisProgress.completedReferenceFiles || 0 }} / {{ analysisProgress.totalReferenceFiles || 0 }} · 设计页 {{ analysisProgress.completedPages }} / {{ analysisProgress.totalPages }} · 工程 {{ projectProgress }} / {{ projectPdfs.length || 1 }}</span></div>
            </div>
            <button type="button" class="analysis-progress-spine" :aria-label="progressPanelOpen ? '收起解析进度' : '展开解析进度'" :aria-expanded="progressPanelOpen" @click="progressPanelOpen = !progressPanelOpen">
              <svg class="analysis-progress-spine__icon" :class="{ 'analysis-progress-spine__icon--expanded': progressPanelOpen }" viewBox="0 0 20 20" aria-hidden="true"><path d="m5.5 7.5 4.5 4.5 4.5-4.5" /></svg>
            </button>
          </div>

          <v-card v-if="projectPdfs.length > 1" class="document-card mb-2" variant="outlined"><v-tabs :model-value="activeProjectIndex" color="secondary" density="compact" @update:model-value="selectProject"><v-tab v-for="(file, index) in projectPdfs" :key="file.webkitRelativePath || file.name" :value="index">{{ file.name }}</v-tab></v-tabs></v-card>

          <div class="work-grid">
            <v-card class="canvas-card" elevation="2">
              <v-toolbar density="compact" color="#1c2b35" theme="dark">
                <v-chip class="ml-2" size="small" color="primary">待标识 ISO PDF</v-chip>
                <v-chip v-if="referenceDocument" class="ml-2" size="small" color="secondary">对照 PDF · P{{ activeReferencePage }} {{ referencePdfs.length > 1 ? `· ${activeReferenceIndex + 1}/${referencePdfs.length}` : '' }}</v-chip>
                <v-divider vertical class="mx-2" />
                <template v-if="result">
                  <v-btn v-for="page in pages" :key="page.page" size="small" class="page-status-button" :class="{ 'page-status-button--active': currentPage === page.page, 'page-status-button--complete': pageMatchSummary(page).complete, 'page-status-button--incomplete': !pageMatchSummary(page).complete }" :variant="currentPage === page.page ? 'flat' : 'tonal'" :color="pageButtonColor(page)" @click="changePage(page.page)">P{{ page.page }}</v-btn>
                </template>
                <template v-else>
                  <v-btn size="small" :disabled="previewPage <= 1" @click="changePage(previewPage - 1)">上一页</v-btn><span class="page-indicator">{{ previewPage }} / {{ targetPageCount || '···' }}</span><v-btn size="small" :disabled="previewPage >= targetPageCount" @click="changePage(previewPage + 1)">下一页</v-btn>
                </template>
                <v-spacer />
                <v-btn size="small" icon :disabled="!canUndo" aria-label="撤销" @click="undo"><span aria-hidden="true">↶</span></v-btn>
                <v-btn size="small" icon :disabled="!canRedo" aria-label="重做" @click="redo"><span aria-hidden="true">↷</span></v-btn>
                <v-btn size="small" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'weld' }" :color="manualAddMode && manualAddType === 'weld' ? 'warning' : undefined" @click="toggleManualAddMode('weld')">+ 焊口</v-btn>
                <v-btn size="small" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'valve' }" :color="manualAddMode && manualAddType === 'valve' ? 'warning' : undefined" @click="toggleManualAddMode('valve')">+ 阀门</v-btn>
                <v-btn size="small" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'flange' }" :color="manualAddMode && manualAddType === 'flange' ? 'warning' : undefined" @click="toggleManualAddMode('flange')">+ 法兰</v-btn>
                <v-btn size="small" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'support' }" :color="manualAddMode && manualAddType === 'support' ? 'warning' : undefined" @click="toggleManualAddMode('support')">+ 支架</v-btn>
                <v-btn size="small" @click="zoomBy(.8)">−</v-btn><span class="zoom-indicator">{{ Math.round(zoom * 100) }}%</span><v-btn size="small" @click="zoomBy(1.25)">+</v-btn><v-btn size="small" @click="fitCanvas">适合</v-btn>
              </v-toolbar>

              <div ref="canvasViewport" class="canvas-viewport" :class="{ panning: canvasPanState, 'manual-adding': manualAddMode }" @click="onCanvasClick" @wheel.prevent="onCanvasWheel" @pointerdown="startCanvasPan" @pointermove="moveCanvasPan" @pointerup="endCanvasPan" @pointercancel="endCanvasPan" @auxclick.prevent>
                <div ref="canvasSurface" class="canvas-surface" :style="canvasSurfaceStyle">
                  <canvas ref="pdfCanvas" class="pdf-canvas"></canvas>
                  <div class="reference-focus-marker" :style="referenceFocusStyle" :title="referenceFocus?.label || ''"><span>{{ referenceFocus?.label }}</span></div>
                  <svg v-if="result && pageData" class="leader-layer" :viewBox="`0 0 ${canvasLayout.width} ${canvasLayout.height}`" preserveAspectRatio="none" aria-hidden="true">
                    <line v-for="item in pageData.candidates.filter(candidate => candidate.included !== false)" :key="`line-${item.id}`" :x1="anchorPoint(item).x" :y1="anchorPoint(item).y" :x2="leaderEnd(item).x" :y2="leaderEnd(item).y" :style="leaderStyle(item)" />
                  </svg>
                  <div v-for="item in (result && pageData ? pageData.candidates.filter(candidate => candidate.origin === 'manual' && candidate.included !== false) : [])" :key="`anchor-${item.id}`" class="manual-weld-anchor" :class="{ selected: selectedId === item.id, dragging: anchorDragState?.id === item.id }" :style="{ left: `${anchorPoint(item).x}px`, top: `${anchorPoint(item).y}px` }" role="button" tabindex="0" @click.stop="selectCandidate(item)" @pointerdown="startAnchorDrag($event, item)" @pointermove="moveAnchor($event, item)" @pointerup="endAnchorDrag($event, item)" @pointercancel="endAnchorDrag($event, item)"></div>
                  <div v-for="item in (result && pageData ? pageData.candidates : [])" :key="item.id" class="weld-label" :class="markerClass(item)" :style="labelPosition(item)" role="button" tabindex="0" @click.stop="selectCandidate(item)" @dblclick="beginInlineEdit($event, item)" @pointerdown="startLabelDrag($event, item)" @pointermove="moveLabel($event, item)" @pointerup="endLabelDrag($event, item)" @pointercancel="endLabelDrag($event, item)">
                    <input v-if="editingId === item.id" v-model="editingValue" class="inline-weld-input" @pointerdown.stop @dblclick.stop @keydown.enter.prevent="commitInlineEdit(item)" @keydown.esc.prevent="cancelInlineEdit" /><span v-else>{{ item.number || '?' }}</span>
                  </div>
                </div>
                <v-progress-circular v-if="rendering" class="canvas-progress" indeterminate color="secondary" size="36" />
              </div>
            </v-card>

          </div>
        </template>
      </v-container>
    </v-main>

    <v-navigation-drawer permanent :rail="!reviewDrawerOpen" :rail-width="24" location="right" :width="reviewDrawerWidth" class="review-drawer">
      <button v-if="!reviewDrawerOpen" type="button" class="drawer-edge-toggle drawer-edge-toggle--right" aria-label="打开终审区域" @click="reviewDrawerOpen = true"><span>‹</span></button>
      <v-card v-else class="review-card" elevation="0">
        <v-card-title class="review-title"><span>拓扑标识终审</span></v-card-title>
        <template v-if="result">
          <div class="metric-row"><v-chip size="small">焊口 {{ weldCandidateCount }}<v-tooltip activator="parent">当前 PDF 的焊口候选数</v-tooltip></v-chip><v-chip size="small">管件 {{ designComponentCount }}<v-tooltip activator="parent">阀门、法兰和支架数量</v-tooltip></v-chip><v-chip size="small">匹配 {{ matchedCount }}<v-tooltip activator="parent">对照 PDF 焊口拓扑匹配数</v-tooltip></v-chip></div>
          <v-divider />
          <div class="review-scroll pa-3">
            <div class="section-label">分类标识外观</div>
            <div class="marker-appearance-card">
              <div class="marker-appearance-tabs" role="tablist" aria-label="分类标识外观">
                <button v-for="group in markerAppearanceGroups" :key="group.key" type="button" role="tab" :aria-selected="markerAppearanceTab === group.key" :class="{ active: markerAppearanceTab === group.key }" @click="markerAppearanceTab = group.key"><i :style="{ backgroundColor: group.style.color }" />{{ group.title.replace('标识', '') }}</button>
              </div>
              <div :key="activeMarkerAppearanceGroup.key" class="marker-appearance-panel" role="tabpanel">
                  <div class="marker-appearance-heading"><span><i :style="{ backgroundColor: activeMarkerAppearanceGroup.style.color }" /><strong>{{ activeMarkerAppearanceGroup.title }}</strong></span><small>{{ activeMarkerAppearanceGroup.subtitle }}</small></div>
                  <div class="marker-appearance-form">
                  <v-select v-model="activeMarkerAppearanceGroup.style.shape" :items="shapeOptions" density="compact" label="外形" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" />
                  <div class="parameter-grid mt-2"><v-text-field v-model.number="activeMarkerAppearanceGroup.style.frameSize" type="number" min="18" max="64" density="compact" label="框尺寸" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /><v-text-field v-model.number="activeMarkerAppearanceGroup.style.fontSize" type="number" min="7" max="24" density="compact" label="字号" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /></div>
                  <div class="parameter-grid mt-2"><v-text-field v-model.number="activeMarkerAppearanceGroup.style.lineWidth" type="number" min="0.5" max="4" step="0.1" density="compact" label="线宽" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /><v-text-field v-model="activeMarkerAppearanceGroup.style.color" type="color" density="compact" label="颜色" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /></div>
                  <v-slider v-model="activeMarkerAppearanceGroup.style.fillOpacity" min="0" max="1" step="0.05" color="accent" label="填充透明度" thumb-label class="mt-2" @start="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @end="commitHistory" />
                  </div>
              </div>
            </div>
            <v-btn block variant="tonal" color="secondary" class="mt-2" @click="reflowAllLabelPositions(true)">重新优化标识位置<v-tooltip activator="parent">优先处理拥挤区域，并避让文字、管线、焊口锚点、其他标识和引线</v-tooltip></v-btn>
            <v-divider class="my-4" />
            <template v-if="selectedCandidate">
              <div class="section-label">选中对象</div>
              <v-text-field v-model="selectedCandidate.number" label="编号" @focus="beginHistory('修改编号')" @blur="commitHistory"><v-tooltip activator="parent">修改选中对象的字符串编号</v-tooltip></v-text-field>
              <v-btn block color="secondary" variant="tonal" class="mt-3" @click="requestRenumberFromSelected">以该对象为起点重新智能编号<v-tooltip activator="parent">确认后仅在当前页循环重编同一类型的对象</v-tooltip></v-btn>
              <v-list density="compact" class="evidence-list mt-3"><v-list-item title="证据" :subtitle="selectedCandidate.evidence" /><v-list-item title="置信度" :subtitle="`${Math.round(selectedCandidate.confidence * 100)}%`" /><v-list-item title="参考身份" :subtitle="selectedCandidate.referenceLabel || '未匹配'" /></v-list>
              <v-btn block class="mt-3" :color="selectedCandidate.included === false ? 'secondary' : 'error'" variant="tonal" @click="toggleCandidate(selectedCandidate)">{{ selectedCandidate.included === false ? '恢复这个对象' : '排除这个误识别对象' }}<v-tooltip activator="parent">切换该对象是否参与保存与导出</v-tooltip></v-btn>
            </template>
          </div>
          <v-divider />
          <v-card-actions class="review-actions"><v-btn size="small" @click="numberWelds">全部重编<v-tooltip activator="parent">每一页分别按画面位置从设置的起始编号开始</v-tooltip></v-btn><v-btn size="small" @click="save">保存<v-tooltip activator="parent">保存当前校对数据</v-tooltip></v-btn><v-menu><template #activator="{ props }"><v-btn v-bind="props" size="small" color="primary">导出<v-tooltip activator="parent">选择导出 CSV 或标识 PDF；AI 训练样本由后端在解析完成后自动生成</v-tooltip></v-btn></template><v-list density="compact"><v-list-item title="导出 CSV" @click="exportCsv" /><v-list-item title="导出标识 PDF" @click="exportPdf" /></v-list></v-menu></v-card-actions>
        </template>
        <v-card-text v-else class="review-placeholder">
          <div class="review-placeholder__title">等待识别结果</div>
          <div class="review-placeholder__text">完成识别输入设置并开始研究后，此处用于候选焊口终审、外观调整、重新编号与导出。</div>
        </v-card-text>
      </v-card>
      <button v-if="reviewDrawerOpen" type="button" class="drawer-edge-toggle drawer-edge-toggle--right" aria-label="折叠终审区域" @click="reviewDrawerOpen = false"><span>›</span></button>
    </v-navigation-drawer>

    <v-dialog v-model="draftRecoveryDialog" persistent max-width="520">
      <v-card>
        <v-card-title>发现可恢复的标识工作区</v-card-title>
        <v-card-text>
          <div class="mb-3">系统已按优先级列出全部可用版本。默认选择优先级最高的版本，也可以自由切换。</div>
          <v-select v-model="selectedRecoveryKey" :items="recoveryOptions" item-title="title" item-value="key" label="选择要恢复的版本" @update:model-value="selectRecoveryVersion">
            <template #item="{ props, item }"><v-list-item v-bind="props" :subtitle="`优先级 ${recoveryOption(item).priority || '-'} · ${recoveryOption(item).savedAt || ''}`" /></template>
          </v-select>
        </v-card-text>
        <v-card-actions><v-btn variant="text" @click="discardWorkspaceDraft">忽略</v-btn><v-spacer /><v-btn color="primary" @click="restoreWorkspaceDraft">恢复并继续编辑</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="renumberConfirmDialog" persistent max-width="500">
      <v-card>
        <v-card-title>确认重新智能编号</v-card-title>
        <v-card-text>将以第 {{ pendingRenumberCandidate?.page || '-' }} 页对象“{{ pendingRenumberCandidate?.number || '?' }}”为起点，循环重新编号当前页 {{ pendingRenumberCount }} 个同类有效对象。此操作会覆盖这些对象现有编号，但可以撤销。</v-card-text>
        <v-card-actions><v-btn variant="text" @click="cancelRenumberConfirmation">取消</v-btn><v-spacer /><v-btn color="secondary" @click="renumberFromSelected">确认重新编号</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="duplicateJobDialog" persistent max-width="540">
      <v-card>
        <v-card-title>发现已完成的相同解析任务</v-card-title>
        <v-card-text>
          <div>图纸“{{ duplicateJobInfo?.fileName || '-' }}”已有相同配置的解析结果。</div>
          <div class="mt-2 text-medium-emphasis">原任务解析范围：第 {{ duplicateJobInfo?.analyzedRange?.[0] || 1 }} 页至第 {{ duplicateJobInfo?.analyzedRange?.[1] || duplicateJobInfo?.totalPages || '-' }} 页；完成时间：{{ duplicateJobInfo?.existingUpdatedAt || '-' }}。</div>
          <div class="mt-3">可以直接读取已有结果，也可以重新创建解析任务。</div>
        </v-card-text>
        <v-card-actions><v-btn variant="text" @click="resolveDuplicateJob('cancel')">取消</v-btn><v-spacer /><v-btn variant="tonal" color="secondary" @click="resolveDuplicateJob('reuse')">使用已有结果</v-btn><v-btn color="accent" @click="resolveDuplicateJob('reanalyze')">重新解析</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="systemSettingsDialog" max-width="620">
      <v-card class="settings-card">
        <v-card-title>系统设置</v-card-title>
        <v-card-text>
          <div class="settings-section">
            <div class="section-label">界面布局</div>
            <v-switch v-model="leftDrawerOpen" color="secondary" hide-details label="显示识别输入与操作抽屉" />
            <v-switch v-model="reviewDrawerOpen" color="secondary" hide-details label="显示拓扑标识终审抽屉" />
            <v-switch v-model="tooltipsEnabled" color="secondary" hide-details label="启用全局 Tooltip 提示" />
          </div>
          <v-divider class="my-4" />
          <div class="settings-section">
            <div class="section-label">草稿与恢复</div>
            <div class="shortcut-settings__hint">查看浏览器中自动保存的图纸工作区，可继续编辑或删除不需要的草稿。</div>
            <v-btn block color="secondary" variant="tonal" class="mt-3" @click="openDraftManager">打开草稿</v-btn>
          </div>
          <v-divider class="my-4" />
          <div class="settings-section">
            <div class="section-label">焊口默认外观</div>
            <div class="parameter-grid"><v-select v-model="markerStyle.shape" :items="shapeOptions" label="外形" /><v-text-field v-model.number="markerStyle.frameSize" type="number" min="18" max="64" label="框尺寸" /></div>
            <div class="parameter-grid mt-3"><v-text-field v-model.number="markerStyle.fontSize" type="number" min="7" max="24" label="字号" /><v-text-field v-model="markerStyle.color" type="color" label="颜色" /></div>
          </div>
          <v-divider class="my-4" />
          <div class="settings-section shortcut-settings">
            <div class="section-label">快捷键</div>
            <div class="shortcut-settings__hint">修饰键和主按键可分开设置。选中“按键”输入框后按下目标键，Backspace 可清空主按键。</div>
            <div class="shortcut-editor-list mt-3">
              <div v-for="row in shortcutSettingRows" :key="row.action" class="shortcut-editor-row">
                <span class="shortcut-editor-row__label">{{ row.label }}</span>
                <v-select :model-value="shortcutEditors[row.action].modifier" :items="shortcutModifierOptions" label="修饰键" density="compact" hide-details @update:model-value="updateShortcutModifier(row.action, $event)" />
                <v-text-field :model-value="shortcutEditors[row.action].key" label="按键" density="compact" hide-details readonly @keydown="captureShortcutKey($event, row.action)" />
              </div>
            </div>
            <v-alert v-if="shortcutConflict" type="warning" variant="tonal" density="compact" class="mt-3">{{ shortcutConflict }}</v-alert>
            <v-btn block variant="outlined" class="mt-3" @click="restoreDefaultShortcuts">恢复默认快捷键</v-btn>
          </div>
          <v-btn block color="primary" variant="tonal" class="mt-5" @click="systemSettingsDialog = false; symbolConfigDialog = true">打开焊口符号研究配置</v-btn>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn color="primary" @click="systemSettingsDialog = false">完成</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="draftManagerDialog" max-width="720">
      <v-card class="draft-manager-card">
        <v-card-title>浏览器草稿</v-card-title>
        <v-card-subtitle>草稿保存在当前浏览器中，按最近保存时间排序。</v-card-subtitle>
        <v-progress-linear v-if="draftManagerLoading" indeterminate color="secondary" />
        <v-card-text>
          <v-alert v-if="!draftManagerLoading && !draftEntries.length" type="info" variant="tonal" density="compact">当前浏览器中没有可用草稿。</v-alert>
          <v-list v-else class="draft-manager-list" lines="three">
            <v-list-item v-for="entry in draftEntries" :key="entry.key">
              <template #prepend><span class="draft-manager-file-icon" aria-hidden="true">PDF</span></template>
              <v-list-item-title>{{ entry.payload?.fileName || '未命名图纸' }}</v-list-item-title>
              <v-list-item-subtitle>{{ draftSavedAt(entry) }} · {{ draftCandidateCount(entry) }} 个标识 · 对照 PDF {{ entry.payload?.referenceFiles?.length || 0 }} 份</v-list-item-subtitle>
              <template #append>
                <div class="draft-manager-actions">
                  <v-chip v-if="!draftHasStoredTarget(entry)" size="x-small" color="warning" variant="tonal">旧版草稿</v-chip>
                  <v-btn size="small" color="secondary" variant="tonal" :disabled="!canOpenManagedDraft(entry)" @click.stop="openManagedDraft(entry)">打开</v-btn>
                  <v-btn size="small" color="error" variant="text" @click.stop="draftDeleteTarget = entry">删除</v-btn>
                </div>
              </template>
            </v-list-item>
          </v-list>
          <v-alert v-if="draftEntries.some(entry => !draftHasStoredTarget(entry))" type="warning" variant="tonal" density="compact" class="mt-3">旧版草稿未保存主图文件。先在工程输入中加载同名 PDF，即可从此处打开。</v-alert>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn color="primary" @click="draftManagerDialog = false">关闭</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog :model-value="Boolean(draftDeleteTarget)" persistent max-width="460" @update:model-value="value => { if (!value) draftDeleteTarget = null }">
      <v-card>
        <v-card-title>确认删除草稿</v-card-title>
        <v-card-text>确定删除“{{ draftDeleteTarget?.payload?.fileName || '未命名图纸' }}”吗？删除后无法从浏览器草稿中恢复。</v-card-text>
        <v-card-actions><v-btn variant="text" @click="draftDeleteTarget = null">取消</v-btn><v-spacer /><v-btn color="error" @click="deleteManagedDraft">删除草稿</v-btn></v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="symbolConfigDialog" max-width="720">
      <v-card class="symbol-config-card">
        <v-card-title>焊口符号研究配置</v-card-title>
        <v-card-text>
          <template v-if="detectionMode === 'placement'">
            <div class="section-label">落图模式 / 黑色焊口符号</div>
            <v-switch v-model="weldSymbolConfig.placementSymbols.blackCircleEnabled" color="secondary" hide-details label="检测主管上的黑色圆"><v-tooltip activator="parent">按闭合路径的圆度识别贝塞尔圆或高圆度密集折线路径</v-tooltip></v-switch>
            <v-switch v-model="weldSymbolConfig.placementSymbols.approximateCircleEnabled" color="warning" hide-details label="允许低边数近似圆（补充规则，默认关闭）"><v-tooltip activator="parent">开启后才接受六边形等低边数近似圆及非圆形复合恢复候选，可能增加误识别</v-tooltip></v-switch>
            <v-switch v-model="weldSymbolConfig.placementSymbols.plainCircleEnabled" color="secondary" hide-details label="普通黑圆焊口"><v-tooltip activator="parent">没有附加 X 或方括号的普通焊口</v-tooltip></v-switch>
            <v-switch v-model="weldSymbolConfig.placementSymbols.prefabricatedXEnabled" color="secondary" hide-details label="黑圆 + X（预制焊口）"><v-tooltip activator="parent">圆内或圆上的两条交叉短线表示预制符号</v-tooltip></v-switch>
            <v-switch v-model="weldSymbolConfig.placementSymbols.socketThreadBracketEnabled" color="secondary" hide-details label="黑圆 + 方括号（承插焊/螺纹焊）"><v-tooltip activator="parent">圆附近成对方括号表示承插焊或螺纹焊</v-tooltip></v-switch>
            <v-switch v-model="weldSymbolConfig.placementSymbols.mainPipeOnly" color="secondary" hide-details label="必须位于主管线上"><v-tooltip activator="parent">默认开启，用强过程线投影排除图签、文字和材料表中的圆</v-tooltip></v-switch>
            <div class="parameter-grid mt-3">
              <v-text-field v-model.number="weldSymbolConfig.placementSymbols.minimumDiameter" type="number" min="0.4" step="0.1" label="最小圆直径" />
              <v-text-field v-model.number="weldSymbolConfig.placementSymbols.maximumDiameter" type="number" min="1" step="0.5" label="最大圆直径" />
              <v-text-field v-model.number="weldSymbolConfig.placementSymbols.mainPipeTolerance" type="number" min="0.2" step="0.1" label="主管投影容差" />
              <v-text-field v-model.number="weldSymbolConfig.placementSymbols.darkThreshold" type="number" min="0" max="1" step="0.05" label="黑色阈值" />
            </div>
            <v-switch v-model="weldSymbolConfig.placementSymbols.includeResearchFallback" color="warning" hide-details label="允许研究候选回退（默认关闭）"><v-tooltip activator="parent">开启后，未呈现圆形的研究包泛化候选也可进入结果；可能增加误识别</v-tooltip></v-switch>
            <v-divider class="my-4" />
            <div class="section-label">研究候选辅助配置</div>
            <v-select v-model="weldSymbolConfig.fixedSymbolPolicy" :items="symbolPolicyOptions" label="固定焊口符号策略" />
            <v-select v-model="weldSymbolConfig.markerPolicy" :items="markerPolicyOptions" label="填充标记策略" class="mt-3" />
            <v-slider v-model="weldSymbolConfig.minimumConfidence" min="0" max="1" step="0.05" color="secondary" label="最低候选置信度" thumb-label class="mt-3" />
            <v-alert type="info" variant="tonal" density="compact" class="mt-3">默认硬门禁是主管上的黑色圆形闭合路径。PDF 没有原生圆指令，因此贝塞尔圆和通过严格圆拟合的密集折线路径都按圆处理；六边形等低边数近似圆默认关闭。</v-alert>
          </template>
          <template v-else>
            <div class="section-label">对照模式 / 红色人工编号框</div>
            <v-switch v-model="weldSymbolConfig.comparisonSymbols.redFrameEnabled" color="secondary" hide-details label="识别红色编号框和红色引线"><v-tooltip activator="parent">使用 PDF 图元研究原有红色人工标注提取逻辑</v-tooltip></v-switch>
            <v-text-field v-model="weldSymbolConfig.comparisonSymbols.redLabelPattern" class="mt-3" label="红框编号表达式" hint="默认支持 F、FS、RP、S，例如 F1、FS20" persistent-hint><v-tooltip activator="parent">正则表达式；可按项目改变允许的编号前缀</v-tooltip></v-text-field>
            <v-slider v-model="weldSymbolConfig.comparisonSymbols.minimumConfidence" min="0" max="1" step="0.05" color="secondary" label="红框最低置信度" thumb-label class="mt-4" />
            <v-alert type="warning" variant="tonal" density="compact" class="mt-3">只有“红色编号文字 + 相连红色引线”同时成立才接受；焊点取引线远离编号框的一端。</v-alert>
          </template>
        </v-card-text>
        <v-card-actions><v-spacer /><v-btn color="primary" @click="symbolConfigDialog = false">应用配置<v-tooltip activator="parent">关闭弹窗；下次图元研究将使用当前配置</v-tooltip></v-btn></v-card-actions>
      </v-card>
    </v-dialog>
  </v-app>
</template>
