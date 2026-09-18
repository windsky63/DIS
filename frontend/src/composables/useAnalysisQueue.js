import { computed, ref } from 'vue'

import { api } from '../api.js'


export function useAnalysisQueue({ currentResult, error, showNotice, client = api }) {
  const dialog = ref(false)
  const loading = ref(false)
  const actionId = ref('')
  const cancellingJobIds = ref([])
  const tab = ref('current')
  const page = ref(1)
  const pageSize = 20
  const deleteTarget = ref(null)
  const deleteDialog = ref(false)
  const queue = ref({
    jobs: [], runningCount: 0, queuedCount: 0, archivedCount: 0, currentCount: 0,
    maxConcurrent: 1,
    pagination: { scope: 'current', page: 1, pageSize, totalItems: 0, totalPages: 0 },
  })
  const visibleJobs = computed(() => queue.value.jobs.filter(job => job.status !== 'cancelled'))
  let refreshGeneration = 0

  async function refresh(showLoading = true) {
    const generation = ++refreshGeneration
    const requestedScope = tab.value
    const requestedPage = page.value
    if (showLoading) loading.value = true
    try {
      const snapshot = await client.getAnalysisQueue({ scope: requestedScope, page: requestedPage, pageSize })
      if (generation !== refreshGeneration) return
      queue.value = { ...snapshot, jobs: snapshot.jobs || [] }
      page.value = Number(snapshot.pagination?.page) || 1
    } catch (cause) {
      if (generation !== refreshGeneration) return
      error.value = `读取解析队列失败：${cause.message || cause}`
    } finally {
      if (generation === refreshGeneration) loading.value = false
    }
  }

  async function open() {
    dialog.value = true
    await refresh()
  }

  async function changePage(nextPage) {
    const totalPages = Math.max(1, Number(queue.value.pagination?.totalPages) || 1)
    page.value = Math.min(totalPages, Math.max(1, Number(nextPage) || 1))
    await refresh(false)
  }

  async function selectTab(nextTab) {
    tab.value = nextTab === 'archived' ? 'archived' : 'current'
    page.value = 1
    await refresh(false)
  }

  async function cancel(job) {
    if (!job?.canCancel || actionId.value === job.jobId || cancellingJobIds.value.includes(job.jobId)) return
    cancellingJobIds.value = [...cancellingJobIds.value, job.jobId]
    try {
      await client.cancelJob(job.jobId)
      await refresh(false)
      showNotice(`已请求取消“${job.fileName}”。`)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { cancellingJobIds.value = cancellingJobIds.value.filter(jobId => jobId !== job.jobId) }
  }

  async function move(job, direction) {
    if (!job?.canReorder || actionId.value || cancellingJobIds.value.includes(job.jobId)) return
    actionId.value = job.jobId
    try {
      await client.moveQueuedJob(job.jobId, direction)
      await refresh(false)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { actionId.value = '' }
  }

  async function archive(job, archived) {
    if (!job?.canArchive || actionId.value) return
    actionId.value = job.jobId
    try {
      await client.archiveJob(job.jobId, archived)
      await refresh(false)
      showNotice(archived ? `已归档“${job.fileName}”。` : `已将“${job.fileName}”移回已完成任务。`)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { actionId.value = '' }
  }

  function requestDelete(job) {
    if (!job?.canDelete || currentResult.value?.jobId === job.jobId) return
    deleteTarget.value = job
    deleteDialog.value = true
  }

  async function confirmDelete() {
    const job = deleteTarget.value
    if (!job || actionId.value) return
    actionId.value = job.jobId
    try {
      await client.deleteJob(job.jobId)
      deleteDialog.value = false
      deleteTarget.value = null
      await refresh(false)
      showNotice(`已永久删除解析任务“${job.fileName}”。`)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { actionId.value = '' }
  }

  return {
    dialog, loading, actionId, cancellingJobIds, tab, page, deleteTarget, deleteDialog, queue, visibleJobs,
    refresh, open, changePage, selectTab, cancel, move, archive, requestDelete, confirmDelete,
  }
}
