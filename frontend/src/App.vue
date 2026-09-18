<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from './api'
import { CANVAS_PERFORMANCE_PROFILES, selectCanvasPerformanceProfile } from './canvasPerformance'
import { encodeCsv } from './csv'
import { enforceFolderFileLimit, filesFromFolderDrop } from './folderDrop'
import { createLatestMessageChannel } from './messageChannel'
import { classifyRecoveryPages } from './recoveryReconciliation'
import {
  ERROR_MESSAGES_KEY,
  OPERATION_MESSAGES_KEY,
  loadMessagePreferences,
  saveMessagePreference,
} from './messagePreferences'
import { referenceFileSlot, resolveReferenceFileIndex } from './referenceIdentity'
import { sameReferenceHint } from './referenceHintIdentity'
import { deleteDraft, listDrafts, loadDraft, loadDraftPage, loadStoredFile, saveDraftPage } from './workspaceStorage'
import { materializeWorkspaceFile, restoreWorkspaceFile } from './workspaceFile'
import { tutorialCatalog, tutorialStepsById } from './tutorialCatalog'
import TutorialTour from './components/TutorialTour.vue'
import TutorialCatalogDialog from './components/TutorialCatalogDialog.vue'
import AppHeader from './components/AppHeader.vue'
import AssistantPanel from './components/AssistantPanel.vue'
import AnalysisProgressPanel from './components/AnalysisProgressPanel.vue'
import AnalysisQueueDialog from './components/AnalysisQueueDialog.vue'
import AnalysisConfirmationDialogs from './components/AnalysisConfirmationDialogs.vue'
import DraftManagerDialogs from './components/DraftManagerDialogs.vue'
import SystemSettingsDialog from './components/SystemSettingsDialog.vue'
import SymbolConfigDialog from './components/SymbolConfigDialog.vue'
import { calculateAnalysisProgress } from './analysisProgress'
import { ANALYSIS_POLL_INTERVAL_MS } from './polling'
import { useAnalysisQueue } from './composables/useAnalysisQueue'
import { useAnalysisWorkflow } from './composables/useAnalysisWorkflow'
import { useCanvasViewport } from './composables/useCanvasViewport'
import { useCanvasRenderer } from './composables/useCanvasRenderer'
import { useDraftManager } from './composables/useDraftManager'
import { matchesShortcut, useShortcutSettings } from './composables/useShortcutSettings'
import { useServiceHealth } from './composables/useServiceHealth'
import { useUploadCloseGuard } from './composables/useUploadCloseGuard'
import { useWorkspaceHistory } from './composables/useWorkspaceHistory'
import { useMarkerPresentation } from './composables/useMarkerPresentation'
import { useRegionDeletion } from './composables/useRegionDeletion.js'
import { useRecoveryConflictDecision } from './composables/useRecoveryConflictDecision.js'
import { useThemeSettings } from './composables/useThemeSettings.js'
import { useMarkerEditor } from './composables/useMarkerEditor'
import { useReferenceWorkspace } from './composables/useReferenceWorkspace'
import { useDetachedReferenceWindow } from './composables/useDetachedReferenceWindow'
import { normalizeFiles, useProjectWorkspace } from './composables/useProjectWorkspace'
import { useWorkspacePersistence } from './composables/useWorkspacePersistence'
import { usePageNavigation } from './composables/usePageNavigation'
import { useAuthSession } from './composables/useAuthSession'
import { browserClientInstanceId, usePageCollaboration } from './composables/usePageCollaboration'
import { isDesignComponent, isSpecialMarker } from './numbering'
import {
  loadAutoReferenceWindow,
  loadReferenceHintLocation,
  saveAutoReferenceWindow,
  saveReferenceHintLocation,
  shouldShowHintsInReferenceWindow,
} from './referenceWindowPreference'
import { createDefaultMarkerAppearance } from './defaultMarkerAppearance'
import { saveAndCloseWorkspace } from './saveAndCloseWorkspace'
import { DEFAULT_MANUAL_LEADER_LENGTH, normalizeManualLeaderLength } from './manualMarkerPlacement'
import { applyLeaderDefault, applyMarkerTypeDefault, createTaskMarkerSettings, restoreTaskCreationSettings } from './markerSettingsScope'
import {
  createManualNumberingSettings,
  loadManualNumberingSettings,
  saveManualNumberingSettings,
} from './manualNumberingSettings'
import { loadProjectProfiles, createRecognitionRules } from './projectProfiles'
import { createSystemSettingsDefaults } from './systemSettingsDefaults'
import { workspaceLayoutForWidth } from './workspaceLayout'

const { preference: themePreference, options: themeOptions } = useThemeSettings()
let pageCollaboration = null
const FOLDER_FILE_LIMIT = 1000
const clientInstanceId = browserClientInstanceId()
const auth = useAuthSession({ client: api })
const router = useRouter()
const leaderLineElements = new Map()
const detectedCanvasPerformanceProfile = selectCanvasPerformanceProfile({
  deviceMemory: navigator.deviceMemory,
  hardwareConcurrency: navigator.hardwareConcurrency,
  maxTouchPoints: navigator.maxTouchPoints,
  viewportWidth: window.innerWidth,
})
const savedCanvasPerformanceMode = localStorage.getItem('weld-marker.canvas-performance') || 'auto'
const canvasPerformanceMode = ref(['auto', ...Object.keys(CANVAS_PERFORMANCE_PROFILES)].includes(savedCanvasPerformanceMode) ? savedCanvasPerformanceMode : 'auto')
const canvasPerformanceProfile = computed(() => canvasPerformanceMode.value === 'auto'
  ? detectedCanvasPerformanceProfile
  : CANVAS_PERFORMANCE_PROFILES[canvasPerformanceMode.value] || detectedCanvasPerformanceProfile)
const canvasPerformanceOptions = [
  { title: `自动检测（当前：${detectedCanvasPerformanceProfile.label}）`, value: 'auto' },
  ...Object.values(CANVAS_PERFORMANCE_PROFILES).map(profile => ({ title: profile.label, value: profile.key }))
]

const projectMode = ref('single')
const projectPdfs = ref([])
const projectResults = ref([])
const activeProjectIndex = ref(0)
const targetPdf = ref(null)
const targetDocument = shallowRef(null)
const pcfFolderOptions = ref([])
const selectedPcfFolder = ref(null)
const startPage = ref(1)
const endPage = ref('')
const startNumber = ref(1)
const numberPrefix = ref('')
const numberSuffix = ref('')
const useReferenceNumber = ref(true)
const progressPanelOpen = ref(true)
const targetPreparation = ref({ active: false, completed: 0, total: 4, message: '' })
const targetPreparationPercent = computed(() => {
  const total = Math.max(1, Number(targetPreparation.value.total) || 1)
  return Math.max(0, Math.min(100, (Number(targetPreparation.value.completed) || 0) / total * 100))
})
const leftDrawerOpen = ref(true)
const leftPanelTab = ref('input')
const reviewDrawerOpen = ref(true)
const systemSettingsDialog = ref(false)
const tutorialOpen = ref(false)
const tutorialCatalogOpen = ref(false)
const activeTutorialId = ref('overview')
const tutorialLoadingId = ref('')
const activeTutorial = computed(() => tutorialCatalog.find(item => item.id === activeTutorialId.value) || tutorialCatalog[0])
const activeTutorialSteps = computed(() => tutorialStepsById[activeTutorialId.value] || tutorialStepsById.overview)
const assistantOpen = ref(false)
const tutorialSampleLoading = ref(false)
const tutorialSessionActive = ref(false)
const tutorialStorageKey = 'weld-marker.tutorial.seen.v2'

function openTutorial() {
  shortcutMenu.value = false
  systemSettingsDialog.value = false
  tutorialOpen.value = false
  tutorialCatalogOpen.value = true
}

async function startTutorial(tutorial) {
  if (!tutorial?.id || tutorialLoadingId.value) return
  activeTutorialId.value = tutorial.id
  tutorialLoadingId.value = tutorial.id
  leftDrawerOpen.value = true
  leftPanelTab.value = 'input'
  try {
    if (tutorial.requiresSample && !tutorialSessionActive.value && !tutorialSampleLoading.value) await loadTutorialSample()
    tutorialCatalogOpen.value = false
    tutorialOpen.value = true
  } finally {
    tutorialLoadingId.value = ''
  }
}

function handleTutorialStepChange({ step } = {}) {
  leftDrawerOpen.value = true
  reviewDrawerOpen.value = true
  if (step?.ensureSelection && !selectedCandidate.value) selectedId.value = candidates.value.find(item => item.included !== false)?.id || ''
  if (step?.leftPanel === 'input') leftPanelTab.value = 'input'
  if (step?.leftPanel === 'reference' && result.value?.ep3dReferenceInventory?.documents?.length) {
    void syncReferenceForPage(currentPage.value, false)
    leftPanelTab.value = referenceHintsInDetachedWindow.value ? 'input' : 'reference'
  }
}

function finishTutorial() {
  try { localStorage.setItem(tutorialStorageKey, 'true') } catch { /* localStorage may be unavailable */ }
}

