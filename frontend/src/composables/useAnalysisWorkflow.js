import { nextTick, ref } from 'vue'

import { api } from '../api.js'
import { resultHasMissingNumbers } from '../numbering.js'
import { ANALYSIS_POLL_INTERVAL_MS } from '../polling.js'
import { uploadJobInputs } from '../services/jobUpload.js'

const initialProgress = () => ({
  fileIndex: 0,
  fileCount: 1,
  fileName: '',
  phase: 'preparing',
  transferPercent: 0,
  transferLoaded: 0,
  transferTotal: 0,
  completedPages: 0,
  totalPages: 1,
  completedReferenceFiles: 0,
  totalReferenceFiles: 0,
  progressCompletedUnits: 0,
  progressTotalUnits: 1,
  progressPercent: 0,
  message: '准备图元研究',
})

const wait = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds))

export function useAnalysisWorkflow({ workspace, operations }) {
  const loading = ref(false)
  const enqueueing = ref(false)
  const enqueueProgress = ref({ percent: 0, message: '正在准备上传' })
  const progress = ref(initialProgress())
  const activeJobId = ref('')
  const cancelling = ref(false)
  const duplicateDialog = ref(false)
  const duplicateInfo = ref(null)
  let duplicateResolver

  const {
    targetPdf, projectPdfs, projectResults, activeProjectIndex, projectMode,
    startPage, endPage, referenceMode, referencePdfs, pcfFiles, selectedPcfFolder,
    weldSymbolConfig, activeRecognitionProject, useReferenceNumber, startNumber, numberPrefix,
    numberSuffix, result, referenceResearchReady, currentPage, selectedId, swapSourceId,
    undoStack, redoStack, uploadCommitPending, error,
  } = workspace

  function confirmDuplicate(job, fileName) {
    duplicateInfo.value = { ...job, fileName }
    duplicateDialog.value = true
    return new Promise(resolve => { duplicateResolver = resolve })
  }

  function resolveDuplicate(choice) {
    duplicateDialog.value = false
    duplicateInfo.value = null
    const resolve = duplicateResolver
    duplicateResolver = null
    resolve?.(choice)
  }

  async function waitForJob(initialJob, onUpdate) {
    let snapshot = initialJob
    await onUpdate(snapshot)
    while (snapshot.status === 'processing' || snapshot.status === 'cancelling') {
      await wait(ANALYSIS_POLL_INTERVAL_MS)
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

  async function cancel() {
    if (!activeJobId.value || cancelling.value) return
    cancelling.value = true
    try {
      await api.cancelJob(activeJobId.value)
      progress.value = { ...progress.value, message: '正在安全取消解析任务' }
      operations.logAudit('analysis.cancel_requested', { jobId: activeJobId.value })
    } catch (cause) {
      error.value = cause.message || String(cause)
      cancelling.value = false
    }
  }

  async function prioritize(job, fileName) {
    if (!job?.jobId || job.status !== 'processing') return null
    try {
      const priority = await api.prioritizeQueuedJob(job.jobId)
      if (priority.queueState === 'queued' && Number(priority.previousQueuePosition) > 1) {
        const overtaken = Number(priority.previousQueuePosition) - 1
        const message = `检测到“${fileName}”前方有 ${overtaken} 个等待任务，已将本次智能编号提升到等待队列首位；正在执行的任务不受影响。`
        progress.value = { ...progress.value, message }
        operations.showNotice(message)
      } else if (priority.queueState === 'queued') {
        progress.value = { ...progress.value, message: '本次智能编号已位于等待队列首位，正在执行的任务完成后将优先开始。' }
      }
      return priority
    } catch (cause) {
      progress.value = { ...progress.value, message: `任务已创建，自动提升优先级失败，将按当前队列等待：${cause.message || cause}` }
      return null
    }
  }

  function jobPayload(uploads, file, batchId, batchIndex, queuedForReview = false) {
    return {
      ...uploads,
      originalTargetName: file.name,
      batchId,
      batchIndex,
      startPage: projectMode.value === 'single' ? Number(startPage.value) || 1 : 1,
      endPage: projectMode.value === 'single' && endPage.value ? Number(endPage.value) : null,
      pcfFolder: referenceMode.value === 'pdf' ? selectedPcfFolder.value || null : null,
      project: {
        id: activeRecognitionProject.value.id,
        name: activeRecognitionProject.value.name,
      },
      symbolConfig: { ...weldSymbolConfig.value, detectionMode: 'placement' },
      numberingConfig: {
        useReferenceNumber: useReferenceNumber.value,
        startNumber: Math.max(1, Number(startNumber.value) || 1),
        prefix: numberPrefix.value,
        suffix: numberSuffix.value,
      },
      ...(queuedForReview ? { queuedForReview: true } : {}),
    }
  }

  async function materializeInputs() {
    const referenceFiles = referenceMode.value === 'pdf'
      ? await Promise.all(referencePdfs.value.map(file => operations.materializeReferenceFile(file)))
      : []
    return {
      referenceFiles,
      sourcePcfs: referenceMode.value === 'pdf' ? pcfFiles.value : [],
    }
  }

  async function enqueue() {
    if (!targetPdf.value || enqueueing.value) {
      if (!targetPdf.value) error.value = '请先选择待解析的 ISO PDF 或工程文件夹。'
      return
    }
    enqueueing.value = true
    enqueueProgress.value = { percent: 0, message: '正在读取待上传文件' }
    operations.dismissError()
    operations.clearNotice()
    try {
      const files = projectPdfs.value.length ? projectPdfs.value : [targetPdf.value]
      const { referenceFiles, sourcePcfs } = await materializeInputs()
      const batchId = globalThis.crypto?.randomUUID?.() || `queue-${Date.now()}`
      let queued = 0
      let alreadyAvailable = 0
      for (let index = 0; index < files.length; index += 1) {
        uploadCommitPending.value = true
        const uploads = await uploadJobInputs(files[index], referenceFiles, sourcePcfs, ({ loaded, total, fileName, awaitingServer }) => {
          const uploadFraction = loaded / Math.max(1, total)
          enqueueProgress.value = {
            percent: 100 * (index + uploadFraction * .9) / files.length,
            message: awaitingServer
              ? `“${fileName}”已发送，服务器正在落盘校验`
              : `正在上传“${fileName}” ${Math.round(uploadFraction * 100)}%`,
          }
        })
        enqueueProgress.value = { percent: 100 * (index + .9) / files.length, message: `服务器正在创建任务 ${index + 1}/${files.length}` }
        const created = await api.createJob(jobPayload(uploads, files[index], batchId, index, true))
        uploadCommitPending.value = false
        if (created.status === 'complete') alreadyAvailable += 1
        else queued += 1
        enqueueProgress.value = { percent: 100 * (index + 1) / files.length, message: `服务器已接收任务 ${index + 1}/${files.length}` }
      }
      await operations.refreshQueue(false)
      operations.showNotice(`${queued ? `已将 ${queued} 份图纸推入解析队列` : '没有新增等待任务'}${alreadyAvailable ? `；${alreadyAvailable} 份已有可恢复结果` : ''}。`)
    } catch (cause) {
      error.value = `推入解析队列失败：${cause.message || cause}`
    } finally {
      uploadCommitPending.value = false
      enqueueing.value = false
    }
  }

  async function analyze() {
    if (!targetPdf.value) {
      error.value = '请先选择待标识 ISO PDF 或工程文件夹。'
      return
    }
    loading.value = true
    operations.dismissError()
    operations.clearNotice()
    referenceResearchReady.value = false
    operations.clearReferenceDisplay()
    progress.value = {
      ...initialProgress(),
      fileCount: projectPdfs.value.length || 1,
      fileName: targetPdf.value.name,
      totalReferenceFiles: referencePdfs.value.length,
      progressTotalUnits: Math.max(1, referencePdfs.value.length + 1),
      message: '正在准备图元研究',
    }
    try {
      const files = projectPdfs.value.length ? projectPdfs.value : [targetPdf.value]
      const stagedManualPages = files.map((_, index) => {
        const currentWorkspace = index === activeProjectIndex.value ? result.value : projectResults.value[index]
        return operations.cloneValue((currentWorkspace?.pages || []).map(page => ({
          page: page.page,
          candidates: (page.candidates || []).filter(item => item.origin === 'manual'),
        })))
      })
      const { referenceFiles, sourcePcfs } = await materializeInputs()
      const analyzed = new Array(files.length).fill(null)
      const batchId = globalThis.crypto?.randomUUID?.() || `batch-${Date.now()}`
      const pageStartNumber = Math.max(1, Number(startNumber.value) || 1)
      for (let index = 0; index < files.length; index += 1) {
        uploadCommitPending.value = true
        progress.value = {
          ...initialProgress(), fileIndex: index, fileCount: files.length, fileName: files[index].name,
          phase: 'uploading', totalReferenceFiles: referenceFiles.length,
          progressTotalUnits: Math.max(1, referenceFiles.length + 1), message: '正在准备上传文件',
        }
        const uploads = await uploadJobInputs(files[index], referenceFiles, sourcePcfs, ({ loaded, total, fileName, fileIndex, fileCount, awaitingServer }) => {
          const percent = Math.max(0, Math.min(100, loaded / Math.max(1, total) * 100))
          progress.value = {
            ...progress.value, phase: 'uploading', transferPercent: percent, transferLoaded: loaded, transferTotal: total,
            message: awaitingServer
              ? `“${fileName}”已发送，服务器正在落盘校验（${fileIndex + 1}/${fileCount}）`
              : `正在上传“${fileName}”（${fileIndex + 1}/${fileCount}）`,
          }
        })
        progress.value = { ...progress.value, phase: 'server-validation', transferPercent: 100, message: '文件上传完成，服务器正在校验并创建任务' }
        const payload = jobPayload(uploads, files[index], batchId, index)
        let createdJob = await api.createJob(payload)
        uploadCommitPending.value = false
        if (createdJob.requiresReanalysisConfirmation) {
          const choice = await confirmDuplicate(createdJob, files[index].name)
          if (choice === 'cancel') {
            const cause = new Error('已取消本次解析操作')
            cause.name = 'AnalysisDialogCancelled'
            throw cause
          }
          if (choice === 'reanalyze') {
            uploadCommitPending.value = true
            createdJob = await api.createJob({ ...payload, forceReanalyze: true })
            uploadCommitPending.value = false
          }
        }
        const interactivePriority = await prioritize(createdJob, files[index].name)
        activeJobId.value = createdJob.jobId
        const preserveExistingResult = createdJob.reusedExisting && createdJob.status === 'complete'
        progress.value = {
          ...progress.value,
          phase: 'analysis',
          totalPages: createdJob.totalPages || 1,
          completedReferenceFiles: createdJob.completedReferenceFiles || 0,
          totalReferenceFiles: createdJob.totalReferenceFiles ?? referenceFiles.length,
          progressCompletedUnits: createdJob.progressCompletedUnits || 0,
          progressTotalUnits: createdJob.progressTotalUnits || Math.max(1, referenceFiles.length + (createdJob.totalPages || 1)),
          progressPercent: createdJob.progressPercent ?? 15,
          message: interactivePriority?.queueState === 'queued' && Number(interactivePriority.previousQueuePosition) > 1
            ? `已从等待队列第 ${interactivePriority.previousQueuePosition} 位提升到首位，正在执行的任务不受影响`
            : interactivePriority?.queueState === 'queued'
              ? '本次智能编号已位于等待队列首位，等待可用解析资源'
              : createdJob.reusedExisting && createdJob.status === 'processing'
                ? '已接入后端正在解析的相同任务'
                : createdJob.progressMessage || '任务已创建',
        }
        let publishedPageCount = 0
        analyzed[index] = await waitForJob(createdJob, async snapshot => {
          operations.mergeStagedManualCandidates(snapshot, stagedManualPages[index])
          const missingNumbers = resultHasMissingNumbers(snapshot)
          if (!preserveExistingResult || missingNumbers) {
            operations.numberResult(snapshot, pageStartNumber)
          }
          analyzed[index] = snapshot
          projectResults.value = analyzed.slice()
          const nextPageCount = snapshot.pages?.length || 0
          progress.value = {
            fileIndex: index, fileCount: files.length, fileName: files[index].name, phase: 'analysis', transferPercent: 100,
            transferLoaded: progress.value.transferTotal, transferTotal: progress.value.transferTotal,
            completedPages: snapshot.completedPages ?? nextPageCount,
            totalPages: snapshot.totalPages || createdJob.totalPages || Math.max(1, nextPageCount),
            completedReferenceFiles: snapshot.completedReferenceFiles || 0,
            totalReferenceFiles: snapshot.totalReferenceFiles ?? referenceFiles.length,
            progressCompletedUnits: snapshot.progressCompletedUnits ?? nextPageCount,
            progressTotalUnits: snapshot.progressTotalUnits || Math.max(1, referenceFiles.length + (snapshot.totalPages || createdJob.totalPages || Math.max(1, nextPageCount))),
            progressPercent: snapshot.progressPercent ?? 15,
            message: snapshot.progressMessage || '正在进行图元研究',
          }
          if (activeProjectIndex.value === index) {
            result.value = snapshot
            if (nextPageCount > 0) {
              referenceResearchReady.value = true
              if (!publishedPageCount) currentPage.value = snapshot.pages[0].page
              await nextTick()
              if (!publishedPageCount) await operations.renderCurrentPage()
            }
          }
          publishedPageCount = nextPageCount
        })
        activeJobId.value = ''
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
      progress.value = {
        ...progress.value, fileIndex: files.length - 1, completedPages: progress.value.totalPages,
        completedReferenceFiles: progress.value.totalReferenceFiles, progressCompletedUnits: progress.value.progressTotalUnits,
        progressPercent: 100,
        message: completionMessage,
      }
      await nextTick()
      await operations.renderCurrentPage()
      undoStack.value = []
      redoStack.value = []
      operations.scheduleDraftSave()
      operations.logAudit('analysis.completed', { files: files.length, candidates: total })
    } catch (cause) {
      if (cause?.name === 'JobCancelled' || cause?.name === 'AnalysisDialogCancelled') operations.showNotice(cause.message)
      else error.value = cause.message || String(cause)
    } finally {
      uploadCommitPending.value = false
      activeJobId.value = ''
      cancelling.value = false
      loading.value = false
    }
  }

  function dispose() {
    duplicateResolver?.('cancel')
    duplicateResolver = undefined
  }

  return {
    loading, enqueueing, enqueueProgress, progress, activeJobId, cancelling,
    duplicateDialog, duplicateInfo, enqueue, analyze, cancel, resolveDuplicate, dispose,
  }
}
