import { computed, ref } from 'vue'

import { api } from '../api'


export function useAnalysisQueue({ currentResult, error, showNotice }) {
  const dialog = ref(false)
  const loading = ref(false)
  const actionId = ref('')
  const tab = ref('current')
  const deleteTarget = ref(null)
  const deleteDialog = ref(false)
  const queue = ref({ jobs: [], runningCount: 0, queuedCount: 0, maxConcurrent: 1 })
  const visibleJobs = computed(() => queue.value.jobs.filter(job => (
    tab.value === 'archived' ? job.isArchived : !job.isArchived
  )))

  async function refresh(showLoading = true) {
    if (showLoading) loading.value = true
    try {
      const snapshot = await api.getAnalysisQueue()
      queue.value = { ...snapshot, jobs: (snapshot.jobs || []).filter(job => job.status !== 'cancelled') }
    } catch (cause) {
      error.value = `读取解析队列失败：${cause.message || cause}`
    } finally {
      if (showLoading) loading.value = false
    }
  }

  async function open() {
    dialog.value = true
    await refresh()
  }

  async function cancel(job) {
    if (!job?.canCancel || actionId.value) return
    actionId.value = job.jobId
    try {
      await api.cancelJob(job.jobId)
      await refresh(false)
      showNotice(`已请求取消“${job.fileName}”。`)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { actionId.value = '' }
  }

  async function move(job, direction) {
    if (!job?.canReorder || actionId.value) return
    actionId.value = job.jobId
    try {
      await api.moveQueuedJob(job.jobId, direction)
      await refresh(false)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { actionId.value = '' }
  }

  async function archive(job, archived) {
    if (!job?.canArchive || actionId.value) return
    actionId.value = job.jobId
    try {
      await api.archiveJob(job.jobId, archived)
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
      await api.deleteJob(job.jobId)
      deleteDialog.value = false
      deleteTarget.value = null
      await refresh(false)
      showNotice(`已永久删除解析任务“${job.fileName}”。`)
    } catch (cause) { error.value = cause.message || String(cause) }
    finally { actionId.value = '' }
  }

  return {
    dialog, loading, actionId, tab, deleteTarget, deleteDialog, queue, visibleJobs,
    refresh, open, cancel, move, archive, requestDelete, confirmDelete,
  }
}