function updateTutorialCatalogOpen(open) {
  tutorialCatalogOpen.value = open
  if (!open) finishTutorial()
}
const error = ref('')
const notice = ref('')
const errorOpen = ref(false)
const noticeOpen = ref(false)
const messagePreferences = loadMessagePreferences(localStorage)
const operationMessagesEnabled = ref(messagePreferences.operationMessagesEnabled)
const errorMessagesEnabled = ref(messagePreferences.errorMessagesEnabled)
const clearReferencesOnDesignUpload = ref(localStorage.getItem('weld-marker.clear-references-on-design-upload') !== 'false')
const autoReferenceWindow = ref(loadAutoReferenceWindow())
const referenceHintLocation = ref(loadReferenceHintLocation())
const noticeChannel = createLatestMessageChannel(value => {
  if (value) {
    notice.value = value
    noticeOpen.value = true
  } else {
    noticeOpen.value = false
  }
}, { interval: 320 })
function showNotice(message) {
  if (operationMessagesEnabled.value) noticeChannel.publish(message)
}
function clearNotice() { noticeChannel.clear() }
function dismissError() { errorOpen.value = false }
function finishErrorLeave() { if (!errorOpen.value) error.value = '' }
function finishNoticeLeave() {
  if (!noticeOpen.value) {
    notice.value = ''
    noticeChannel.clear()
  }
}
const unsavedReviewChanges = ref(false)
const uncertainReviewSave = ref(false)
const uploadCommitPending = useUploadCloseGuard({ dirty: unsavedReviewChanges, uncertain: uncertainReviewSave })
let analysisQueueTimer
const result = ref(null)
const {
  dialog: analysisQueueDialog,
  loading: analysisQueueLoading,
  actionId: analysisQueueActionId,
  cancellingJobIds: analysisQueueCancellingJobIds,
  tab: analysisQueueTab,
  deleteTarget: analysisQueueDeleteTarget,
  deleteDialog: analysisQueueDeleteDialog,
  queue: analysisQueue,
  visibleJobs: visibleAnalysisQueueJobs,
  refresh: refreshAnalysisQueue,
  open: openAnalysisQueue,
  changePage: changeAnalysisQueuePage,
  selectTab: selectAnalysisQueueTab,
  cancel: cancelQueueJob,
  move: moveQueueJob,
  archive: setQueueJobArchived,
  requestDelete: requestDeleteQueueJob,
  confirmDelete: confirmDeleteQueueJob,
} = useAnalysisQueue({ currentResult: result, error, showNotice })
const {
  backend: health,
  mineru: mineruHealth,
  mineruMessage: mineruStatusText,
  refresh: refreshServiceHealth,
  start: startServiceHealthPolling,
  stop: stopServiceHealthPolling,
} = useServiceHealth({
  client: api,
  afterRefresh: () => analysisQueueDialog.value ? refreshAnalysisQueue(false) : undefined,
})
const currentPage = ref(1)
const previewPage = ref(1)
const selectedId = ref('')
const editingId = ref('')
const editingValue = ref('')
const manualAddMode = ref(false)
const manualAddType = ref('weld')
const hydratingWorkspace = ref(false)
const activeFileFingerprint = ref('')
const pendingDraft = ref(null)
const recoveryOptions = ref([])
const selectedRecoveryKey = ref('')
const draftRecoveryDialog = ref(false)
const recoveryConflictDecision = useRecoveryConflictDecision()
const {
  dialog: draftManagerDialog,
  loading: draftManagerLoading,
  entries: draftEntries,
  deleteTarget: draftDeleteTarget,
  selectedKeys: selectedDraftKeys,
  batchDeleteDialog: draftBatchDeleteDialog,
  batchDeleting: draftBatchDeleting,
  selectionMode: draftSelectionMode,
  allSelected: allDraftsSelected,
  open: openDraftManager,
  load: loadManagedDraft,
  removeTarget: deleteManagedDraft,
  toggleAll: toggleAllDrafts,
  toggleSelectionMode: toggleDraftSelectionMode,
  toggleSelection: toggleDraftSelection,
  removeSelected: deleteSelectedDrafts,
} = useDraftManager({
  storage: { list: listDrafts, load: loadDraft, remove: deleteDraft },
  error,
  showNotice,
  beforeOpen: () => { systemSettingsDialog.value = false },
})
const saveAndCloseRunning = ref(false)
const anchorDragState = ref(null)
const groupDragState = ref(null)
const canvasPointerPosition = ref(null)
const canvasViewport = ref(null)
const canvasSurface = ref(null)
const pdfCanvas = ref(null)
const displayEmbeddedReference = ref(true)
const pcfSelector = ref(null)
const pcfSelectorWidth = ref(0)
const appViewport = ref({ width: window.innerWidth, height: window.innerHeight })
const workspaceLayout = computed(() => workspaceLayoutForWidth(appViewport.value.width))
const leftDrawerWidth = computed(() => workspaceLayout.value.leftDrawerWidth)
const reviewDrawerWidth = computed(() => workspaceLayout.value.reviewDrawerWidth)
const appHeaderHeight = computed(() => Math.round(appViewport.value.height * 0.06))
const labelDragState = shallowRef(null)
const canvasLayout = ref({ width: 1, height: 1, target: { x: 0, y: 34, width: 1, height: 1 }, reference: null })
const {
  zoom,
  pan,
  panState: canvasPanState,
  surfaceStyle: canvasSurfaceStyle,
  fit: fitCanvas,
  zoomBy,
  onWheel: onCanvasWheel,
  startPan: startCanvasPan,
  movePan: moveCanvasPan,
  endPan: endCanvasPan,
} = useCanvasViewport({
  canvasLayout,
  canvasViewport,
  onResolutionChange: scheduleResolutionRender,
  onPointerMove(event) {
    canvasPointerPosition.value = { clientX: event.clientX, clientY: event.clientY }
  },
})
const {
  mode: referenceMode,
  projectMode: referenceProjectMode,
  files: referencePdfs,
  unavailableFiles: unavailableReferenceFiles,
  activeIndex: activeReferenceIndex,
  researchReady: referenceResearchReady,
  activeFile: referencePdf,
  document: referenceDocument,
  activePage: activeReferencePage,
  pcfFiles,
  focus: referenceFocus,
  showAll: referenceShowAll,
  archiving: archivingWorkspace,
  changeMode: changeReferenceMode,
  changeProjectMode: changeReferenceProjectMode,
  setFiles: setReferenceFiles,
  setFolderFiles: setReferenceFolderFiles,
  setPcfFiles,
  setPcfFolderFiles,
  select: selectReference,
  jumpToHint: jumpToReferenceHint,
  toggleAllHints: showAllReferenceHints,
  matched: matchedReference,
  syncForPage: syncReferenceForPage,
  materialize: materializeReferenceFile,
  archiveLazyFiles: archiveLazyReferencesForDraft,
  loadPdf: loadReferencePdf,
  clearDisplay: clearReferenceDisplay,
  clearFiles: clearReferenceFiles,
  cancelArchiving: cancelReferenceArchiving,
  dispose: disposeReferenceWorkspace,
} = useReferenceWorkspace({
  projectResults,
  result,
  currentPage,
  selectedPcfFolder,
  error,
  operations: {
    normalizeFiles,
    showNotice,
    materializeStoredFile: materializeStoredWorkspaceFile,
    invalidateCanvas: () => canvasRenderer.invalidate(),
    clearCanvasCache: () => canvasRenderer.clearCache(),
    renderCanvas: (...args) => renderUnifiedCanvas(...args),
    getHintDocument: () => referenceHintDocument.value,
    getHintPage: () => referenceHintPage.value,
    canvasLayout,
    canvasViewport,
    zoom,
    pan,
    scheduleResolutionRender,
    fitCanvas,
    scheduleDraftSave: scheduleWorkspaceDraftSave,
    archiveWorkspacePage: (projectIndex, page) => saveDraftPage(activeFileFingerprint.value, projectIndex, page),
    shouldRenderReference: () => displayEmbeddedReference.value,
    locateReferenceHint: focus => referenceWindow.locateHint(focus),
  },
})
const defaultMarkerAppearance = createDefaultMarkerAppearance()
const defaultMarkerStyle = ref(cloneValue(defaultMarkerAppearance.weld))
const defaultComponentMarkerStyles = ref(cloneValue(defaultMarkerAppearance.components))
const markerStyle = ref(cloneValue(defaultMarkerAppearance.weld))
const componentMarkerStyles = ref(cloneValue(defaultMarkerAppearance.components))
const markerAppearanceTab = ref('weld')
const settingsAppearanceTab = ref('weld')
const symbolConfigDialog = ref(false)
const shortcutMenu = ref(false)
const swapSourceId = ref('')
const {
  settings: shortcutSettings,
  editors: shortcutEditors,
  conflict: shortcutConflict,
  rows: shortcutSettingRows,
  modifierOptions: shortcutModifierOptions,
  updateModifier: updateShortcutModifier,
  restoreDefaults: restoreDefaultShortcuts,
  captureKey: captureShortcutKey,
} = useShortcutSettings({ onDefaultsRestored: () => showNotice('快捷键已恢复为系统默认值。') })
const tooltipsEnabled = ref(localStorage.getItem('weld-marker.tooltips-enabled') === 'true')
const pageButtonsPerGroup = ref(Math.max(1, Math.min(20, Number.parseInt(localStorage.getItem('weld-marker.page-buttons-per-group') || '5', 10) || 5)))
const storedManualLeaderLength = normalizeManualLeaderLength(localStorage.getItem('weld-marker.manual-leader-length') || DEFAULT_MANUAL_LEADER_LENGTH)
const defaultManualLeaderLength = ref(storedManualLeaderLength)
const manualLeaderLength = ref(storedManualLeaderLength)
const defaultManualNumberingSettings = ref(createManualNumberingSettings())
const manualNumberingSettings = ref(createManualNumberingSettings())
const designPageInput = ref('')
const recognitionProjects = ref(loadProjectProfiles(localStorage))
const storedRecognitionProjectId = localStorage.getItem('weld-marker.recognition-project')
const activeRecognitionProjectId = ref(recognitionProjects.value.some(project => project.id === storedRecognitionProjectId) ? storedRecognitionProjectId : recognitionProjects.value[0].id)
const activeRecognitionProject = computed(() => recognitionProjects.value.find(project => project.id === activeRecognitionProjectId.value) || recognitionProjects.value[0])
const weldSymbolConfig = ref(createRecognitionRules({ ...activeRecognitionProject.value.recognitionRules, referenceRuleAssignments: {} }))

function switchRecognitionProject(projectId, announce = true) {
  const project = recognitionProjects.value.find(item => item.id === projectId)
  if (!project) return false
  activeRecognitionProjectId.value = project.id
  weldSymbolConfig.value = createRecognitionRules({ ...project.recognitionRules, referenceRuleAssignments: {} })
  localStorage.setItem('weld-marker.recognition-project', project.id)
  if (announce) showNotice(`已切换到${project.name}，后续解析将使用该项目的识别规则。`)
  return true
}

function switchReferenceRule(ruleId) {
  if (weldSymbolConfig.value.referenceRules.some(rule => rule.id === ruleId)) {
    weldSymbolConfig.value.activeReferenceRuleId = ruleId
  }
}
function assignReferenceRule({ slot, ruleId }) {
  if (!ruleId) delete weldSymbolConfig.value.referenceRuleAssignments[slot]
  else if (weldSymbolConfig.value.referenceRules.some(rule => rule.id === ruleId)) {
    weldSymbolConfig.value.referenceRuleAssignments[slot] = ruleId
  }
}
function restoreRecognitionSettings(payload) {
  const project = payload?.result?.project || payload?.project
  const rules = payload?.symbolConfig || payload?.result?.symbolConfig || project?.recognitionRules
  const id = payload?.recognitionProjectId || project?.id
  if (id && !recognitionProjects.value.some(item => item.id === id) && project?.name) {
    recognitionProjects.value.push({ id, name: project.name, description: project.description || '', recognitionRules: createRecognitionRules(rules || {}) })
  }
  switchRecognitionProject(id, false)
  if (rules) weldSymbolConfig.value = createRecognitionRules(rules)
}

const pages = computed(() => result.value?.pages || [])
const pageData = computed(() => pages.value.find(item => item.page === currentPage.value) || pages.value[0] || null)
const candidates = computed(() => pages.value.flatMap(page => page.candidates || []))
const activeCandidates = computed(() => candidates.value.filter(item => item.included !== false))
const selectedCandidate = computed(() => candidates.value.find(item => item.id === selectedId.value) || null)
const assistantContext = computed(() => ({
  recognitionProject: activeRecognitionProject.value.name,
  jobLoaded: Boolean(result.value),
  currentPage: currentPage.value,
  totalPages: pages.value.length,
  projectMode: projectMode.value,
  referenceMode: referenceMode.value,
  editMode: manualAddMode.value ? manualAddType.value : '',
  markerCount: pages.value.reduce((total, page) => total + (Number(page.candidateCount) || (page.candidates || []).length), 0),
  selectedMarkerType: selectedCandidate.value
    ? (selectedCandidate.value.componentType || 'weld')
    : '',
  jobStatus: result.value?.status || '',
}))
const {
  undoStack,
  redoStack,
  pending: pendingHistory,
  applying: applyingHistory,
  canUndo,
  canRedo,
  begin: beginHistory,
  commit: commitHistory,
  undo,
  redo,
  clear: clearHistory,
  clearPage: clearPageHistory,
} = useWorkspaceHistory({
  result,
  pages,
  pageData,
  markerStyle,
  componentMarkerStyles,
  projectResults,
  activeProjectIndex,
  selectedId,
  cloneValue,
  scheduleSave: scheduleDraftSave,
  canEdit: () => Boolean(result.value?.jobId === 'tutorial-000207' || (
    pageCollaboration?.activeLock.value?.jobId === result.value?.jobId
    && pageCollaboration?.activeLock.value?.page === Number(pageData.value?.page)
    && !pageCollaboration?.readOnly.value
    && pageCollaboration?.saveState.value !== 'saving'
    && !pageCollaboration?.synchronizing.value
  )),
  showNotice,
  logAudit,
})
const {
  candidateMarkerStyle,
  markerClass,
  markerDimensions,
  reflowCurrentPageLabelPositions: calculateCurrentPageLabelPositions,
  labelPosition,
  anchorPoint,
  leaderEnd,
  groupHandlePosition,
  leaderStyle,
} = useMarkerPresentation({
  markerStyle,
  componentMarkerStyles,
  selectedId,
  editingId,
  swapSourceId,
  labelDragState,
  canvasLayout,
  pages,
  pageData,
})
const markerEditor = useMarkerEditor({
  state: {
    result, pageData, pages, candidates, selectedCandidate,
    targetDocument, currentPage, previewPage, projectResults, activeProjectIndex, manualAddMode,
    manualAddType, canvasSurface, canvasLayout, canvasPointerPosition, referenceFocus, referenceShowAll,
    startNumber, numberPrefix, numberSuffix, useReferenceNumber, selectedId, swapSourceId,
    editingId, editingValue,
    pendingHistory, labelDragState, anchorDragState, groupDragState, manualLeaderLength, manualNumberingSettings, error,
  },
  operations: {
    cloneValue, beginHistory, commitHistory,
    reflowCurrentPageLabelPositions: calculateCurrentPageLabelPositions,
    leaderEnd, logAudit, showNotice,
    selectMarkerAppearanceTab: type => { markerAppearanceTab.value = type },
    getLeaderLine: id => leaderLineElements.get(id) || null,
    setLeaderLine(id, element) {
      if (element) leaderLineElements.set(id, element)
      else leaderLineElements.delete(id)
    },
  },
})
const {
  ensureManualWorkspace, toggleManualAddMode, mergeStagedManualCandidates, onCanvasClick,
  addManualAtClientPoint, candidateMatchesModificationType, copySelectedCandidate, pasteCopiedAtClientPoint,
  formattedWeldNumber, numberResult,
  pageMatchSummary, swapSelectedWeldNumbers, selectCandidate,
  reflowCurrentPageLabelPositions, beginInlineEdit, commitInlineEdit, cancelInlineEdit,
  onApplicationPointerDown, startLabelDrag, moveLabel, endLabelDrag, setLeaderLineElement,
  startAnchorDrag, moveAnchor, endAnchorDrag, startGroupDrag, moveGroup, endGroupDrag,
  toggleCandidate, deleteSelectedWeld,
} = markerEditor

const {
  loading,
  enqueueing,
  enqueueProgress,
  referenceInputMissing,
  progress: analysisProgress,
  activeJobId: activeAnalysisJobId,
  cancelling: cancellingAnalysis,
  duplicateDialog: duplicateJobDialog,
  duplicateInfo: duplicateJobInfo,
  enqueue: enqueueForReview,
  analyze,
  cancel: cancelActiveAnalysis,
  resolveDuplicate: resolveDuplicateJob,
  dispose: disposeAnalysisWorkflow,
} = useAnalysisWorkflow({
  workspace: {
    targetPdf, projectPdfs, projectResults, activeProjectIndex, projectMode,
    startPage, endPage, referenceMode, referencePdfs, pcfFiles, selectedPcfFolder,
    weldSymbolConfig, activeRecognitionProject, useReferenceNumber, startNumber, numberPrefix,
    numberSuffix, result, referenceResearchReady, currentPage, selectedId, swapSourceId,
    undoStack, redoStack, uploadCommitPending, error,
  },
  operations: {
    cloneValue,
    dismissError,
    clearNotice,
    showNotice,
    refreshQueue: refreshAnalysisQueue,
    materializeReferenceFile,
    clearReferenceDisplay,
    mergeStagedManualCandidates,
    numberResult,
    currentMarkerAppearancePayload,
    scheduleDraftSave: scheduleWorkspaceDraftSave,
    logAudit,
    renderCurrentPage: () => referenceMode.value === 'pdf'
      ? syncReferenceForPage(currentPage.value, true)
      : renderUnifiedCanvas(true),
  },
})

