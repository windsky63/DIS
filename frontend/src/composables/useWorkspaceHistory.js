import { computed, ref, watch } from 'vue'

const REVIEW_FIELDS = ['reviewRevision', 'reviewedBy', 'reviewedAt']
export const DEFAULT_TASK_HISTORY_LIMIT = 120

export function useWorkspaceHistory({
  result, pages, pageData, markerStyle, componentMarkerStyles, projectResults,
  activeProjectIndex, selectedId, cloneValue, scheduleSave, showNotice, logAudit,
  canEdit = () => true, limit = 30, taskLimit = DEFAULT_TASK_HISTORY_LIMIT,
}) {
  const histories = ref(new Map())
  const pending = ref(null)
  const applying = ref(false)
  const pageHistoryLimit = Math.max(1, Number(limit) || 30)
  const taskHistoryLimit = Math.max(1, Number(taskLimit) || DEFAULT_TASK_HISTORY_LIMIT)
  let historySequence = 0
  const documentKey = () => JSON.stringify([result.value?.jobId || result.value?.targetFile || '', activeProjectIndex.value])
  const pageKey = (page, taskKey = documentKey()) => `${taskKey}:${Number(page)}`
  const currentKey = () => pageKey(pageData.value?.page)
  watch(currentKey, () => { pending.value = null }, { flush: 'sync' })
  function history(key = currentKey(), taskKey = documentKey()) {
    if (!histories.value.has(key)) histories.value.set(key, { taskKey, undo: [], redo: [] })
    return histories.value.get(key)
  }
  const undoStack = computed({ get: () => history().undo, set: value => { history().undo = value } })
  const redoStack = computed({ get: () => history().redo, set: value => { history().redo = value } })
  const canUndo = computed(() => canEdit() && undoStack.value.length > 0)
  const canRedo = computed(() => canEdit() && redoStack.value.length > 0)

  function editablePage(page) {
    const value = cloneValue(page)
    for (const field of REVIEW_FIELDS) delete value[field]
    return value
  }
  function snapshot(description) {
    if (!pageData.value) return null
    return {
      taskKey: documentKey(), key: currentKey(), page: Number(pageData.value.page), description,
      value: editablePage(pageData.value),
      markerStyle: cloneValue(markerStyle.value),
      componentMarkerStyles: cloneValue(componentMarkerStyles.value),
    }
  }
  function trimTaskHistory(taskKey) {
    const entries = []
    for (const stacks of histories.value.values()) {
      if (stacks.taskKey !== taskKey) continue
      for (const stackName of ['undo', 'redo']) {
        stacks[stackName].forEach(saved => entries.push({ saved, stacks, stackName }))
      }
    }
    entries.sort((left, right) => Number(left.saved.historyOrder) - Number(right.saved.historyOrder))
    for (const entry of entries.slice(0, Math.max(0, entries.length - taskHistoryLimit))) {
      const stack = entry.stacks[entry.stackName]
      const index = stack.indexOf(entry.saved)
      if (index >= 0) stack.splice(index, 1)
    }
  }
  function begin(description) {
    if (applying.value || pending.value || !result.value || !canEdit()) return
    pending.value = snapshot(description)
  }
  function commit() {
    const before = pending.value
    pending.value = null
    if (!before || !result.value || before.key !== currentKey() || !canEdit()) return
    before.restoreMarkerStyle = JSON.stringify(before.markerStyle) !== JSON.stringify(markerStyle.value)
    before.restoreComponentStyles = JSON.stringify(before.componentMarkerStyles) !== JSON.stringify(componentMarkerStyles.value)
    if (JSON.stringify(before.value) === JSON.stringify(editablePage(pageData.value))
        && !before.restoreMarkerStyle && !before.restoreComponentStyles) return
    before.historyOrder = ++historySequence
    const stacks = history(before.key, before.taskKey)
    stacks.undo.push(before)
    if (stacks.undo.length > pageHistoryLimit) stacks.undo.shift()
    stacks.redo = []
    trimTaskHistory(before.taskKey)
    scheduleSave()
  }
  function apply(saved) {
    if (!result.value || saved.key !== currentKey() || !canEdit()) return false
    applying.value = true
    try {
      result.value.pages = pages.value.map(page => {
        if (Number(page.page) !== saved.page) return page
        const restored = cloneValue(saved.value)
        // Server versions are monotonic, never part of an edit undo.
        for (const field of REVIEW_FIELDS) {
          if (Object.hasOwn(page, field)) restored[field] = cloneValue(page[field])
        }
        return restored
      })
      if (saved.restoreMarkerStyle) markerStyle.value = cloneValue(saved.markerStyle)
      if (saved.restoreComponentStyles) componentMarkerStyles.value = cloneValue(saved.componentMarkerStyles)
      projectResults.value[activeProjectIndex.value] = result.value
      selectedId.value = ''
    } finally { applying.value = false }
    scheduleSave()
    return true
  }
  function move(from, to, action, label) {
    if (!canEdit() || !result.value || pending.value) return
    const stacks = history()
    const saved = stacks[from].at(-1)
    if (!saved || saved.key !== currentKey()) return
    const inverse = { ...snapshot(saved.description),
      restoreMarkerStyle: saved.restoreMarkerStyle,
      restoreComponentStyles: saved.restoreComponentStyles,
      historyOrder: ++historySequence }
    if (!apply(saved)) return
    stacks[from].pop()
    stacks[to].push(inverse)
    trimTaskHistory(saved.taskKey)
    showNotice(`已${label}：${saved.description}`)
    logAudit(action, { description: saved.description, page: saved.page })
  }
  const undo = () => move('undo', 'redo', 'undo', '撤销')
  const redo = () => move('redo', 'undo', 'redo', '重做')
  function clearPage(page) {
    const key = pageKey(page)
    histories.value.delete(key)
    if (pending.value?.key === key) pending.value = null
  }
  function clear() { histories.value.clear(); pending.value = null }
  return { undoStack, redoStack, pending, applying, canUndo, canRedo, begin, commit, undo, redo, clear, clearPage }
}
