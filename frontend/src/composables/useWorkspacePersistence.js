import { computed, ref } from 'vue'

import { saveDraft } from '../workspaceStorage.js'

export function useWorkspacePersistence({ state, operations }) {
  const backendSaveState = ref('idle')
  const draftDirty = ref(false)
  const backendSaveButtonLabel = computed(() => ({ pending: '待保存', saving: '保存中…', saved: '已保存', error: '重试保存' })[backendSaveState.value] || '保存')
  let savePromise = null

  function scheduleDraftSave() {
    if (!state.activeFileFingerprint.value || !state.result.value || state.result.value.status === 'processing'
        || state.applyingHistory.value || state.hydratingWorkspace.value) return
    draftDirty.value = true
    backendSaveState.value = 'pending'
    operations.markDirty?.()
  }

  function scheduleWorkspaceDraftSave() {
    if (!state.activeFileFingerprint.value || !state.result.value || state.result.value.status === 'processing'
        || state.applyingHistory.value || state.hydratingWorkspace.value || state.archivingWorkspace.value) return
    draftDirty.value = true
  }

  function markRecoveredPagesDirty(pages = []) {
    const pageNumbers = new Set(pages
      .map(page => Number(typeof page === 'object' ? page?.page : page))
      .filter(page => Number.isInteger(page) && page > 0))
    if (!pageNumbers.size) return
    pageNumbers.forEach(page => operations.markDirty?.(page))
    backendSaveState.value = 'pending'
  }

  async function persistDraftChanges() {
    if (!state.activeFileFingerprint.value || !state.result.value) return null
    const saved = await saveDraft(state.activeFileFingerprint.value, operations.buildDraftPayload())
    draftDirty.value = false
    operations.logAudit('draft.saved')
    return saved
  }

  async function persistBackendChanges(notifyUser = false) {
    if (!state.result.value || state.result.value.status !== 'complete') return null
    if (savePromise) return savePromise
    backendSaveState.value = 'saving'
    savePromise = state.result.value.jobId === 'tutorial-000207'
      ? persistDraftChanges()
      : (async () => {
          const savedPage = operations.saveAllDirtyPages
            ? await operations.saveAllDirtyPages()
            : await operations.saveCurrentPage?.()
          if (draftDirty.value) await persistDraftChanges()
          return savedPage
        })()
    try {
      const saved = await savePromise
      backendSaveState.value = 'saved'
      operations.logAudit('job.page-saved')
      if (notifyUser) operations.showNotice('当前 PDF 校对结果已保存。')
      return saved
    } catch (cause) {
      backendSaveState.value = 'error'
      state.error.value = `保存失败，修改仍保留在当前页面：${cause.message || cause}`
      throw cause
    } finally {
      savePromise = null
    }
  }

  function dispose() {}
  return {
    backendSaveState, backendSaveButtonLabel, draftDirty,
    scheduleDraftSave, scheduleWorkspaceDraftSave, markRecoveredPagesDirty,
    persistDraftChanges, persistBackendChanges, dispose,
  }
}