const selectedPcfFolderInfo = computed(() => pcfFolderOptions.value.find(item => item.value === selectedPcfFolder.value) || null)
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
async function reconcileRecovery(recovery) {
  const pageSummaries = recovery?.payload?.result?.pages || []
  const jobId = recovery?.payload?.result?.jobId
  if (!jobId || jobId === 'tutorial-000207') {
    return {
      choice: 'draft', unchangedPages: [], safePages: pageSummaries.map(page => Number(page.page)),
      conflictPages: [], unavailablePages: [], serverByPage: new Map(),
      localByPage: new Map(pageSummaries.map(page => [Number(page.page), page])),
    }
  }
  const activeIndex = Math.max(0, Number(recovery?.payload?.activeProjectIndex) || 0)
  const localByPage = new Map()
  const unchangedPages = []
  const safePages = []
  const conflictPages = []
  const unavailablePages = []
  const serverByPage = new Map()
  for (const summary of pageSummaries) {
    const pageNumber = Number(summary.page)
    const localPage = recovery?.source === 'indexeddb'
      ? await loadDraftPage(activeFileFingerprint.value, activeIndex, pageNumber).catch(() => null)
      : summary
    if (!localPage) {
      unchangedPages.push(pageNumber)
      continue
    }
    localByPage.set(pageNumber, localPage)
    let serverPage = null
    try {
      const response = await api.getJobPage(jobId, pageNumber)
      serverPage = response.page || response
      serverByPage.set(pageNumber, serverPage)
    } catch { /* The archived local page remains recoverable while the server is unavailable. */ }
    const pageComparison = classifyRecoveryPages([localPage], serverPage ? [serverPage] : [])
    unchangedPages.push(...pageComparison.unchangedPages)
    safePages.push(...pageComparison.safePages)
    conflictPages.push(...pageComparison.conflictPages)
    unavailablePages.push(...pageComparison.unavailablePages)
  }
  const comparison = { unchangedPages, safePages, conflictPages, unavailablePages, serverByPage, localByPage }
  let choice = 'draft'
  if (comparison.conflictPages.length || comparison.unavailablePages.length) {
    choice = await recoveryConflictDecision.request({
      source: recovery.source,
      conflictPages: comparison.conflictPages,
      unavailablePages: comparison.unavailablePages,
    })
  }
  return { ...comparison, choice }
}
function syncPcfMenuWidth() {
  void nextTick(() => {
    const element = pcfSelector.value?.$el || pcfSelector.value
    const width = Math.round(element?.getBoundingClientRect?.().width || 0)
    if (width > 0) pcfSelectorWidth.value = width
  })
}
function clearReferenceHintSelection() {
  referenceFocus.value = null
  referenceShowAll.value = false
}
const referenceHintDocument = computed(() => {
  const documents = result.value?.ep3dReferenceInventory?.documents || []
  const selectedDocument = activeReferenceIndex.value >= 0
    ? documents.find(document => resolveReferenceFileIndex(document.file, referencePdfs.value) === activeReferenceIndex.value)
    : null
  return selectedDocument
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
const referenceDisplayMarkers = computed(() => {
  const page = referenceHintPage.value
  const layout = canvasLayout.value.reference
  if (!page || !layout) return []
  const focus = referenceFocus.value
  const rawItems = referenceShowAll.value
    ? page.items || []
    : focus && focus.page === page.page && focus.file === referenceHintDocument.value?.file ? [focus] : []
  const clusters = []
  rawItems.forEach(item => {
    const point = { x: Number(item.point?.[0]), y: Number(item.point?.[1]) }
    let cluster = clusters.find(group => Math.hypot(group.origin.x - point.x, group.origin.y - point.y) < 6)
    if (!cluster) { cluster = { origin: point, items: [] }; clusters.push(cluster) }
    cluster.items.push({ item, point })
  })
  const colors = { weld: '#d4143c', valve: '#1769d2', flange: '#1769d2', support: '#1769d2' }
  return clusters.flatMap(cluster => cluster.items.map(({ item, point }, index) => {
    const spread = cluster.items.length > 1 ? 18 : 0
    const angle = cluster.items.length > 1 ? -Math.PI / 2 + index * Math.PI * 2 / cluster.items.length : 0
    const offsetX = Math.cos(angle) * spread
    const offsetY = Math.sin(angle) * spread
    const linkLength = Math.hypot(offsetX, offsetY)
    return {
      key: `${item.type}-${item.label}-${point.x}-${point.y}`,
      label: item.label,
      type: item.type,
      style: {
        left: `${layout.x + point.x / Math.max(1, page.width) * layout.width + offsetX}px`,
        top: `${layout.y + point.y / Math.max(1, page.height) * layout.height + offsetY}px`,
        '--reference-marker-color': colors[item.type] || '#ff8a3d'
      },
      linkStyle: linkLength ? {
        width: `${linkLength}px`,
        transform: `rotate(${Math.atan2(-offsetY, -offsetX)}rad)`
      } : { display: 'none' }
    }
  }))
})
const projectProgress = computed(() => projectResults.value.filter(item => item && item.status !== 'processing').length)

watch(() => result.value?.ep3dReferenceInventory, inventory => {
  if (targetPdf.value && inventory?.documents?.length && !tutorialOpen.value && !tutorialSampleLoading.value) {
    if (!(referenceDetached.value && referenceHintLocation.value === 'reference')) leftPanelTab.value = 'reference'
  }
})
const analysisProgressPercent = computed(() => calculateAnalysisProgress(analysisProgress.value))
const {
  shownPage,
  targetPageCount,
  navigationPages,
  visiblePages: visibleNavigationPages,
  canShowPreviousGroup: canShowPreviousPageGroup,
  canShowNextGroup: canShowNextPageGroup,
  step: stepPage,
  stepGroup: stepPageGroup,
  jump: jumpToDesignPage,
  navigate: navigateDesignPage,
  pageClasses: navigationPageClasses,
} = usePageNavigation({
  result, pages, currentPage, previewPage, targetDocument, pageButtonsPerGroup, changePage, pageMatchSummary,
  pageLocks: computed(() => pageCollaboration?.lockSummaries.value || []),
  clientInstanceId,
})
function submitDesignPageJump() {
  if (jumpToDesignPage(designPageInput.value)) {
    designPageInput.value = ''
    return
  }
  error.value = '请输入当前设计图任务中存在的页码。'
}
const canvasRenderer = useCanvasRenderer({
  state: {
    targetDocument,
    referenceDocument,
    targetPdf,
    referencePdf,
    activeProjectIndex,
    activeReferenceIndex,
    shownPage,
    activeReferencePage,
    referencePdfs,
    pdfCanvas,
    canvasLayout,
    zoom,
    performanceProfile: canvasPerformanceProfile,
    error,
    displayReference: displayEmbeddedReference,
  },
  fitCanvas,
})
const { rendering } = canvasRenderer
let referencePublishGeneration = 0
const referenceWindow = useDetachedReferenceWindow({
  onViewerClosed() {
    displayEmbeddedReference.value = true
    if (targetDocument.value) void loadReferencePdf(referencePdf.value, true)
  },
  onError(cause) {
    error.value = `独立对照窗口通信失败：${cause?.message || cause}`
  },
})
const referenceDetached = referenceWindow.detached
const referenceHintsInDetachedWindow = computed(() => shouldShowHintsInReferenceWindow(referenceDetached.value, referenceHintLocation.value))
const referenceHintsInDesignWindow = computed(() => !referenceHintsInDetachedWindow.value)

async function publishDetachedReference() {
  if (!referenceDetached.value) return
  const generation = ++referencePublishGeneration
  const source = referencePdf.value
  if (!source) {
    referenceWindow.publish(null)
    return
  }
  try {
    const file = await materializeReferenceFile(source)
    if (generation !== referencePublishGeneration || !referenceDetached.value) return
    const pageInfo = referenceHintPage.value ? cloneValue(referenceHintPage.value) : null
    referenceWindow.publish({
      file,
      fileKey: `${referenceFileSlot(source, activeReferenceIndex.value)}:${file?.size || 0}:${file?.lastModified || 0}`,
      fileName: file?.name || source?.name || '对照图.pdf',
      page: activeReferencePage.value,
      pageInfo,
      hintDocumentFile: referenceHintDocument.value?.file || '',
      focus: referenceFocus.value ? cloneValue(referenceFocus.value) : null,
      showAll: referenceShowAll.value,
      hintsLocation: referenceHintLocation.value,
      tooltipsEnabled: tooltipsEnabled.value,
    })
  } catch (cause) {
    if (generation === referencePublishGeneration) error.value = `无法向独立窗口发送对照 PDF：${cause?.message || cause}`
  }
}

async function openReferenceWindow({ automatic = false } = {}) {
  if (referenceDetached.value) {
    referenceWindow.open()
    return true
  }
  if (!referencePdf.value) {
    if (!automatic) error.value = '当前没有可在独立窗口中显示的对照 PDF。'
    return false
  }
  if (!referenceWindow.open()) {
    const message = '浏览器阻止了独立对照窗口，请允许本站弹出窗口后使用工具栏按钮或快捷键重试。'
    if (automatic) showNotice(message)
    else error.value = message
    return false
  }
  displayEmbeddedReference.value = false
  const previousDocument = referenceDocument.value
  referenceDocument.value = null
  previousDocument?.destroy?.()
  await renderUnifiedCanvas(true)
  await publishDetachedReference()
  return true
}

function setAutoReferenceWindow(value) {
  autoReferenceWindow.value = Boolean(value)
  saveAutoReferenceWindow(localStorage, autoReferenceWindow.value)
  if (autoReferenceWindow.value) void openReferenceWindow({ automatic: true })
}

function setReferenceHintLocation(value) {
  referenceHintLocation.value = value === 'design' ? 'design' : 'reference'
  saveReferenceHintLocation(localStorage, referenceHintLocation.value)
}

function restoreEmbeddedReference() {
  referenceWindow.close()
}

watch(
  () => [
    referenceDetached.value,
    activeReferenceIndex.value,
    activeReferencePage.value,
    referencePdf.value,
    referenceFocus.value,
    referenceShowAll.value,
    referenceHintPage.value,
    referenceHintLocation.value,
    tooltipsEnabled.value,
  ],
  () => { void publishDetachedReference() },
  { deep: true },
)
watch([referencePdf, referenceResearchReady], ([file, ready]) => {
  if (autoReferenceWindow.value && file && ready && !referenceDetached.value) void openReferenceWindow({ automatic: true })
})
watch(referenceHintsInDetachedWindow, moved => {
  if (moved && leftPanelTab.value === 'reference') leftPanelTab.value = 'input'
})
const projectWorkspace = useProjectWorkspace({
  state: {
    projectMode, projectPdfs, projectResults, activeProjectIndex, targetPdf, targetDocument,
    startPage, endPage, result, activeFileFingerprint, pendingDraft, recoveryOptions,
    selectedRecoveryKey, draftRecoveryDialog, targetPreparation, referenceResearchReady,
    selectedId, swapSourceId, previewPage, currentPage, tutorialSessionActive,
    tutorialSampleLoading, referenceMode, referenceProjectMode, referencePdfs,
    activeReferenceIndex, referencePdf, unavailableReferenceFiles, pcfFiles, selectedPcfFolder,
    useReferenceNumber, startNumber, hydratingWorkspace, leftPanelTab,
    markerStyle, error,
  },
  operations: {
    cloneValue, materializeStoredFile: materializeStoredWorkspaceFile, restoreMarkerAppearances,
    restoreProjectSettings(payload) {
      restoreRecognitionSettings(payload)
      const restored = restoreTaskCreationSettings(defaultMarkerSettingsProfile(), payload)
      manualLeaderLength.value = restored.leaderLength
      manualNumberingSettings.value = restored.numbering
    },
    numberResult, currentMarkerAppearancePayload, selectRecoveryVersion,
    clearReferencesOnDesignUpload, clearReferenceFiles, clearReferenceDisplay,
    cancelReferenceArchiving, syncReferenceForPage, matchedReference, loadReferencePdf,
    archiveLazyReferences: archiveLazyReferencesForDraft, invalidateCanvas: () => canvasRenderer.invalidate(),
    renderCanvas: (...args) => renderUnifiedCanvas(...args), targetPageCount, dismissError,
    clearHistory, showNotice, logAudit, reconcileRecovery,
    markRecoveredPagesDirty: pages => workspacePersistence.markRecoveredPagesDirty(pages),
    overwriteRecoveredPages: (pages, unavailablePages) => pageCollaboration.saveAllDirtyPages({ overwritePages: pages, skipPages: unavailablePages }),
    activateRecoveredPage: () => pageCollaboration.acquireCurrentPage(),
    setActiveReferencePage: value => { activeReferencePage.value = value },
  },
})
const {
  clearProject, changeProjectMode: changeProjectModeWorkspace, setProjectFiles: setProjectFilesWorkspace, loadTutorialSample: loadTutorialSampleWorkspace,
  loadTargetPdf, restoreWorkspaceDraft: restoreWorkspaceDraftWorkspace, discardWorkspaceDraft, selectProject: selectProjectWorkspace,
  dispose: disposeProjectWorkspace,
} = projectWorkspace

async function restoreWorkspaceDraft() {
  try {
    return await restoreWorkspaceDraftWorkspace()
  } catch (cause) {
    error.value = `恢复结果保存失败，恢复内容仍保留在当前工作区：${cause?.message || cause}`
    return false
  }
}

async function leaveCurrentPageFor(action, failurePrefix) {
  try {
    await pageCollaboration?.leavePage()
    return await action()
  } catch (cause) {
    error.value = `${failurePrefix}：${cause?.message || cause}`
    return false
  }
}
function changeProjectMode(mode) { return leaveCurrentPageFor(() => changeProjectModeWorkspace(mode), '当前页保存失败，未切换模式') }
function setProjectFiles(value) {
  return leaveCurrentPageFor(() => {
    resetTaskMarkerSettings()
    return setProjectFilesWorkspace(value)
  }, '当前页保存失败，未更换图纸')
}
function loadTutorialSample() {
  return leaveCurrentPageFor(() => {
    resetTaskMarkerSettings()
    return loadTutorialSampleWorkspace()
  }, '当前页保存失败，未打开教程')
}

async function handleFolderDrop(event, setter) {
  event.preventDefault()
  event.stopPropagation()
  event.stopImmediatePropagation?.()
  try {
    await setter(await filesFromFolderDrop(event, { maxFiles: FOLDER_FILE_LIMIT }))
  } catch (cause) {
    error.value = `读取拖拽文件夹失败：${cause?.message || cause}`
  }
}

function handleProjectFolderDrop(event) { return handleFolderDrop(event, setProjectFiles) }
function handleReferenceFolderDrop(event) { return handleFolderDrop(event, setReferenceFolderFiles) }

async function handleFolderSelection(value, setter) {
  try {
    return await setter(enforceFolderFileLimit(normalizeFiles(value), FOLDER_FILE_LIMIT))
  } catch (cause) {
    error.value = `读取文件夹失败：${cause?.message || cause}`
    return false
  }
}

function handleProjectFolderSelection(value) { return handleFolderSelection(value, setProjectFiles) }
function handleReferenceFolderSelection(value) { return handleFolderSelection(value, setReferenceFolderFiles) }

async function selectProject(index) {
  try {
    await pageCollaboration?.leavePage()
    return await selectProjectWorkspace(index)
  } catch (cause) {
    error.value = `当前页保存失败，未切换图纸：${cause?.message || cause}`
    return false
  }
}
const shapeOptions = [
  { title: '方形框', value: 'rectangle' },
  { title: '圆形（默认）', value: 'circle' },
  { title: '菱形', value: 'diamond' },
]
const markerAppearanceGroups = computed(() => [
  { key: 'weld', title: '焊口标识', style: markerStyle.value, numbering: manualNumberingSettings.value.weld },
  { key: 'valve', title: '阀门标识', style: componentMarkerStyles.value.valve, numbering: manualNumberingSettings.value.valve },
  { key: 'flange', title: '法兰标识', style: componentMarkerStyles.value.flange, numbering: manualNumberingSettings.value.flange },
  { key: 'support', title: '支架标识', style: componentMarkerStyles.value.support, numbering: manualNumberingSettings.value.support }
])
const defaultMarkerAppearanceGroups = computed(() => [
  { key: 'weld', title: '焊口标识', subtitle: '默认红色圆形框', style: defaultMarkerStyle.value, numbering: defaultManualNumberingSettings.value.weld },
  { key: 'valve', title: '阀门标识', subtitle: '默认编号前缀 V', style: defaultComponentMarkerStyles.value.valve, numbering: defaultManualNumberingSettings.value.valve },
  { key: 'flange', title: '法兰标识', subtitle: '默认编号前缀 FL', style: defaultComponentMarkerStyles.value.flange, numbering: defaultManualNumberingSettings.value.flange },
  { key: 'support', title: '支架标识', subtitle: '默认编号前缀 SP', style: defaultComponentMarkerStyles.value.support, numbering: defaultManualNumberingSettings.value.support }
])
const activeMarkerAppearanceGroup = computed(() => markerAppearanceGroups.value.find(group => group.key === markerAppearanceTab.value) || markerAppearanceGroups.value[0])
const activeSettingsAppearanceGroup = computed(() => defaultMarkerAppearanceGroups.value.find(group => group.key === settingsAppearanceTab.value) || defaultMarkerAppearanceGroups.value[0])
function normalizeManualNumberingInput() {
  manualNumberingSettings.value = createManualNumberingSettings(manualNumberingSettings.value)
}
function taskMarkerSettingsProfile() {
  return createTaskMarkerSettings({
    appearance: { weld: markerStyle.value, components: componentMarkerStyles.value },
    numbering: manualNumberingSettings.value,
    leaderLength: manualLeaderLength.value,
  })
}
function defaultMarkerSettingsProfile() {
  return createTaskMarkerSettings({
    appearance: { weld: defaultMarkerStyle.value, components: defaultComponentMarkerStyles.value },
    numbering: defaultManualNumberingSettings.value,
    leaderLength: defaultManualLeaderLength.value,
  })
}
function assignTaskMarkerSettings(profile) {
  const next = createTaskMarkerSettings(profile)
  markerStyle.value = next.appearance.weld
  componentMarkerStyles.value = next.appearance.components
  manualNumberingSettings.value = next.numbering
  manualLeaderLength.value = next.leaderLength
}
function resetTaskMarkerSettings() {
  assignTaskMarkerSettings(defaultMarkerSettingsProfile())
}
function applySystemMarkerTypeToTask(type) {
  assignTaskMarkerSettings(applyMarkerTypeDefault(taskMarkerSettingsProfile(), defaultMarkerSettingsProfile(), type))
}
function normalizeDefaultManualNumberingInput() {
  defaultManualNumberingSettings.value = createManualNumberingSettings(defaultManualNumberingSettings.value)
}
function applySystemLeaderToTask() {
  defaultManualLeaderLength.value = normalizeManualLeaderLength(defaultManualLeaderLength.value)
  assignTaskMarkerSettings(applyLeaderDefault(taskMarkerSettingsProfile(), defaultManualLeaderLength.value))
}
function restoreSystemDefaults() {
  const defaults = createSystemSettingsDefaults()
  themePreference.value = defaults.themePreference
  leftDrawerOpen.value = defaults.leftDrawerOpen
  reviewDrawerOpen.value = defaults.reviewDrawerOpen
  tooltipsEnabled.value = defaults.tooltipsEnabled
  operationMessagesEnabled.value = defaults.operationMessagesEnabled
  errorMessagesEnabled.value = defaults.errorMessagesEnabled
  pageButtonsPerGroup.value = defaults.pageButtonsPerGroup
  clearReferencesOnDesignUpload.value = defaults.clearReferencesOnDesignUpload
  setAutoReferenceWindow(defaults.autoReferenceWindow)
  setReferenceHintLocation(defaults.referenceHintLocation)
  canvasPerformanceMode.value = defaults.canvasPerformanceMode
  defaultMarkerStyle.value = cloneValue(defaults.markerAppearance.weld)
  defaultComponentMarkerStyles.value = cloneValue(defaults.markerAppearance.components)
  defaultManualNumberingSettings.value = defaults.manualNumbering
  defaultManualLeaderLength.value = defaults.manualLeaderLength
  restoreDefaultShortcuts()
  resetTaskMarkerSettings()
  showNotice('系统设置已恢复为默认值，并已同步到当前任务的新增标识设置。')
}
const workspacePersistence = useWorkspacePersistence({
  state: { result, pages, activeFileFingerprint, applyingHistory, hydratingWorkspace, archivingWorkspace, error },
  operations: {
    cloneValue,
    currentMarkerAppearancePayload,
    logAudit,
    showNotice,
    markDirty: page => pageCollaboration?.markDirty(page ?? currentPage.value),
    saveCurrentPage: () => pageCollaboration?.saveCurrentPage(),
    saveAllDirtyPages: () => pageCollaboration?.saveAllDirtyPages(),
    buildDraftPayload: () => ({
      result: result.value,
      projectResults: projectResults.value,
      activeProjectIndex: activeProjectIndex.value,
      markerStyle: cloneValue(markerStyle.value),
      componentMarkerStyles: cloneValue(componentMarkerStyles.value),
      currentPage: currentPage.value,
      fileName: targetPdf.value?.name || '',
      targetFile: storeWorkspaceFile(targetPdf.value),
      projectFiles: projectPdfs.value.map(storeWorkspaceFile).filter(Boolean),
      referenceMode: referenceMode.value,
      referenceProjectMode: referenceProjectMode.value,
      referenceFiles: referencePdfs.value.map(storeWorkspaceFile).filter(Boolean),
      pcfFiles: pcfFiles.value.map(storeWorkspaceFile).filter(Boolean),
      selectedPcfFolder: selectedPcfFolder.value,
      referenceResearchReady: referenceResearchReady.value,
      activeReferenceIndex: activeReferenceIndex.value,
      activeReferencePage: activeReferencePage.value,
      useReferenceNumber: useReferenceNumber.value,
      recognitionProjectId: activeRecognitionProject.value.id,
      symbolConfig: cloneValue(weldSymbolConfig.value),
      manualLeaderLength: manualLeaderLength.value,
      manualNumberingSettings: cloneValue(manualNumberingSettings.value),
    }),
  },
})
const { backendSaveState, backendSaveButtonLabel } = workspacePersistence
pageCollaboration = usePageCollaboration({
  client: api,
  clientInstanceId,
  getJobId: () => result.value?.jobId || '',
  getCurrentPage: () => currentPage.value,
  getPageData: page => pages.value.find(item => Number(item.page) === Number(page)),
  saveDraftBoundary: () => workspacePersistence.persistDraftChanges(),
  hasPendingDraftChanges: () => workspacePersistence.draftDirty.value,
  switchPage: (page, options) => projectWorkspace.changePage(page, options),
  onPageReloaded: page => clearPageHistory(page),
})
const collaborationReadOnly = computed(() => pageCollaboration.readOnly.value)
const collaborationInteractionBlocked = computed(() => collaborationReadOnly.value || pageCollaboration.synchronizing.value || pageCollaboration.saveState.value === 'saving')
const regionDeletion = useRegionDeletion({
  pageData, canvasSurface, canvasLayout, readOnly: collaborationInteractionBlocked,
  manualAddMode, manualAddType, selectedId, swapSourceId, editingId,
  operations: { beginHistory, commitHistory, logAudit, showNotice },
})
const { active: regionDeleteMode, rectangle: regionDeleteRectangle, rectangleStyle: regionDeleteRectangleStyle } = regionDeletion
watch(pageCollaboration.dirtyPages, dirty => { unsavedReviewChanges.value = dirty.size > 0 }, { deep: true })
watch([pageCollaboration.saveState, pageCollaboration.leaseUncertain], ([state, leaseUncertain]) => {
  uncertainReviewSave.value = leaseUncertain || state === 'saving' || state === 'error'
})
watch(() => [result.value?.jobId, result.value?.status], async ([jobId, status], previous) => {
  if (hydratingWorkspace.value || !jobId || jobId === 'tutorial-000207' || status !== 'complete' || (previous?.[0] === jobId && pageCollaboration.activeLock.value)) return
  await nextTick()
  pageCollaboration.enterPage(currentPage.value).catch(cause => {
    const owner = cause?.payload?.lock?.owner?.username
    error.value = owner ? `第 ${currentPage.value} 页正在由 ${owner} 核对。` : (cause?.message || String(cause))
  })
})
watch(tooltipsEnabled, value => {
  localStorage.setItem('weld-marker.tooltips-enabled', String(value))
  document.body.classList.toggle('tooltips-disabled', !value)
}, { immediate: true })
watch(operationMessagesEnabled, value => {
  saveMessagePreference(localStorage, OPERATION_MESSAGES_KEY, value)
  if (!value) {
    clearNotice()
  }
})
watch(errorMessagesEnabled, value => {
  saveMessagePreference(localStorage, ERROR_MESSAGES_KEY, value)
  if (!value) {
    errorOpen.value = false
    error.value = ''
  }
})
watch(analysisQueueDialog, open => {
  clearInterval(analysisQueueTimer)
  analysisQueueTimer = open ? window.setInterval(() => { void refreshAnalysisQueue(false) }, ANALYSIS_POLL_INTERVAL_MS) : undefined
})
watch(clearReferencesOnDesignUpload, value => {
  localStorage.setItem('weld-marker.clear-references-on-design-upload', String(value))
})
watch(error, value => {
  if (value && errorMessagesEnabled.value) errorOpen.value = true
  else if (value && !errorMessagesEnabled.value) error.value = ''
})
watch(pageButtonsPerGroup, value => {
  const normalized = Math.max(1, Math.min(20, Number.parseInt(value, 10) || 5))
  if (normalized !== value) pageButtonsPerGroup.value = normalized
  localStorage.setItem('weld-marker.page-buttons-per-group', String(normalized))
})
watch(manualLeaderLength, value => {
  const normalized = normalizeManualLeaderLength(value)
  if (normalized !== value) manualLeaderLength.value = normalized
  scheduleWorkspaceDraftSave()
})
watch(defaultManualLeaderLength, value => {
  const normalized = normalizeManualLeaderLength(value)
  if (normalized !== value) defaultManualLeaderLength.value = normalized
  localStorage.setItem('weld-marker.manual-leader-length', String(normalized))
  if (normalized === value) applySystemLeaderToTask()
})
watch(canvasPerformanceMode, value => {
  localStorage.setItem('weld-marker.canvas-performance', value)
  canvasRenderer.clearCache()
  if (targetDocument.value) void renderUnifiedCanvas(false)
})
watch([markerStyle, componentMarkerStyles], () => {
  scheduleWorkspaceDraftSave()
}, { deep: true })
watch([defaultMarkerStyle, defaultComponentMarkerStyles], () => {
  const userId = auth.user.value?.userId
  if (userId) localStorage.setItem(`weld-marker.appearance.${userId}`, JSON.stringify(defaultMarkerAppearancePayload()))
}, { deep: true })
watch(() => auth.user.value?.userId, userId => {
  defaultManualNumberingSettings.value = loadManualNumberingSettings(localStorage, userId)
  if (!userId) return
  try {
    const saved = JSON.parse(localStorage.getItem(`weld-marker.appearance.${userId}`) || 'null')
    const profile = createTaskMarkerSettings(saved || {})
    defaultMarkerStyle.value = profile.appearance.weld
    defaultComponentMarkerStyles.value = profile.appearance.components
  } catch { /* keep defaults when local settings are invalid */ }
  resetTaskMarkerSettings()
})
function isReferenceHintFocused(item) {
  return sameReferenceHint(referenceFocus.value, {
    ...item,
    file: referenceHintDocument.value?.file,
    page: referenceHintPage.value?.page,
  })
}
watch(manualNumberingSettings, value => {
  scheduleWorkspaceDraftSave()
}, { deep: true })
watch(defaultManualNumberingSettings, value => {
  const userId = auth.user.value?.userId
  if (userId) saveManualNumberingSettings(localStorage, userId, value)
}, { deep: true })
for (const type of ['weld', 'valve', 'flange', 'support']) {
  watch(
    () => {
      const group = defaultMarkerAppearanceGroups.value.find(item => item.key === type)
      return JSON.stringify({ style: group?.style, numbering: group?.numbering })
    },
    () => applySystemMarkerTypeToTask(type),
  )
}
watch(
  [referenceMode, referenceProjectMode, activeReferenceIndex, activeReferencePage, selectedPcfFolder, useReferenceNumber],
  () => scheduleWorkspaceDraftSave()
)
watch(weldSymbolConfig, () => scheduleWorkspaceDraftSave(), { deep: true })
watch(() => referencePdfs.value.map(file => file.webkitRelativePath || file.name).join('|'), (next, previous) => {
  if (next !== previous && !hydratingWorkspace.value) weldSymbolConfig.value.referenceRuleAssignments = {}
})
watch(
  () => [referencePdfs.value, pcfFiles.value].map(files => files.map(file => `${file.name}:${file.size}:${file.lastModified}`).join('|')),
  () => scheduleWorkspaceDraftSave()
)

function cloneValue(value) { return value == null ? value : JSON.parse(JSON.stringify(value)) }
function storeWorkspaceFile(file) {
  if (file?.storedFileKey) return { ...file }
  if (!(file instanceof Blob)) return null
  return {
    blob: file,
    storedFileKey: file.weldMarkerStoredFileKey || undefined,
    name: file.name || 'file',
    type: file.type || '',
    lastModified: Number(file.lastModified) || Date.now(),
    relativePath: file.webkitRelativePath || '',
    referenceIndex: Number.isInteger(file.weldMarkerReferenceIndex) ? file.weldMarkerReferenceIndex : null
  }
}
async function materializeStoredWorkspaceFile(saved) {
  return materializeWorkspaceFile(saved, loadStoredFile)
}
function firstSavedStyle(documentResult, componentType = '') {
  return (documentResult?.pages || []).flatMap(page => page.candidates || []).find(item => (
    componentType ? item.componentType === componentType : !isDesignComponent(item) && !isSpecialMarker(item)
  ))?.markerStyle || null
}
function restoreMarkerAppearances(payload) {
  const documentResult = payload?.result
  const savedGroups = payload?.markerStyles || documentResult?.markerStyles || {}
  const tutorialStyle = documentResult?.referenceMode === 'tutorial-reference-pdf' ? markerStyle.value : null
  markerStyle.value = cloneValue(savedGroups.weld || payload?.markerStyle || documentResult?.markerStyle || tutorialStyle || firstSavedStyle(documentResult) || markerStyle.value)
  const savedComponents = payload?.componentMarkerStyles || savedGroups.components || documentResult?.componentMarkerStyles || {}
  componentMarkerStyles.value = Object.fromEntries(['valve', 'flange', 'support'].map(componentType => [
    componentType,
    cloneValue(savedComponents[componentType] || firstSavedStyle(documentResult, componentType) || componentMarkerStyles.value[componentType])
  ]))
}
function markerAppearancePayload(weldValue, componentValues) {
  const weld = cloneValue(weldValue)
  const components = cloneValue(componentValues)
  return { markerStyle: weld, componentMarkerStyles: components, markerStyles: { weld, components } }
}
function currentMarkerAppearancePayload() {
  return markerAppearancePayload(markerStyle.value, componentMarkerStyles.value)
}
function defaultMarkerAppearancePayload() {
  return markerAppearancePayload(defaultMarkerStyle.value, defaultComponentMarkerStyles.value)
}
function logAudit(action, details = {}) { void api.audit(action, { jobId: result.value?.jobId || null, file: targetPdf.value?.name || null, page: currentPage.value, ...details }).catch(() => {}) }
function scheduleDraftSave() { workspacePersistence.scheduleDraftSave() }
function scheduleWorkspaceDraftSave() { workspacePersistence.scheduleWorkspaceDraftSave() }
function persistBackendChanges(notifyUser = false) { return workspacePersistence.persistBackendChanges(notifyUser) }
async function openManagedDraft(entry) {
  const loadedEntry = await loadManagedDraft(entry)
  const payload = loadedEntry?.payload
  if (!payload?.result) { error.value = '该草稿没有可恢复的识别结果。'; return }
  const storedProjectFiles = (payload.projectFiles || []).map(restoreWorkspaceFile).filter(Boolean)
  const storedTargetFile = restoreWorkspaceFile(payload.targetFile)
  const availableFiles = storedProjectFiles.length ? storedProjectFiles : (storedTargetFile ? [storedTargetFile] : [])
  if (!availableFiles.length && (!targetPdf.value || targetPdf.value.name !== payload.fileName)) {
    error.value = `旧草稿“${payload.fileName || '未命名图纸'}”未保存主图文件，请先在工程输入中选择同名 PDF 后再恢复。`
    return
  }
  try {
    await pageCollaboration?.leavePage()
  } catch (cause) {
    error.value = `当前工作区保存失败，未打开草稿：${cause?.message || cause}`
    return
  }
  if (availableFiles.length) {
    projectPdfs.value = availableFiles
    projectMode.value = availableFiles.length > 1 ? 'folder' : 'single'
    activeProjectIndex.value = Math.max(0, Math.min(Number(payload.activeProjectIndex) || 0, availableFiles.length - 1))
    targetPdf.value = availableFiles[activeProjectIndex.value]
    projectResults.value = Array.isArray(payload.projectResults) ? cloneValue(payload.projectResults) : new Array(availableFiles.length).fill(null)
    await loadTargetPdf(targetPdf.value, true, false)
  }
  activeFileFingerprint.value = entry.key
  pendingDraft.value = { key: entry.key, title: payload.fileName || '浏览器草稿', source: 'indexeddb', priority: 1, payload, savedAt: entry.savedAt }
  draftManagerDialog.value = false
  await restoreWorkspaceDraft()
}

async function restoreQueueJob(job) {
  if (!job?.canRestore || analysisQueueActionId.value) return
  try { await pageCollaboration?.leavePage() } catch (cause) {
    error.value = `当前页保存失败，未恢复其他任务：${cause?.message || cause}`
    return
  }
  analysisQueueActionId.value = job.jobId
  hydratingWorkspace.value = true
  try {
    const [snapshot, source, references] = await Promise.all([
      api.getJobWorkspace(job.jobId),
      api.getJobTargetFile(job.jobId, job.fileName),
      api.getJobReferenceManifest(job.jobId),
    ])
    restoreRecognitionSettings(snapshot)
    clearProject()
    clearReferenceFiles()
    resetTaskMarkerSettings()
    projectMode.value = 'single'
    projectPdfs.value = [source]
    projectResults.value = [snapshot]
    targetPdf.value = source
    referencePdfs.value = references
    referenceProjectMode.value = references.length > 1 ? 'folder' : 'single'
    referenceMode.value = references.length ? 'pdf' : 'none'
    const queuedNumbering = snapshot.numberingConfig || {}
    useReferenceNumber.value = queuedNumbering.useReferenceNumber ?? useReferenceNumber.value
    startNumber.value = Math.max(1, Number(queuedNumbering.startNumber) || Number(startNumber.value) || 1)
    numberPrefix.value = String(queuedNumbering.prefix ?? numberPrefix.value)
    numberSuffix.value = String(queuedNumbering.suffix ?? numberSuffix.value)
    // Server-side analysis stores recognition identities separately from the
    // editable display number. Fill only missing display numbers when a queue
    // result is restored, while preserving any numbers reviewers already saved.
    numberResult(snapshot, startNumber.value, { preserveExistingNumbers: true })
    activeReferenceIndex.value = references.length ? 0 : -1
    referencePdf.value = references[0] || null
    await loadTargetPdf(source, true, false, snapshot.analyzedRange?.[0] || snapshot.pages?.[0]?.page || 1)
    result.value = snapshot
    projectResults.value[0] = snapshot
    restoreMarkerAppearances({ result: snapshot })
    currentPage.value = snapshot.analyzedRange?.[0] || snapshot.pages?.[0]?.page || 1
    previewPage.value = currentPage.value
    referenceResearchReady.value = true
    analysisQueueDialog.value = false
    await nextTick()
    if (references.length) await syncReferenceForPage(currentPage.value, true)
    else await renderUnifiedCanvas(true)
    showNotice(`已从服务器恢复“${job.fileName}”，可以直接核对。`)
    scheduleWorkspaceDraftSave()
    void archiveLazyReferencesForDraft()
  } catch (cause) { error.value = `恢复解析任务失败：${cause.message || cause}` }
  finally {
    await nextTick()
    hydratingWorkspace.value = false
    analysisQueueActionId.value = ''
  }
}

let authenticatedWorkspaceStarted = false
async function startAuthenticatedWorkspace() {
  if (authenticatedWorkspaceStarted) return
  authenticatedWorkspaceStarted = true
  try {
    if (!localStorage.getItem(tutorialStorageKey)) window.setTimeout(openTutorial, 450)
  } catch { window.setTimeout(openTutorial, 450) }
  try {
    const response = await api.getPcfFolders()
    pcfFolderOptions.value = Array.isArray(response?.folders)
      ? response.folders.filter(item => item && typeof item === 'object' && item.value && item.title)
      : []
  } catch { pcfFolderOptions.value = [] }
  startServiceHealthPolling()
  window.addEventListener('keydown', handleShortcut)
}

async function logoutUser() {
  try { await pageCollaboration?.leavePage() } catch (cause) {
    error.value = cause?.message || String(cause)
    return
  }
  await auth.logout()
  assistantOpen.value = false
  authenticatedWorkspaceStarted = false
  stopServiceHealthPolling()
  window.removeEventListener('keydown', handleShortcut)
  await router.replace('/login')
}

function handleAuthenticationRequired() {
  pageCollaboration?.dispose()
  auth.user.value = null
  auth.status.value = 'anonymous'
  authenticatedWorkspaceStarted = false
  stopServiceHealthPolling()
  window.removeEventListener('keydown', handleShortcut)
  void router.replace({ path: '/login', query: { redirect: '/main' } })
}

watch(auth.status, status => { if (status === 'authenticated') void startAuthenticatedWorkspace() })

onMounted(async () => {
  window.addEventListener('resize', syncAppViewport)
  window.addEventListener('drawing-marker:authentication-required', handleAuthenticationRequired)
  await refreshServiceHealth()
  await auth.restore()
  if (auth.status.value === 'authenticated') await startAuthenticatedWorkspace()
  else await router.replace({ path: '/login', query: { redirect: '/main' } })
})

onBeforeUnmount(() => {
  const activeLabelDrag = labelDragState.value
  if (activeLabelDrag?.animationFrame) window.cancelAnimationFrame(activeLabelDrag.animationFrame)
  if (activeLabelDrag?.element) activeLabelDrag.element.style.translate = ''
  leaderLineElements.clear()
  disposeReferenceWorkspace()
  recoveryConflictDecision.dispose()
  workspacePersistence.dispose()
  pageCollaboration?.dispose()
  disposeProjectWorkspace()
  noticeChannel.dispose()
  stopServiceHealthPolling()
  clearInterval(analysisQueueTimer)
  window.removeEventListener('keydown', handleShortcut)
  window.removeEventListener('resize', syncAppViewport)
  window.removeEventListener('drawing-marker:authentication-required', handleAuthenticationRequired)
  canvasRenderer.dispose()
  referenceWindow.dispose()
  targetDocument.value?.destroy?.()
  referenceDocument.value?.destroy?.()
  disposeAnalysisWorkflow()
  regionDeletion.exit()
})

function syncAppViewport() {
  appViewport.value = { width: window.innerWidth, height: window.innerHeight }
}

function handleShortcut(event) {
  const tag = event.target?.tagName?.toLowerCase()
  const isEditing = ['input', 'textarea', 'select'].includes(tag) || event.target?.isContentEditable
  if (tutorialOpen.value || tutorialCatalogOpen.value) return
  if (event.key === 'F1') { event.preventDefault(); shortcutMenu.value = !shortcutMenu.value; return }
  if (isEditing && event.key !== 'Escape') return
  if ((draftRecoveryDialog.value || draftManagerDialog.value || draftDeleteTarget.value || draftBatchDeleteDialog.value || systemSettingsDialog.value || symbolConfigDialog.value) && event.key !== 'Escape') return
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') { event.preventDefault(); undo(); return }
  if ((event.ctrlKey || event.metaKey) && (event.key.toLowerCase() === 'y' || (event.shiftKey && event.key.toLowerCase() === 'z'))) { event.preventDefault(); redo(); return }
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'c' && selectedCandidate.value) { event.preventDefault(); copySelectedCandidate(); return }
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'v' && result.value) {
    event.preventDefault()
    const point = canvasPointerPosition.value
    pasteCopiedAtClientPoint(point?.clientX, point?.clientY)
    return
  }
  if (matchesShortcut(event, shortcutSettings.value.analyze)) { event.preventDefault(); if (!shortcutConflict.value && !loading.value && targetPdf.value) void analyze(); return }
  if (matchesShortcut(event, shortcutSettings.value.save)) { event.preventDefault(); if (!shortcutConflict.value && result.value) void save(); return }
  if (matchesShortcut(event, shortcutSettings.value.openReferenceWindow)) { event.preventDefault(); if (!shortcutConflict.value) void openReferenceWindow(); return }
  if (matchesShortcut(event, shortcutSettings.value.deleteRegion)) {
    event.preventDefault()
    if (!shortcutConflict.value && !event.repeat) regionDeletion.toggle()
    return
  }
  if (!collaborationInteractionBlocked.value && manualAddMode.value && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'a') {
    event.preventDefault()
    const point = canvasPointerPosition.value
    if (!point) { error.value = '请先把鼠标移到待标识 PDF 的目标位置。'; return }
    addManualAtClientPoint(point.clientX, point.clientY)
    return
  }
  if (matchesShortcut(event, shortcutSettings.value.addWeld)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode(); return }
  if (matchesShortcut(event, shortcutSettings.value.addValve)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('valve'); return }
  if (matchesShortcut(event, shortcutSettings.value.addFlange)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('flange'); return }
  if (matchesShortcut(event, shortcutSettings.value.addSupport)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('support'); return }
  if (matchesShortcut(event, shortcutSettings.value.modifyAll)) { event.preventDefault(); if (!shortcutConflict.value) toggleManualAddMode('all'); return }
  if (matchesShortcut(event, shortcutSettings.value.swapWeld)) { event.preventDefault(); if (!shortcutConflict.value && !collaborationInteractionBlocked.value) swapSelectedWeldNumbers(); return }
  if (matchesShortcut(event, shortcutSettings.value.deleteWeld)) { event.preventDefault(); if (!shortcutConflict.value && !collaborationInteractionBlocked.value) deleteSelectedWeld(); return }
  if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomBy(1.25); return }
  if (event.key === '-') { event.preventDefault(); zoomBy(.8); return }
  if (event.key === '0') { event.preventDefault(); fitCanvas(); return }
  if (event.key === 'ArrowLeft') { event.preventDefault(); stepPage(-1); return }
  if (event.key === 'ArrowRight') { event.preventDefault(); stepPage(1); return }
  if (event.key === 'Escape') { regionDeletion.exit(); cancelInlineEdit(); selectedId.value = ''; swapSourceId.value = ''; shortcutMenu.value = false; manualAddMode.value = false }
}

