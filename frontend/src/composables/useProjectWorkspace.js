import { nextTick } from 'vue'

import { api } from '../api.js'
import { readEditablePdfAttachments } from '../pdfWorkspace.js'
import { loadPdfDocument } from '../services/pdfDocument.js'
import { deleteDraft, fileFingerprint, loadDraft, loadDraftPage } from '../workspaceStorage.js'
import { restoreWorkspaceFile } from '../workspaceFile.js'

export function normalizeFiles(value) {
  if (!value) return []
  return Array.isArray(value) ? value.filter(Boolean) : [value]
}

export function useProjectWorkspace({ state, operations }) {
  const {
    projectMode, projectPdfs, projectResults, activeProjectIndex, targetPdf, targetDocument,
    startPage, endPage, result, activeFileFingerprint, pendingDraft, recoveryOptions,
    selectedRecoveryKey, draftRecoveryDialog, targetPreparation, referenceResearchReady,
    selectedId, swapSourceId, previewPage, currentPage, tutorialSessionActive,
    tutorialSampleLoading, referenceMode, referenceProjectMode, referencePdfs,
    activeReferenceIndex, referencePdf, unavailableReferenceFiles, pcfFiles, selectedPcfFolder,
    useReferenceNumber, hydratingWorkspace, leftPanelTab,
    markerStyle, error,
  } = state
  let targetLoadToken = 0
  let pageChangeGeneration = 0

  function clearProject() {
    operations.cancelReferenceArchiving(); pageChangeGeneration += 1; operations.invalidateCanvas(); targetLoadToken += 1
    targetDocument.value?.destroy?.(); targetDocument.value = null; projectPdfs.value = []; projectResults.value = []
    activeProjectIndex.value = 0; targetPdf.value = null; result.value = null; activeFileFingerprint.value = ''
    pendingDraft.value = null; recoveryOptions.value = []; selectedRecoveryKey.value = ''; draftRecoveryDialog.value = false
    targetPreparation.value = { ...targetPreparation.value, active: false }
    operations.clearHistory(); referenceResearchReady.value = false; selectedId.value = ''; swapSourceId.value = ''; previewPage.value = 1
    operations.dismissError(); operations.clearReferenceDisplay()
  }

  function changeProjectMode(mode) {
    tutorialSessionActive.value = false; projectMode.value = mode; startPage.value = 1; endPage.value = ''; clearProject()
  }

  async function setProjectFiles(value) {
    tutorialSessionActive.value = false
    const rawFiles = normalizeFiles(value)
    const pdfs = rawFiles.filter(file => file.name?.toLowerCase().endsWith('.pdf'))
      .sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name, 'zh-CN'))
    clearProject()
    if (!rawFiles.length) return
    if (!pdfs.length) { error.value = '所选内容中没有 PDF 文件。'; return }
    if (operations.clearReferencesOnDesignUpload.value) operations.clearReferenceFiles()
    projectPdfs.value = pdfs; projectResults.value = new Array(pdfs.length).fill(null); targetPdf.value = pdfs[0]
    await loadTargetPdf(pdfs[0], true)
  }

  async function loadTutorialSample() {
    tutorialSampleLoading.value = true
    try {
      const [sample, tutorialResult] = await Promise.all([api.getTutorialFile(), api.getTutorialSession()])
      const tutorialReferences = await api.getJobReferenceManifest(tutorialResult.jobId)
      projectMode.value = 'single'; referenceMode.value = 'pdf'; referenceProjectMode.value = 'single'
      clearProject(); operations.clearReferenceFiles(); projectPdfs.value = [sample]; projectResults.value = [tutorialResult]; targetPdf.value = sample
      referencePdfs.value = tutorialReferences; activeReferenceIndex.value = tutorialReferences.length ? 0 : -1; referencePdf.value = tutorialReferences[0] || null
      const tutorialStartPage = tutorialResult.pages?.[0]?.page || tutorialResult.analyzedRange?.[0] || 1
      result.value = operations.cloneValue(tutorialResult); operations.restoreMarkerAppearances({ result: tutorialResult })
      currentPage.value = tutorialStartPage; previewPage.value = currentPage.value; projectResults.value[0] = result.value
      await nextTick()
      let targetLoaded = await loadTargetPdf(sample, true, false, tutorialStartPage)
      if (!targetLoaded && targetPdf.value === sample) {
        await nextTick()
        targetLoaded = await loadTargetPdf(sample, true, false, tutorialStartPage)
      }
      if (!targetLoaded || !targetDocument.value) throw new Error(error.value || '教程 PDF 加载失败，请重新打开操作教程')
      referenceResearchReady.value = true; tutorialSessionActive.value = true; await nextTick()
      if (referencePdf.value) await operations.syncReferenceForPage(currentPage.value, true)
      leftPanelTab.value = 'input'; operations.showNotice('000207 教程示例及预解析结果已载入，可直接校对和导出。')
    } catch (cause) {
      const detail = String(cause?.message || cause).replace(/^PDF 加载失败：/, '')
      error.value = `载入教程示例失败：${detail}`
    }
    finally { tutorialSampleLoading.value = false }
  }

  async function loadTargetPdf(inputFile, fitAfter = true, checkRecovery = true, initialPage = 1) {
    const token = ++targetLoadToken
    const requestedFile = inputFile
    const file = await operations.materializeStoredFile(inputFile)
    if (token !== targetLoadToken || !file) return false
    if (targetPdf.value === requestedFile) targetPdf.value = file
    const projectFileIndex = projectPdfs.value.indexOf(requestedFile)
    if (projectFileIndex >= 0) projectPdfs.value[projectFileIndex] = file
    operations.invalidateCanvas(); const previousDocument = targetDocument.value; targetDocument.value = null; previousDocument?.destroy?.()
    if (checkRecovery) targetPreparation.value = { active: true, completed: 0, total: 4, message: '正在读取 PDF 并检查可恢复版本' }
    const markStep = () => {
      if (!checkRecovery || token !== targetLoadToken) return
      const completed = Math.min(targetPreparation.value.total, targetPreparation.value.completed + 1)
      targetPreparation.value = { ...targetPreparation.value, completed, message: completed === targetPreparation.value.total ? '恢复检查完成' : `正在检查可恢复版本（${completed}/${targetPreparation.value.total}）` }
    }
    try {
      const documentPromise = loadPdfDocument(file).then(document => { markStep(); return document })
      const fingerprintPromise = fileFingerprint(file).then(async fingerprint => ({ fingerprint, draft: checkRecovery ? await loadDraft(fingerprint).catch(() => null) : null })).then(value => { markStep(); return value })
      const recentPromise = checkRecovery ? api.getRecentBatch().catch(() => null).then(value => { markStep(); return value }) : Promise.resolve(null)
      const document = await documentPromise
      if (token !== targetLoadToken) { document.destroy?.(); return false }
      const embeddedPromise = checkRecovery ? readEditablePdfAttachments(document).then(({ embedded, sourceContent }) => ({
        embedded,
        sourceFile: embedded && sourceContent ? new File([sourceContent], embedded.sourceFileName || file.name, { type: 'application/pdf', lastModified: file.lastModified }) : null,
      })).catch(() => null).then(value => { markStep(); return value }) : Promise.resolve(null)
      const [{ fingerprint, draft }, embeddedAssets, recent] = await Promise.all([fingerprintPromise, embeddedPromise, recentPromise])
      const embedded = embeddedAssets?.embedded || null
      if (token !== targetLoadToken) { document.destroy?.(); return false }
      targetDocument.value = document; activeFileFingerprint.value = fingerprint
      previewPage.value = Math.max(1, Math.min(document.numPages, Number(initialPage) || 1)); await nextTick()
      if (!checkRecovery) { await operations.renderCanvas(fitAfter); return true }
      const options = []
      if (embedded) options.push({ key: 'embedded', title: 'PDF 内嵌编辑数据', source: 'embedded', sourceFile: embeddedAssets?.sourceFile || null, priority: 1, payload: { result: embedded, markerStyles: embedded.markerStyles, markerStyle: embedded.markerStyle || markerStyle.value, componentMarkerStyles: embedded.componentMarkerStyles, currentPage: embedded.analyzedRange?.[0] || 1 }, savedAt: '随当前 PDF 保存的可编辑版本' })
      if (draft?.payload?.result?.pages?.length) options.push({ ...draft, key: 'indexeddb', title: '浏览器自动草稿', source: 'indexeddb', priority: 2 })
      const recentJobs = Array.isArray(recent?.jobs) ? recent.jobs.filter(item => item?.pages?.length) : []
      const byName = new Map(recentJobs.filter(item => item.originalTargetName).map(item => [item.originalTargetName, item]))
      const restoredProjects = projectPdfs.value.map(projectFile => byName.get(projectFile.name) || null)
      const preferredIndex = restoredProjects[activeProjectIndex.value] ? activeProjectIndex.value : restoredProjects.findIndex(Boolean)
      if (preferredIndex >= 0 && restoredProjects[preferredIndex]) {
        const restoredResult = restoredProjects[preferredIndex]
        options.push({ key: 'recent-batch', title: '后端最近解析批次', source: 'recent-batch', savedAt: '后端最近解析批次', priority: 3, batchId: recent.batchId || null,
          payload: { result: restoredResult, projectResults: restoredProjects, activeProjectIndex: preferredIndex, markerStyles: restoredResult.markerStyles, markerStyle: restoredResult.markerStyle || markerStyle.value, componentMarkerStyles: restoredResult.componentMarkerStyles, currentPage: restoredResult.analyzedRange?.[0] || restoredResult.pages?.[0]?.page || 1 } })
      }
      if (options.length) {
        recoveryOptions.value = options.sort((a, b) => a.priority - b.priority); operations.selectRecoveryVersion(recoveryOptions.value[0].key)
        draftRecoveryDialog.value = true; targetPreparation.value = { ...targetPreparation.value, active: true, completed: 4, message: '发现可恢复版本，请先选择' }
      } else {
        targetPreparation.value = { ...targetPreparation.value, message: '正在渲染 PDF 页面' }; await operations.renderCanvas(fitAfter)
        if (token === targetLoadToken) targetPreparation.value = { ...targetPreparation.value, active: false }
      }
      return token === targetLoadToken && Boolean(targetDocument.value)
    } catch (cause) {
      if (token === targetLoadToken) { targetPreparation.value = { ...targetPreparation.value, active: false }; error.value = `PDF 加载失败：${cause.message || cause}` }
      return false
    }
  }

  async function restoreWorkspaceDraft() {
    const recovery = pendingDraft.value; const payload = recovery?.payload
    if (!payload?.result) return
    hydratingWorkspace.value = true; const loadedTargetFile = targetPdf.value
    const needsReconciliation = recovery?.source === 'indexeddb' || recovery?.source === 'embedded'
    let reconciliation = null
    if (needsReconciliation && operations.reconcileRecovery) {
      reconciliation = await operations.reconcileRecovery(recovery)
      if (reconciliation?.choice === 'cancel') {
        hydratingWorkspace.value = false
        targetPreparation.value = { ...targetPreparation.value, active: false }
        return false
      }
    }
    if (recovery?.sourceFile) { targetPdf.value = recovery.sourceFile; projectPdfs.value[activeProjectIndex.value] = recovery.sourceFile }
    targetPreparation.value = { ...targetPreparation.value, active: true, message: '正在恢复工作区并渲染页面' }
    if (Array.isArray(payload.projectResults)) { projectResults.value = operations.cloneValue(payload.projectResults); activeProjectIndex.value = Number(payload.activeProjectIndex) || 0; targetPdf.value = projectPdfs.value[activeProjectIndex.value] || targetPdf.value }
    result.value = operations.cloneValue(payload.result); if (payload.workspaceDraftSchema) result.value.workspaceDraftSchema = payload.workspaceDraftSchema
    if (reconciliation?.localByPage) {
      const keepLocal = new Set([
        ...(reconciliation.safePages || []),
        ...(reconciliation.unavailablePages || []),
        ...(reconciliation.choice === 'draft' ? (reconciliation.conflictPages || []) : []),
      ])
      result.value.pages = (result.value.pages || []).map(page => keepLocal.has(Number(page.page))
        ? operations.cloneValue(reconciliation.localByPage.get(Number(page.page)) || page)
        : page)
    }
    if (reconciliation?.choice === 'server') {
      const conflicts = new Set(reconciliation.conflictPages || [])
      result.value.pages = (result.value.pages || []).map(page => conflicts.has(Number(page.page))
        ? operations.cloneValue(reconciliation.serverByPage.get(Number(page.page)))
        : page)
    }
    if (needsReconciliation) {
      const dirtyPages = reconciliation
        ? [...(reconciliation.safePages || []), ...(reconciliation.unavailablePages || []), ...(reconciliation.choice === 'draft' ? (reconciliation.conflictPages || []) : [])]
        : (result.value.pages || []).map(page => Number(page.page)).filter(Number.isFinite)
      operations.markRecoveredPagesDirty?.(dirtyPages)
    }
    operations.restoreProjectSettings?.(payload)
    operations.restoreMarkerAppearances(payload)
    if (payload.referenceMode) referenceMode.value = payload.referenceMode; else if ((result.value.referenceFiles || []).length) referenceMode.value = 'pdf'
    if (payload.referenceProjectMode) referenceProjectMode.value = payload.referenceProjectMode; else if (String(result.value.referenceMode || '').startsWith('reference-pdf-folder')) referenceProjectMode.value = 'folder'
    const restoredReferences = (payload.referenceFiles || []).map(restoreWorkspaceFile).filter(Boolean)
    const restoredPcfs = (await Promise.all((payload.pcfFiles || []).map(operations.materializeStoredFile))).filter(Boolean)
    const expectedReferences = Array.isArray(result.value.referenceFiles) ? result.value.referenceFiles.filter(Boolean) : []
    let recoveredReferences = restoredReferences
    if (!recoveredReferences.length && expectedReferences.length && result.value.jobId) recoveredReferences = await api.getJobReferenceManifest(result.value.jobId).catch(() => [])
    referencePdfs.value = recoveredReferences
    const recoveredNames = new Set(recoveredReferences.map(file => file.name)); unavailableReferenceFiles.value = expectedReferences.filter(name => !recoveredNames.has(name))
    if (restoredPcfs.length) pcfFiles.value = restoredPcfs
    selectedPcfFolder.value = payload.selectedPcfFolder || selectedPcfFolder.value; useReferenceNumber.value = payload.useReferenceNumber ?? useReferenceNumber.value
    referenceResearchReady.value = payload.referenceResearchReady ?? true; activeReferenceIndex.value = Math.max(0, Math.min(Number(payload.activeReferenceIndex) || 0, Math.max(0, referencePdfs.value.length - 1)))
    operations.setActiveReferencePage(Math.max(1, Number(payload.activeReferencePage) || 1)); referencePdf.value = referencePdfs.value[activeReferenceIndex.value] || null
    currentPage.value = Number(payload.currentPage) || result.value.analyzedRange?.[0] || 1; projectResults.value[activeProjectIndex.value] = result.value
    pendingDraft.value = null; recoveryOptions.value = []; selectedRecoveryKey.value = ''; draftRecoveryDialog.value = false; operations.clearHistory()
    operations.showNotice(unavailableReferenceFiles.value.length ? `已恢复标识数据；原对照 PDF 无法自动取回，请重新选择：${unavailableReferenceFiles.value.join('、')}` : expectedReferences.length ? '已恢复可编辑标识数据及对照 PDF，可继续校对、移动和导出。' : '已恢复可编辑标识数据，可继续校对、移动和导出。')
    operations.logAudit(recovery?.source === 'recent-batch' ? 'batch.recent_restored' : 'draft.restored', { batchId: recovery?.batchId || null }); await nextTick()
    try {
      if (recovery?.source === 'recent-batch' || targetPdf.value !== loadedTargetFile) await loadTargetPdf(targetPdf.value, true, false)
      if (referenceMode.value === 'pdf' && referencePdfs.value.length) { if (referenceProjectMode.value === 'folder' || operations.matchedReference(currentPage.value)) await operations.syncReferenceForPage(currentPage.value, true); else await operations.loadReferencePdf(referencePdf.value, true); void operations.archiveLazyReferences() }
      else if (recovery?.source !== 'recent-batch' && targetPdf.value === loadedTargetFile) await operations.renderCanvas(true)
      if (reconciliation?.choice === 'draft' && reconciliation.conflictPages?.length) {
        await operations.overwriteRecoveredPages?.(reconciliation.conflictPages, reconciliation.unavailablePages || [])
      }
      if (needsReconciliation && !reconciliation?.unavailablePages?.includes(Number(currentPage.value))) {
        await operations.activateRecoveredPage?.()
      }
    } finally { await nextTick(); hydratingWorkspace.value = false; targetPreparation.value = { ...targetPreparation.value, active: false } }
    return true
  }

  async function discardWorkspaceDraft() {
    if (activeFileFingerprint.value && pendingDraft.value?.source === 'indexeddb') await deleteDraft(activeFileFingerprint.value).catch(() => {})
    pendingDraft.value = null; recoveryOptions.value = []; selectedRecoveryKey.value = ''; draftRecoveryDialog.value = false
    operations.logAudit('draft.discarded'); targetPreparation.value = { ...targetPreparation.value, active: true, message: '正在渲染 PDF 页面' }
    try { await operations.renderCanvas(true) } finally { targetPreparation.value = { ...targetPreparation.value, active: false } }
  }

  async function selectProject(index) {
    pageChangeGeneration += 1; activeProjectIndex.value = index; targetPdf.value = projectPdfs.value[index] || null; result.value = projectResults.value[index] || null
    currentPage.value = result.value?.analyzedRange?.[0] || 1; previewPage.value = 1; selectedId.value = ''; swapSourceId.value = ''
    operations.clearReferenceDisplay(); await loadTargetPdf(targetPdf.value, true, !result.value)
    if (result.value && referenceMode.value === 'pdf') await operations.syncReferenceForPage(currentPage.value, true)
  }

  async function changePage(pageNumber, { preserveLocal = false } = {}) {
    const previousHydration = hydratingWorkspace.value
    hydratingWorkspace.value = true
    try {
      const generation = ++pageChangeGeneration; const requested = result.value?.pages?.find(item => item.page === pageNumber)
      if (result.value) currentPage.value = pageNumber; else previewPage.value = Math.max(1, Math.min(operations.targetPageCount.value, pageNumber))
      selectedId.value = ''
      if (result.value && referenceMode.value === 'pdf') await operations.syncReferenceForPage(currentPage.value, true); else await operations.renderCanvas(true)
      if (generation !== pageChangeGeneration) return
      if (!preserveLocal && result.value?.jobId && requested && requested.detailsLoaded === false) {
        try {
          const isServerJob = result.value.jobId && result.value.jobId !== 'tutorial-000207'
          const localPage = !isServerJob && activeFileFingerprint.value ? await loadDraftPage(activeFileFingerprint.value, activeProjectIndex.value, pageNumber).catch(() => null) : null
          if (localPage) Object.assign(requested, localPage, { detailsLoaded: true })
          else { const hydrated = await api.getJobPage(result.value.jobId, pageNumber); Object.assign(requested, hydrated.page || hydrated, { detailsLoaded: true }) }
        } catch { /* light page remains usable */ }
        if (generation !== pageChangeGeneration) return
        if (referenceMode.value === 'pdf') await operations.syncReferenceForPage(pageNumber, false); else await operations.renderCanvas(false)
      }
    } finally {
      hydratingWorkspace.value = previousHydration
    }
  }

  function releasePageDetails(keepPage, protectedPages = []) {
    if (!result.value?.jobId || result.value.jobId === 'tutorial-000207') return
    const protectedSet = new Set([Number(keepPage), ...protectedPages].map(Number))
    for (const page of result.value.pages || []) {
      if (page.detailsLoaded === false || protectedSet.has(Number(page.page))) continue
      const included = (page.candidates || []).filter(candidate => candidate.included !== false
        && candidate.componentKind !== 'design-component'
        && candidate.componentKind !== 'special-marker'
        && candidate.componentType !== 'special'
        && candidate.specialMarker !== true)
      const unmatched = included.filter(candidate => !candidate.referenceMatched || !String(candidate.referenceLabel || '').trim()).length
      const unresolved = Math.max(0, Number(page.reference?.unresolvedCalloutGap) || 0)
      page.candidateCount = (page.candidates || []).length
      page.matchSummary = {
        matched: included.length - unmatched,
        unmatched,
        unresolved,
        complete: included.length > 0 && unmatched === 0 && unresolved === 0,
      }
      page.candidates = []
      delete page.layoutObstacles
      delete page.designComponents
      page.detailsLoaded = false
    }
  }

  function dispose() { targetLoadToken += 1; pageChangeGeneration += 1 }
  return { normalizeFiles, clearProject, changeProjectMode, setProjectFiles, loadTutorialSample, loadTargetPdf, restoreWorkspaceDraft, discardWorkspaceDraft, selectProject, changePage, releasePageDetails, dispose }
}
