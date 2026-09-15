import { computed, ref, watch } from 'vue'

export function useRegionDeletion({ pageData, canvasSurface, canvasLayout, readOnly, manualAddMode, selectedId, swapSourceId, editingId, operations }) {
  const active = ref(false)
  const selection = ref(null)
  let capturedElement = null
  const rectangle = computed(() => {
    const value = selection.value
    if (!value) return null
    return {
      left: Math.min(value.start.x, value.end.x), top: Math.min(value.start.y, value.end.y),
      width: Math.abs(value.end.x - value.start.x), height: Math.abs(value.end.y - value.start.y),
    }
  })
  const rectangleStyle = computed(() => {
    const rect = rectangle.value
    const target = canvasLayout.value.target
    return rect ? {
      left: `${target.x + rect.left * target.width}px`, top: `${target.y + rect.top * target.height}px`,
      width: `${rect.width * target.width}px`, height: `${rect.height * target.height}px`,
    } : {}
  })

  function cancelSelection() {
    const pointerId = selection.value?.pointerId
    selection.value = null
    if (pointerId != null && capturedElement?.hasPointerCapture?.(pointerId)) capturedElement.releasePointerCapture(pointerId)
    capturedElement = null
  }
  function exit() { active.value = false; cancelSelection() }
  function toggle() {
    if (active.value) { exit(); return }
    if (!pageData.value || readOnly.value) return
    manualAddMode.value = false
    editingId.value = ''
    active.value = true
    operations.showNotice('区域删除：按住左键拖框，删除定位点在框内的标识；Esc 退出，Ctrl+Z 撤销。')
  }
  function point(event, clamp = false) {
    const bounds = canvasSurface.value?.getBoundingClientRect()
    const layout = canvasLayout.value
    const target = layout.target
    if (!bounds?.width || !bounds.height || !target?.width || !target.height) return null
    const x = ((event.clientX - bounds.left) / bounds.width * layout.width - target.x) / target.width
    const y = ((event.clientY - bounds.top) / bounds.height * layout.height - target.y) / target.height
    if (!clamp && (x < 0 || x > 1 || y < 0 || y > 1)) return null
    return { x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y)) }
  }
  function start(event) {
    if (!active.value || event.button !== 0 || readOnly.value) return
    event.preventDefault(); event.stopPropagation()
    const start = point(event)
    if (!start) return
    selection.value = { pointerId: event.pointerId, start, end: start, clientX: event.clientX, clientY: event.clientY, page: pageData.value }
    capturedElement = event.currentTarget
    capturedElement.setPointerCapture?.(event.pointerId)
  }
  function move(event) {
    if (selection.value?.pointerId !== event.pointerId) return
    const end = point(event, true)
    if (end) selection.value.end = end
  }
  function finish(event) {
    const value = selection.value
    if (!value || value.pointerId !== event.pointerId) return
    move(event)
    const rect = rectangle.value
    if (!readOnly.value && pageData.value === value.page &&
      Math.abs(event.clientX - value.clientX) >= 4 && Math.abs(event.clientY - value.clientY) >= 4) {
      const page = value.page
      const removed = page.candidates.filter(item => {
        const x = item.xNorm ?? item.x / page.width
        const y = item.yNorm ?? item.y / page.height
        return x >= rect.left && x <= rect.left + rect.width && y >= rect.top && y <= rect.top + rect.height
      })
      if (removed.length) {
        const ids = new Set(removed.map(item => item.id))
        operations.beginHistory('区域删除标识')
        page.candidates = page.candidates.filter(item => !ids.has(item.id))
        page.candidateCount = page.candidates.length
        if (ids.has(selectedId.value)) selectedId.value = ''
        if (ids.has(swapSourceId.value)) swapSourceId.value = ''
        operations.commitHistory()
        operations.logAudit('marker.region_deleted', { page: page.page, ids: [...ids], count: removed.length })
        operations.showNotice(`已删除 ${removed.length} 个标识，可使用 Ctrl+Z 撤销；Esc 退出区域删除。`)
      } else operations.showNotice('区域内没有标识。')
    }
    cancelSelection()
  }
  watch(pageData, exit)
  watch(readOnly, value => { if (value) exit() })
  watch(manualAddMode, value => { if (value) exit() })
  return { active, rectangle, rectangleStyle, toggle, exit, start, move, finish, cancelSelection }
}