async function changePage(pageNumber, fallbackPages = []) {
  if (autoReferenceWindow.value && !referenceDetached.value && referencePdf.value) void openReferenceWindow({ automatic: true })
  if (!result.value || result.value.jobId === 'tutorial-000207' || result.value.status !== 'complete') return projectWorkspace.changePage(pageNumber)
  try {
    const enteredPage = await pageCollaboration.enterPage(pageNumber, fallbackPages)
    projectWorkspace.releasePageDetails(enteredPage, [...pageCollaboration.dirtyPages.value])
    if (Number(enteredPage) !== Number(pageNumber)) showNotice(`第 ${pageNumber} 页正由其他用户核对，已自动跳到第 ${enteredPage} 页。`)
    return enteredPage
  } catch (cause) {
    const owner = cause?.payload?.lock?.owner?.username
    error.value = owner ? `第 ${pageNumber} 页正在由 ${owner} 核对，当前页面未切换。` : (cause?.message || String(cause))
    return false
  }
}
function pageLockOwner(pageNumber) {
  const lock = pageCollaboration?.lockSummaries.value.find(item => Number(item.page) === Number(pageNumber))
  return lock?.clientInstanceId === clientInstanceId ? '' : (lock?.owner?.username || '')
}

function renderUnifiedCanvas(fitAfter = false, indicateLoading = true) {
  return canvasRenderer.render(fitAfter, indicateLoading)
}

