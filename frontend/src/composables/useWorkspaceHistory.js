import { computed, ref } from 'vue'

export function useWorkspaceHistory({
  result,
  pages,
  pageData,
  markerStyle,
  componentMarkerStyles,
  projectResults,
  activeProjectIndex,
  selectedId,
  cloneValue,
  scheduleSave,
  showNotice,
  logAudit,
  limit = 50,
}) {
  const undoStack = ref([])
  const redoStack = ref([])
  const pending = ref(null)
  const applying = ref(false)
  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)

  function pageSnapshots(scope = 'page') {
    const selectedPages = scope === 'all' ? pages.value : [pageData.value].filter(Boolean)
    return selectedPages.map(page => ({ page: Number(page.page), value: cloneValue(page) }))
  }

  function inverse(snapshot) {
    const pageNumbers = new Set((snapshot.pageSnapshots || []).map(item => Number(item.page)))
    const selectedPages = snapshot.pages
      ? pages.value
      : pages.value.filter(page => pageNumbers.has(Number(page.page)))
    return {
      description: snapshot.description,
      pageSnapshots: selectedPages.map(page => ({ page: Number(page.page), value: cloneValue(page) })),
      markerStyle: cloneValue(markerStyle.value),
      componentMarkerStyles: cloneValue(componentMarkerStyles.value),
    }
  }

  function begin(description, scope = 'page') {
    if (applying.value || pending.value || !result.value) return
    pending.value = {
      description,
      pageSnapshots: pageSnapshots(scope),
      markerStyle: cloneValue(markerStyle.value),
      componentMarkerStyles: cloneValue(componentMarkerStyles.value),
    }
  }

  function commit() {
    if (!pending.value || !result.value) return
    const before = pending.value
    pending.value = null
    if (
      before.pageSnapshots.every(snapshot => JSON.stringify(snapshot.value) === JSON.stringify(
        pages.value.find(page => Number(page.page) === Number(snapshot.page))
      ))
      && JSON.stringify(before.markerStyle) === JSON.stringify(markerStyle.value)
      && JSON.stringify(before.componentMarkerStyles) === JSON.stringify(componentMarkerStyles.value)
    ) return
    undoStack.value.push(before)
    if (undoStack.value.length > limit) undoStack.value.shift()
    redoStack.value = []
    scheduleSave()
  }

  function apply(snapshot) {
    if (!result.value || !snapshot) return
    applying.value = true
    if (snapshot.pages) result.value.pages = cloneValue(snapshot.pages)
    else {
      const restored = new Map((snapshot.pageSnapshots || []).map(item => [Number(item.page), cloneValue(item.value)]))
      result.value.pages = pages.value.map(page => restored.get(Number(page.page)) || page)
    }
    markerStyle.value = cloneValue(snapshot.markerStyle || markerStyle.value)
    componentMarkerStyles.value = cloneValue(snapshot.componentMarkerStyles || componentMarkerStyles.value)
    projectResults.value[activeProjectIndex.value] = result.value
    selectedId.value = ''
    applying.value = false
    scheduleSave()
  }

  function undo() {
    const snapshot = undoStack.value.pop()
    if (!snapshot || !result.value) return
    redoStack.value.push(inverse(snapshot))
    apply(snapshot)
    showNotice(`已撤销：${snapshot.description}`)
    logAudit('undo', { description: snapshot.description })
  }

  function redo() {
    const snapshot = redoStack.value.pop()
    if (!snapshot || !result.value) return
    undoStack.value.push(inverse(snapshot))
    apply(snapshot)
    showNotice(`已重做：${snapshot.description}`)
    logAudit('redo', { description: snapshot.description })
  }

  function clear() {
    undoStack.value = []
    redoStack.value = []
    pending.value = null
  }

  return { undoStack, redoStack, pending, applying, canUndo, canRedo, begin, commit, undo, redo, clear }
}