function scheduleResolutionRender() {
  canvasRenderer.schedule()
}
async function save() {
  if (!result.value) return
  if (result.value.jobId === 'tutorial-000207') {
    await workspacePersistence.persistDraftChanges()
    logAudit('tutorial.saved-locally')
    showNotice('教程校对结果已保存在当前浏览器草稿。')
    return
  }
  try {
    await persistBackendChanges(true)
  } catch { /* persistBackendChanges already reports the failure */ }
}
async function saveAndClose() {
  if (!result.value || saveAndCloseRunning.value) return
  saveAndCloseRunning.value = true
  dismissError()
  error.value = ''
  try {
    await saveAndCloseWorkspace({
      persist: () => result.value?.jobId === 'tutorial-000207'
        ? workspacePersistence.persistDraftChanges()
        : persistBackendChanges(false),
      leave: () => pageCollaboration?.leavePage(),
      close: () => { clearProject(); clearReferenceFiles() },
      showTasks: () => openAnalysisQueue(),
    })
    showNotice('校对结果已保存，任务工作区已关闭。')
  } catch (cause) {
    if (!error.value) error.value = `保存并关闭失败，当前任务仍保持打开：${cause?.message || cause}`
  } finally {
    saveAndCloseRunning.value = false
  }
}
async function exportPdf() {
  if (!result.value) return
  loading.value = true
  try {
    const exportCandidates = await loadAllExportCandidates()
    const styled = exportCandidates.map(item => ({ ...item, markerStyle: { ...candidateMarkerStyle(item) } }))
    const exported = result.value.jobId === 'tutorial-000207'
      ? await api.exportTutorialPdf(styled)
      : await api.exportPdf(result.value.jobId, styled)
    window.location.href = exported.downloadUrl
    logAudit('pdf.exported')
    showNotice('标识 PDF 已生成。')
  } catch (cause) { error.value = cause.message } finally { loading.value = false }
}
async function loadAllExportCandidates() {
  const sourcePages = result.value?.pages || []
  const pageCandidates = new Array(sourcePages.length)
  let cursor = 0
  const worker = async () => {
    while (cursor < sourcePages.length) {
      const index = cursor++
      const page = sourcePages[index]
      let fullPage = page
      if (page?.detailsLoaded === false && result.value?.jobId) {
        const response = await api.getJobPage(result.value.jobId, Number(page.page))
        fullPage = response.page || response
      }
      pageCandidates[index] = (fullPage?.candidates || []).filter(item => item.included !== false)
    }
  }
  await Promise.all([worker(), worker(), worker()])
  return pageCandidates.flat()
}
async function exportCsv() {
  if (!result.value) return
  loading.value = true
  try {
    const exportCandidates = await loadAllExportCandidates()
  const rows = [['页码', '对象类型', '编号', 'X', 'Y', '证据', '置信度', '参考匹配']]
    exportCandidates.forEach(item => rows.push([item.page, isSpecialMarker(item) ? 'special' : (isDesignComponent(item) ? item.componentType : 'weld'), item.number, item.x.toFixed(2), item.y.toFixed(2), item.evidence, Math.round((item.confidence || 0) * 100) + '%', item.referenceLabel || '']))
  const csv = encodeCsv(rows)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  link.download = '图纸标识识别结果.csv'
  link.click()
  URL.revokeObjectURL(link.href)
    logAudit('csv.exported', { candidates: exportCandidates.length })
  } catch (cause) { error.value = cause.message } finally { loading.value = false }
}

</script>

<template>
  <v-app class="weld-app" :class="{ 'workspace-readonly': collaborationInteractionBlocked, 'workspace-hide-context-chips': workspaceLayout.hideContextChips, 'workspace-narrow-drawers': workspaceLayout.narrowDrawers, 'workspace-two-row-toolbar': workspaceLayout.twoRowToolbar }" @pointerdown.capture="onApplicationPointerDown">
    <div v-if="auth.status.value === 'loading'" class="auth-loading"><v-progress-circular indeterminate color="primary" /><span>正在检查登录状态…</span></div>
    <template v-else-if="auth.status.value === 'authenticated'">
    <AppHeader
      v-model:shortcut-menu="shortcutMenu"
      :height="appHeaderHeight"
      :backend-health="health"
      :mineru-health="mineruHealth"
      :mineru-status-text="mineruStatusText"
      :shortcuts="shortcutSettings"
      :user="auth.user.value"
      :projects="recognitionProjects"
      :active-project="activeRecognitionProject"
      @open-project-rules="symbolConfigDialog = true"
      @open-queue="openAnalysisQueue"
      @open-assistant="assistantOpen = true"
      @open-tutorial="openTutorial"
      @open-settings="systemSettingsDialog = true"
      @switch-project="switchRecognitionProject"
      @logout="logoutUser"
    />

    <AssistantPanel
      v-model="assistantOpen"
      :username="auth.user.value?.username || ''"
      :context="assistantContext"
    />

    <v-navigation-drawer data-tour="input" permanent :rail="!leftDrawerOpen" :rail-width="24" :width="leftDrawerWidth" class="control-drawer">
      <button v-if="!leftDrawerOpen" type="button" class="drawer-edge-toggle drawer-edge-toggle--left" aria-label="打开识别输入区域" @click="leftDrawerOpen = true"><span>›</span></button>
      <template v-else>
        <div class="drawer-heading">
          <div><div class="drawer-heading__title">{{ leftPanelTab === 'reference' ? '对照图提示' : '识别输入与操作' }}</div></div>
          <button data-tour="left-panel-toggle" type="button" class="drawer-view-toggle" :class="{ 'drawer-view-toggle--active': leftPanelTab === 'reference' }" :disabled="leftPanelTab === 'input' && (!result?.ep3dReferenceInventory?.documents?.length || !referenceHintsInDesignWindow)" :aria-label="leftPanelTab === 'reference' ? '切换到输入操作' : '切换到对照图提示'" @click="leftPanelTab = leftPanelTab === 'reference' ? 'input' : 'reference'">
            <svg v-if="leftPanelTab === 'input'" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="7" height="15" rx="1.5" /><rect x="13.5" y="7.5" width="7" height="12" rx="1.5" /><path d="M7 9h.01M7 13h.01M17 12h.01M17 16h.01" /></svg>
            <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z" /><path d="M8 9h8M8 13h8M8 17h5" /></svg>
            <v-tooltip activator="parent" location="bottom">{{ referenceHintsInDetachedWindow ? '对照图提示已显示在独立对照图窗口' : (leftPanelTab === 'reference' ? '切换到输入操作' : '查看对照图识别提示') }}</v-tooltip>
          </button>
        </div>
        <div class="drawer-scroll pa-4">
        <template v-if="leftPanelTab === 'input'">
        <div class="section-label">工程输入</div>
        <v-btn-toggle data-tour="project-input" :model-value="projectMode" mandatory divided color="secondary" density="compact" class="w-100 mb-3" @update:model-value="changeProjectMode">
          <v-btn value="single" class="flex-grow-1">单个 PDF<v-tooltip activator="parent">选择一份待标识 ISO PDF</v-tooltip></v-btn>
          <v-btn value="folder" class="flex-grow-1">文件夹 PDF<v-tooltip activator="parent">读取文件夹中的全部 PDF，切换模式不会报空文件错误</v-tooltip></v-btn>
        </v-btn-toggle>

        <v-file-input v-if="projectMode === 'single'" accept="application/pdf,.pdf" class="drop-file-input" label="点击或拖拽待标识 ISO PDF" prepend-icon="" clearable @update:model-value="setProjectFiles"><v-tooltip activator="parent">可点击选择或把 PDF 拖入此区域，选择后立即渲染</v-tooltip></v-file-input>
        <v-file-input v-else :model-value="projectPdfs" webkitdirectory directory multiple class="drop-file-input folder-file-input" label="点击或拖拽工程文件夹" prepend-icon="" clearable @drop.capture="handleProjectFolderDrop" @update:model-value="handleProjectFolderSelection"><template #selection="{ fileNames }"><span class="folder-selection">已选择 {{ fileNames.length }} 个文件</span></template><v-tooltip activator="parent">可点击选择或拖入文件夹，最多读取 1000 个文件，并自动过滤非 PDF 文件</v-tooltip></v-file-input>

        <v-list v-if="projectPdfs.length > 1" density="compact" class="project-list my-2" border>
          <v-list-item v-for="(file, index) in projectPdfs" :key="file.webkitRelativePath || file.name" :active="activeProjectIndex === index" color="secondary" @click="selectProject(index)">
            <v-list-item-title>{{ file.webkitRelativePath || file.name }}</v-list-item-title>
            <template #append><v-chip size="x-small" :color="projectResults[index] ? 'success' : 'default'">{{ projectResults[index] ? '已分析' : '待分析' }}</v-chip></template>
            <v-tooltip activator="parent">切换当前预览和编辑的 PDF</v-tooltip>
          </v-list-item>
        </v-list>

        <div v-if="projectMode === 'single'" data-tour="page-range" class="parameter-grid mt-3">
          <v-text-field v-model="startPage" type="number" min="1" label="设计图起始页"><v-tooltip activator="parent">仅指定当前单个设计图 PDF 的研究起始页</v-tooltip></v-text-field>
          <v-text-field v-model="endPage" type="number" min="1" label="设计图结束页"><v-tooltip activator="parent">仅作用于当前单个设计图 PDF；留空研究到末页</v-tooltip></v-text-field>
        </div>

        <v-divider class="my-4" />
        <div class="section-label">参考输入</div>
        <v-btn-toggle data-tour="reference-mode" :model-value="referenceMode" mandatory divided color="secondary" density="compact" class="w-100 mb-3" @update:model-value="changeReferenceMode">
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
            <v-file-input :model-value="referencePdfs" webkitdirectory directory multiple class="drop-file-input folder-file-input" label="点击或拖拽对照 PDF 文件夹" prepend-icon="" clearable @drop.capture="handleReferenceFolderDrop" @update:model-value="handleReferenceFolderSelection"><template #selection="{ fileNames }"><span class="folder-selection">已选择 {{ fileNames.length }} 个文件</span></template><v-tooltip activator="parent">最多读取 1000 个文件；仅保留 PDF，输入框只显示文件数量</v-tooltip></v-file-input>
          </template>
          <v-alert v-if="unavailableReferenceFiles.length" type="warning" variant="tonal" density="compact" class="mb-3">标识数据已恢复，但原任务中的对照 PDF 无法从本机取回。请重新选择：{{ unavailableReferenceFiles.join('、') }}</v-alert>
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
        <button data-tour="numbering" type="button" class="reference-number-toggle mt-1" :class="{ 'reference-number-toggle--active': useReferenceNumber }" :aria-pressed="useReferenceNumber" @click="useReferenceNumber = !useReferenceNumber">
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
        <v-btn data-tour="enqueue-analysis" block color="secondary" variant="tonal" size="large" :loading="enqueueing" :disabled="!targetPdf || loading || referenceInputMissing" class="queue-submit-button mt-2" @click="enqueueForReview">推入解析队列<v-tooltip activator="parent">将图纸提前交给服务器后台解析；完成后可从顶部状态按钮直接恢复核对</v-tooltip></v-btn>
        <div v-if="enqueueing" class="queue-upload-progress" role="status" aria-live="polite">
          <div><span>{{ enqueueProgress.message }}</span><strong>{{ Math.round(enqueueProgress.percent) }}%</strong></div>
          <v-progress-linear :model-value="enqueueProgress.percent" color="secondary" height="6" rounded striped />
        </div>
        <v-btn data-tour="analyze" block color="accent" size="large" :loading="loading" :disabled="!targetPdf || enqueueing || referenceInputMissing" class="primary-action-button mt-2" @click="analyze">智能编号<v-tooltip activator="parent">识别焊口、阀门、法兰和支架，并按类型自动生成编号</v-tooltip></v-btn>
        </template>
        <template v-else>
          <v-alert v-if="!referenceHintPage" type="info" variant="tonal" density="compact">当前编辑页尚无可显示的对照识别结果，可切回“输入操作”完成解析或选择对照图。</v-alert>
          <template v-else>
            <div data-tour="reference-hints" class="reference-hint-meta">
              <strong>{{ referenceHintDocument?.file }}</strong>
              <span>第 {{ referenceHintPage.page }} 页 · 首次点击显示提示；再次点击同一对象可放大定位，点击图中空白处可取消选择</span>
            </div>
            <v-btn block class="reference-show-all-button mt-3" :color="referenceShowAll ? 'secondary' : 'primary'" :variant="referenceShowAll ? 'flat' : 'tonal'" @click="showAllReferenceHints">{{ referenceShowAll ? '隐藏全部对照标识' : '显示全部对照标识' }}</v-btn>
            <section v-for="group in referenceHintGroups" :key="group.key" class="reference-hint-group">
              <div class="reference-hint-group__heading"><span><i :style="{ backgroundColor: group.color }" />{{ group.title }}</span><strong>{{ group.items.length }}</strong></div>
              <v-list v-if="group.items.length" density="compact" class="reference-hint-list" border>
                <v-list-item v-for="item in group.items" :key="`${group.key}-${item.label}-${item.point.join('-')}`" :active="isReferenceHintFocused(item)" color="secondary" @click="jumpToReferenceHint(item)">
                  <v-list-item-title>{{ item.label }}</v-list-item-title>
                  <v-list-item-subtitle>{{ isReferenceHintFocused(item) ? '再次点击定位' : (item.geometryVerified === false ? '引线定位 · 点击显示' : '点击显示') }}</v-list-item-subtitle>
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

    <v-main data-tour="canvas" class="main-area">
      <v-container fluid class="main-container pa-3">
        <v-snackbar v-if="errorMessagesEnabled" v-model="errorOpen" class="app-message" color="error" location="bottom" timeout="5000" transition="fade-transition" @after-leave="finishErrorLeave">{{ error }}<template #actions><v-btn variant="text" @click="dismissError">关闭<v-tooltip activator="parent">关闭错误消息</v-tooltip></v-btn></template></v-snackbar>
        <v-snackbar v-if="operationMessagesEnabled && !loading" v-model="noticeOpen" class="app-message" color="info" location="bottom" timeout="4000" transition="fade-transition" @after-leave="finishNoticeLeave">{{ notice }}<template #actions><v-btn variant="text" @click="clearNotice">关闭<v-tooltip activator="parent">关闭状态消息</v-tooltip></v-btn></template></v-snackbar>
        <v-alert v-if="result?.pcf?.degraded" type="warning" variant="tonal" density="compact" class="mb-3">PCF 当前使用内置降级解析器，拓扑完整性可能受影响：{{ result.pcf.warning }}</v-alert>

        <v-card v-if="!targetPdf" class="empty-card" variant="tonal" color="primary"><v-card-title>选择 ISO PDF 或整个工程文件夹</v-card-title><v-card-text>系统会先检查可恢复工作区，再渲染图纸。</v-card-text></v-card>

        <template v-else>
          <AnalysisProgressPanel
            v-if="loading"
            :state="analysisProgress"
            :percent="analysisProgressPercent"
            :open="progressPanelOpen"
            :active-job-id="activeAnalysisJobId"
            :cancelling="cancellingAnalysis"
            :project-progress="projectProgress"
            :project-count="projectPdfs.length || 1"
            @toggle="progressPanelOpen = !progressPanelOpen"
            @cancel="cancelActiveAnalysis"
          />

          <v-card v-if="projectPdfs.length > 1" class="document-card mb-2" variant="outlined"><v-tabs :model-value="activeProjectIndex" color="secondary" density="compact" @update:model-value="selectProject"><v-tab v-for="(file, index) in projectPdfs" :key="file.webkitRelativePath || file.name" :value="index">{{ file.name }}</v-tab></v-tabs></v-card>

          <div class="work-grid">
            <v-card class="canvas-card" elevation="2">
              <v-toolbar data-tour="canvas-toolbar" density="compact" color="header" class="canvas-toolbar">
                <div class="canvas-toolbar__context">
                  <v-chip size="small" color="primary">待标识 ISO PDF</v-chip>
                  <v-chip v-if="referenceDocument" size="small" color="secondary">对照 PDF</v-chip>
                  <v-btn v-if="referenceDetached" size="x-small" icon class="detached-reference-button" variant="text" aria-label="恢复同屏显示对照图" @click="restoreEmbeddedReference"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5.5" width="8" height="13" rx="1.3" /><rect x="12.5" y="5.5" width="8" height="13" rx="1.3" /><path d="m16.5 9-3 3 3 3" /></svg><v-tooltip activator="parent">恢复同屏显示对照图</v-tooltip></v-btn>
                  <v-btn v-else size="x-small" icon class="detached-reference-button" variant="text" aria-label="独立窗口打开对照图" :disabled="!referencePdf || !referenceResearchReady" @click="openReferenceWindow"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="5.5" width="11" height="13" rx="1.5" /><path d="M10 3.5h10.5v12M14 10l6.5-6.5M15.5 3.5h5v5" /></svg><v-tooltip activator="parent">独立窗口打开对照图；快捷键 {{ shortcutSettings.openReferenceWindow }}</v-tooltip></v-btn>
                </div>
                <div class="canvas-toolbar__navigation">
                  <div data-tour="page-navigator" class="page-navigator">
                  <v-btn size="small" icon variant="text" class="page-group-button" aria-label="显示上一组页码" :disabled="!canShowPreviousPageGroup" @click="stepPageGroup(-1)"><span aria-hidden="true">‹</span><v-tooltip activator="parent">上一组页码</v-tooltip></v-btn>
                  <v-btn v-for="pageNumber in visibleNavigationPages" :key="pageNumber" size="small" class="page-status-button" :class="navigationPageClasses(pageNumber)" :variant="shownPage === pageNumber ? 'flat' : 'tonal'" @click="navigateDesignPage(pageNumber)">P{{ pageNumber }}<v-tooltip v-if="pageLockOwner(pageNumber)" activator="parent">{{ pageLockOwner(pageNumber) }} 正在核对此页</v-tooltip></v-btn>
                  <v-btn size="small" icon variant="text" class="page-group-button" aria-label="显示下一组页码" :disabled="!canShowNextPageGroup" @click="stepPageGroup(1)"><span aria-hidden="true">›</span><v-tooltip activator="parent">下一组页码</v-tooltip></v-btn>
                  </div>
                  <div class="design-page-jump" aria-label="跳转到指定设计图页">
                    <v-text-field v-model="designPageInput" type="number" min="1" :max="targetPageCount || undefined" density="compact" hide-details placeholder="页码" aria-label="设计图跳转页码" @keydown.enter.prevent="submitDesignPageJump" />
                    <span class="design-page-total">/ {{ targetPageCount || navigationPages.length || 0 }} 页</span>
                    <v-btn size="small" icon variant="text" aria-label="跳转到指定设计图页" :disabled="!navigationPages.length" @click="submitDesignPageJump"><span aria-hidden="true">↵</span><v-tooltip activator="parent">跳转到指定设计图页</v-tooltip></v-btn>
                  </div>
                </div>
                <v-spacer />
                <div class="canvas-toolbar__actions">
                  <v-btn size="small" icon :disabled="!canUndo" aria-label="撤销" @click="undo"><span aria-hidden="true">↶</span></v-btn>
                  <v-btn size="small" icon :disabled="!canRedo" aria-label="重做" @click="redo"><span aria-hidden="true">↷</span></v-btn>
                  <v-btn size="small" class="manual-mode-button" :disabled="collaborationInteractionBlocked" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'weld' }" aria-label="焊口修改模式" @click="toggleManualAddMode('weld')">W<v-tooltip activator="parent">焊口修改模式（W）；鼠标定位后按 A 新增</v-tooltip></v-btn>
                  <v-btn size="small" class="manual-mode-button" :disabled="collaborationInteractionBlocked" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'valve' }" aria-label="阀门修改模式" @click="toggleManualAddMode('valve')">V<v-tooltip activator="parent">阀门修改模式（V）；鼠标定位后按 A 新增</v-tooltip></v-btn>
                  <v-btn size="small" class="manual-mode-button" :disabled="collaborationInteractionBlocked" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'flange' }" aria-label="法兰修改模式" @click="toggleManualAddMode('flange')">F<v-tooltip activator="parent">法兰修改模式（F）；鼠标定位后按 A 新增</v-tooltip></v-btn>
                  <v-btn size="small" class="manual-mode-button" :disabled="collaborationInteractionBlocked" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'support' }" aria-label="支架修改模式" @click="toggleManualAddMode('support')">S<v-tooltip activator="parent">支架修改模式（S）；鼠标定位后按 A 新增</v-tooltip></v-btn>
                  <v-btn size="small" class="manual-mode-button" :disabled="collaborationInteractionBlocked" :class="{ 'manual-add-button--active': manualAddMode && manualAddType === 'all' }" aria-label="全局标识修改模式" @click="toggleManualAddMode('all')">M<v-tooltip activator="parent">全局标识修改模式（M）；可调整所有标识，鼠标定位后按 A 新增特殊标识</v-tooltip></v-btn>
                  <v-btn size="small" @click="zoomBy(.8)">−</v-btn><span class="zoom-indicator">{{ Math.round(zoom * 100) }}%</span><v-btn size="small" @click="zoomBy(1.25)">+</v-btn><v-btn size="small" @click="fitCanvas">适合</v-btn>
                </div>
              </v-toolbar>

              <div ref="canvasViewport" data-tour="canvas-surface" class="canvas-viewport" :class="{ panning: canvasPanState, 'manual-adding': manualAddMode, 'region-deleting': regionDeleteMode, 'navigation-sync': pageCollaboration.synchronizing.value }" @click="onCanvasClick" @wheel.prevent="onCanvasWheel" @pointerdown.capture="clearReferenceHintSelection($event); regionDeletion.start($event)" @pointerdown="startCanvasPan" @pointermove="moveCanvasPan($event); regionDeletion.move($event)" @pointerleave="canvasPointerPosition = null" @pointerup="endCanvasPan($event); regionDeletion.finish($event)" @pointercancel="endCanvasPan($event); regionDeletion.cancelSelection()" @lostpointercapture="regionDeletion.cancelSelection()" @auxclick.prevent>
                <div v-if="regionDeleteMode" class="region-delete-hint" role="status">区域删除（{{ regionDeletion.scopeLabel.value }}） · 左键拖框 · Esc 退出 · Ctrl+Z 撤销</div>
                <div v-if="collaborationReadOnly" class="canvas-readonly-hint" role="status" aria-live="polite">当前页的核对锁已失效，页面已转为只读。请切换到其他页后再重新打开。</div>
                <div ref="canvasSurface" class="canvas-surface" :style="canvasSurfaceStyle">
                  <canvas ref="pdfCanvas" class="pdf-canvas"></canvas>
                  <div v-if="regionDeleteRectangle" class="region-delete-rectangle" :style="regionDeleteRectangleStyle" aria-hidden="true" />
                  <div v-for="marker in (displayEmbeddedReference ? referenceDisplayMarkers : [])" :key="marker.key" class="reference-focus-marker" :class="[`reference-focus-marker--${marker.type}`, { 'reference-focus-marker--all': referenceShowAll }]" :style="marker.style" :title="marker.label"><i :style="marker.linkStyle" aria-hidden="true" /><span>{{ marker.label }}</span></div>
                  <svg v-if="result && pageData" class="leader-layer" :viewBox="`0 0 ${canvasLayout.width} ${canvasLayout.height}`" preserveAspectRatio="none" aria-hidden="true">
                    <line v-for="item in pageData.candidates.filter(candidate => candidate.included !== false)" :key="`line-${item.id}`" :ref="element => setLeaderLineElement(item.id, element)" :x1="anchorPoint(item).x" :y1="anchorPoint(item).y" :x2="leaderEnd(item).x" :y2="leaderEnd(item).y" :style="leaderStyle(item)" />
                  </svg>
                  <div v-for="item in (result && pageData && manualAddMode ? pageData.candidates.filter(candidate => candidate.included !== false && candidateMatchesModificationType(candidate)) : [])" :key="`group-${item.id}`" class="marker-group-handle" :class="{ selected: selectedId === item.id, 'marker-group-handle--temporarily-hidden': labelDragState?.id === item.id || anchorDragState?.id === item.id || groupDragState?.id === item.id }" :style="{ left: `${groupHandlePosition(item).x}px`, top: `${groupHandlePosition(item).y}px` }" role="button" tabindex="0" :aria-label="`整体移动标识 ${item.number || '?'}`" title="拖动可整体移动标识框和引线" @click.stop="selectCandidate(item)" @pointerdown="startGroupDrag($event, item)" @pointermove="moveGroup($event, item)" @pointerup="endGroupDrag($event, item)" @pointercancel="endGroupDrag($event, item)"></div>
                  <div v-for="item in (result && pageData && manualAddMode ? pageData.candidates.filter(candidate => candidate.included !== false && candidateMatchesModificationType(candidate)) : [])" :key="`anchor-${item.id}`" class="manual-weld-anchor" :class="{ selected: selectedId === item.id, dragging: anchorDragState?.id === item.id, 'manual-weld-anchor--recognized': item.origin !== 'manual' }" :style="{ left: `${anchorPoint(item).x}px`, top: `${anchorPoint(item).y}px` }" role="button" tabindex="0" @click.stop="selectCandidate(item)" @pointerdown="startAnchorDrag($event, item)" @pointermove="moveAnchor($event, item)" @pointerup="endAnchorDrag($event, item)" @pointercancel="endAnchorDrag($event, item)"></div>
                  <div v-for="item in (result && pageData ? pageData.candidates : [])" :key="item.id" class="weld-label" :class="markerClass(item)" :style="labelPosition(item)" role="button" tabindex="0" @click.stop="selectCandidate(item)" @dblclick="beginInlineEdit($event, item)" @pointerdown="startLabelDrag($event, item)" @pointermove="moveLabel($event, item)" @pointerup="endLabelDrag($event, item)" @pointercancel="endLabelDrag($event, item)">
                    <input v-if="editingId === item.id" v-model="editingValue" class="inline-weld-input" @pointerdown.stop @dblclick.stop @keydown.enter.prevent="commitInlineEdit(item)" @keydown.esc.prevent="cancelInlineEdit" /><span v-else>{{ item.number || '?' }}</span>
                  </div>
                </div>
                <div v-if="targetPreparation.active" class="canvas-preparation" role="status" aria-live="polite">
                  <v-progress-circular indeterminate color="secondary" size="48" width="4" />
                  <strong>{{ targetPreparation.message }}</strong>
                  <v-progress-linear :model-value="targetPreparationPercent" color="secondary" height="7" rounded striped />
                  <span>{{ Math.round(targetPreparationPercent) }}%</span>
                </div>
                <v-progress-circular v-if="rendering" class="canvas-progress" indeterminate color="secondary" size="36" />
              </div>
            </v-card>

          </div>
        </template>
      </v-container>
    </v-main>

    <v-navigation-drawer data-tour="review" permanent :rail="!reviewDrawerOpen" :rail-width="24" location="right" :width="reviewDrawerWidth" class="review-drawer">
      <button v-if="!reviewDrawerOpen" type="button" class="drawer-edge-toggle drawer-edge-toggle--right" aria-label="打开图纸标识区域" @click="reviewDrawerOpen = true"><span>‹</span></button>
      <v-card v-else class="review-card" elevation="0">
        <v-card-title class="review-title"><span>图纸标识</span></v-card-title>
        <template v-if="result">
          <v-divider />
          <div class="review-scroll pa-3">
            <div data-tour="marker-appearance" class="marker-appearance-card">
              <div class="marker-appearance-tabs" role="tablist" aria-label="分类标识外观">
                <button v-for="group in markerAppearanceGroups" :key="group.key" type="button" role="tab" :aria-selected="markerAppearanceTab === group.key" :class="{ active: markerAppearanceTab === group.key }" @click="markerAppearanceTab = group.key"><i :style="{ backgroundColor: group.style.color }" />{{ group.title.replace('标识', '') }}</button>
              </div>
              <div :key="activeMarkerAppearanceGroup.key" class="marker-appearance-panel" role="tabpanel">
                  <div class="marker-appearance-heading"><span><i :style="{ backgroundColor: activeMarkerAppearanceGroup.style.color }" /><strong>{{ activeMarkerAppearanceGroup.title }}</strong></span></div>
                  <div class="marker-appearance-form">
                  <div class="section-label">分类标识外观</div>
                  <v-select v-model="activeMarkerAppearanceGroup.style.shape" :items="shapeOptions" density="compact" label="外形" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" />
                  <div class="parameter-grid mt-2"><v-text-field v-model.number="activeMarkerAppearanceGroup.style.frameSize" type="number" min="18" max="64" density="compact" label="框尺寸" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /><v-text-field v-model.number="activeMarkerAppearanceGroup.style.fontSize" type="number" min="7" max="24" density="compact" label="字号" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /></div>
                  <div class="parameter-grid mt-2"><v-text-field v-model.number="activeMarkerAppearanceGroup.style.lineWidth" type="number" min="0.5" max="4" step="0.1" density="compact" label="线宽" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /><v-text-field v-model="activeMarkerAppearanceGroup.style.color" type="color" density="compact" label="颜色" @focus="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @blur="commitHistory" /></div>
                  <v-slider v-model="activeMarkerAppearanceGroup.style.fillOpacity" min="0" max="1" step="0.05" color="accent" label="填充透明度" thumb-label class="mt-2" @start="beginHistory(`调整${activeMarkerAppearanceGroup.title}外观`)" @end="commitHistory" />
                  <v-divider class="my-4" />
                  <div class="manual-numbering-settings">
                    <div class="section-label">新增编号规则</div>
                    <div class="parameter-grid"><v-text-field v-model="activeMarkerAppearanceGroup.numbering.prefix" maxlength="20" density="compact" label="前缀" @blur="normalizeManualNumberingInput" /><v-text-field v-model="activeMarkerAppearanceGroup.numbering.suffix" maxlength="20" density="compact" label="后缀" @blur="normalizeManualNumberingInput" /></div>
                    <v-text-field v-model.number="activeMarkerAppearanceGroup.numbering.start" class="mt-2" type="number" min="1" step="1" density="compact" label="起始数字" @blur="normalizeManualNumberingInput" />
                  </div>
                  <v-divider class="my-4" />
                  <div class="section-label">新增标识引线</div>
                  <v-text-field v-model.number="manualLeaderLength" type="number" min="20" max="240" step="4" density="compact" label="引线长度" suffix="图纸单位" />
                  </div>
              </div>
            </div>
            <v-btn data-tour="position-optimization" block variant="tonal" color="secondary" class="mt-2" :disabled="collaborationInteractionBlocked" @click="reflowCurrentPageLabelPositions(true)">重新优化当前页标识位置<v-tooltip activator="parent">仅重新排列当前页标识，并避让文字、管线、焊口锚点、其他标识和引线</v-tooltip></v-btn>
            <v-divider class="my-4" />
            <div v-if="selectedCandidate" data-tour="selected-object">
              <div class="section-label">选中对象</div>
              <v-text-field v-model="selectedCandidate.number" label="编号" @focus="beginHistory('修改编号')" @blur="commitHistory"><v-tooltip activator="parent">修改选中对象的字符串编号</v-tooltip></v-text-field>
              <div v-if="isSpecialMarker(selectedCandidate)" class="marker-appearance-card mt-3">
                <div class="marker-appearance-heading"><span><i :style="{ backgroundColor: selectedCandidate.markerStyle.color }" /><strong>特殊标识独立外观</strong></span><small>仅影响当前选中对象</small></div>
                <div class="marker-appearance-form">
                  <v-select v-model="selectedCandidate.markerStyle.shape" :items="shapeOptions" density="compact" label="外形" @focus="beginHistory('调整特殊标识外观')" @blur="commitHistory" />
                  <div class="parameter-grid mt-2"><v-text-field v-model.number="selectedCandidate.markerStyle.frameSize" type="number" min="18" max="64" density="compact" label="框尺寸" @focus="beginHistory('调整特殊标识外观')" @blur="commitHistory" /><v-text-field v-model.number="selectedCandidate.markerStyle.fontSize" type="number" min="7" max="24" density="compact" label="字号" @focus="beginHistory('调整特殊标识外观')" @blur="commitHistory" /></div>
                  <div class="parameter-grid mt-2"><v-text-field v-model.number="selectedCandidate.markerStyle.lineWidth" type="number" min="0.5" max="4" step="0.1" density="compact" label="线宽" @focus="beginHistory('调整特殊标识外观')" @blur="commitHistory" /><v-text-field v-model="selectedCandidate.markerStyle.color" type="color" density="compact" label="颜色" @focus="beginHistory('调整特殊标识外观')" @blur="commitHistory" /></div>
                  <v-slider v-model="selectedCandidate.markerStyle.fillOpacity" min="0" max="1" step="0.05" color="accent" label="填充透明度" thumb-label class="mt-2" @start="beginHistory('调整特殊标识外观')" @end="commitHistory" />
                </div>
              </div>
              <v-list density="compact" class="evidence-list mt-3"><v-list-item title="证据" :subtitle="selectedCandidate.evidence" /><v-list-item title="置信度" :subtitle="`${Math.round(selectedCandidate.confidence * 100)}%`" /><v-list-item title="参考身份" :subtitle="selectedCandidate.referenceLabel || '未匹配'" /></v-list>
              <v-btn block class="mt-3" :disabled="collaborationInteractionBlocked" :color="selectedCandidate.included === false ? 'secondary' : 'error'" variant="tonal" @click="toggleCandidate(selectedCandidate)">{{ selectedCandidate.included === false ? '恢复这个对象' : '排除这个误识别对象' }}<v-tooltip activator="parent">切换该对象是否参与保存与导出</v-tooltip></v-btn>
            </div>
          </div>
          <v-divider />
          <v-card-actions data-tour="review-actions" class="review-actions"><v-btn size="small" :loading="backendSaveState === 'saving' && !saveAndCloseRunning" :disabled="saveAndCloseRunning" @click="save">{{ result?.jobId === 'tutorial-000207' ? '保存草稿' : backendSaveButtonLabel }}<v-tooltip activator="parent">修改只保留在内存中；切换页时自动保存当前页，也可点击立即保存。教程仅保存浏览器草稿。</v-tooltip></v-btn><v-menu><template #activator="{ props }"><v-btn v-bind="props" size="small" color="primary" :disabled="saveAndCloseRunning">导出<v-tooltip activator="parent">选择导出 CSV 或标识 PDF；AI 训练样本由后端在解析完成后自动生成</v-tooltip></v-btn></template><v-list density="compact"><v-list-item title="导出 CSV" @click="exportCsv" /><v-list-item title="导出标识 PDF" @click="exportPdf" /></v-list></v-menu><v-btn size="small" :loading="saveAndCloseRunning" @click="saveAndClose">保存并关闭<v-tooltip activator="parent">保存当前校对结果，释放页面锁并关闭工作区，然后返回解析任务列表</v-tooltip></v-btn></v-card-actions>
        </template>
        <v-card-text v-else class="review-placeholder">
          <div class="review-placeholder__title">等待识别结果</div>
          <div class="review-placeholder__text">查看对象信息、调整外观、修改编号与导出。</div>
        </v-card-text>
      </v-card>
      <button v-if="reviewDrawerOpen" type="button" class="drawer-edge-toggle drawer-edge-toggle--right" aria-label="折叠图纸标识区域" @click="reviewDrawerOpen = false"><span>›</span></button>
    </v-navigation-drawer>

    <TutorialCatalogDialog :model-value="tutorialCatalogOpen" :tutorials="tutorialCatalog" :loading-id="tutorialLoadingId" @update:model-value="updateTutorialCatalogOpen" @start="startTutorial" />
    <TutorialTour v-model="tutorialOpen" :steps="activeTutorialSteps" :tutorial-title="activeTutorial.title" @step-change="handleTutorialStepChange" @close="finishTutorial" />

    <DraftManagerDialogs
      v-model:recovery-dialog="draftRecoveryDialog"
      v-model:recovery-conflict-dialog="recoveryConflictDecision.dialog.value"
      v-model:selected-recovery-key="selectedRecoveryKey"
      v-model:manager-dialog="draftManagerDialog"
      v-model:batch-delete-dialog="draftBatchDeleteDialog"
      :entries="draftEntries"
      :loading="draftManagerLoading"
      :selected-keys="selectedDraftKeys"
      :selection-mode="draftSelectionMode"
      :all-selected="allDraftsSelected"
      :batch-deleting="draftBatchDeleting"
      :delete-target="draftDeleteTarget"
      :recovery-options="recoveryOptions"
      :recovery-conflict-info="recoveryConflictDecision.info.value"
      :current-target-name="targetPdf?.name || ''"
      @select-recovery="selectRecoveryVersion"
      @discard-recovery="discardWorkspaceDraft"
      @restore-recovery="restoreWorkspaceDraft"
      @resolve-recovery-conflict="recoveryConflictDecision.resolve"
      @toggle-selection-mode="toggleDraftSelectionMode"
      @toggle-all="toggleAllDrafts"
      @toggle-selection="toggleDraftSelection"
      @open-draft="openManagedDraft"
      @request-delete="draftDeleteTarget = $event"
      @delete-selected="deleteSelectedDrafts"
      @clear-delete-target="draftDeleteTarget = null"
      @delete-draft="deleteManagedDraft"
    />

    <AnalysisConfirmationDialogs
      v-model:duplicate="duplicateJobDialog"
      :duplicate-info="duplicateJobInfo"
      @resolve-duplicate="resolveDuplicateJob"
    />

    <AnalysisQueueDialog
      v-model="analysisQueueDialog"
      v-model:delete-dialog="analysisQueueDeleteDialog"
      :tab="analysisQueueTab"
      :loading="analysisQueueLoading"
      :queue="analysisQueue"
      :jobs="visibleAnalysisQueueJobs"
      :action-id="analysisQueueActionId"
      :cancelling-job-ids="analysisQueueCancellingJobIds"
      :delete-target="analysisQueueDeleteTarget"
      :current-job-id="result?.jobId || ''"
      @refresh="refreshAnalysisQueue"
      @update:tab="selectAnalysisQueueTab"
      @change-page="changeAnalysisQueuePage"
      @cancel="cancelQueueJob"
      @move="moveQueueJob"
      @archive="setQueueJobArchived"
      @restore="restoreQueueJob"
      @delete-request="requestDeleteQueueJob"
      @delete-confirm="confirmDeleteQueueJob"
    />

    <SystemSettingsDialog
      v-model="systemSettingsDialog"
      v-model:left-drawer="leftDrawerOpen"
      v-model:review-drawer="reviewDrawerOpen"
      v-model:tooltips="tooltipsEnabled"
      v-model:operation-messages="operationMessagesEnabled"
      v-model:error-messages="errorMessagesEnabled"
      v-model:page-button-count="pageButtonsPerGroup"
      v-model:clear-references="clearReferencesOnDesignUpload"
      v-model:auto-reference-window="autoReferenceWindow"
      v-model:reference-hint-location="referenceHintLocation"
      v-model:performance-mode="canvasPerformanceMode"
      v-model:appearance-tab="settingsAppearanceTab"
      v-model:manual-leader-length="defaultManualLeaderLength"
      v-model:theme-preference="themePreference"
      :theme-options="themeOptions"
      :canvas-performance-options="canvasPerformanceOptions"
      :canvas-performance-profile="canvasPerformanceProfile"
      :marker-appearance-groups="defaultMarkerAppearanceGroups"
      :active-appearance-group="activeSettingsAppearanceGroup"
      :shape-options="shapeOptions"
      :shortcut-rows="shortcutSettingRows"
      :shortcut-editors="shortcutEditors"
      :shortcut-modifier-options="shortcutModifierOptions"
      :shortcut-conflict="shortcutConflict"
      @open-drafts="openDraftManager"
      @update-shortcut-modifier="updateShortcutModifier"
      @capture-shortcut-key="captureShortcutKey"
      @restore-default-shortcuts="restoreDefaultShortcuts"
      @reference-hint-location-change="setReferenceHintLocation"
      @auto-reference-window-change="setAutoReferenceWindow"
      @normalize-numbering-defaults="normalizeDefaultManualNumberingInput"
      @restore-system-defaults="restoreSystemDefaults"
    />
    <SymbolConfigDialog
      v-model="symbolConfigDialog"
      :config="weldSymbolConfig"
      :projects="recognitionProjects"
      :active-project-id="activeRecognitionProjectId"
      :reference-files="referencePdfs"
      @switch-project="switchRecognitionProject"
      @switch-rule="switchReferenceRule"
      @assign-reference-rule="assignReferenceRule"
    />

    </template>
  </v-app>
</template>

<style scoped>
.auth-loading { min-height: 100vh; display: flex; align-items: center; justify-content: center; gap: 14px; color: #173e57; }
.workspace-readonly :deep(.canvas-surface) { pointer-events: none; filter: saturate(.65); }
</style>
