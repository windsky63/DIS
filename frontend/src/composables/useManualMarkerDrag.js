import { computed, ref, watch } from 'vue'

export function useManualMarkerDrag({ mode, editing, type, pageData, canvasSurface, canvasLayout, readOnly, regionDeleting, addMarker, previewMarker }) {
  const selection = ref(null)
  let capturedElement = null
  const enabled = computed(() => mode.value === 'drag' && editing.value)
  const markerPreview = computed(() => selection.value && previewMarker
    ? previewMarker(selection.value.start, selection.value.end)
    : null)
  const preview = computed(() => {
    if (!selection.value) return null
    const { start, end } = selection.value
    const target = canvasLayout.value.target
    return {
      x1: target.x + start.x * target.width, y1: target.y + start.y * target.height,
      x2: target.x + end.x * target.width, y2: target.y + end.y * target.height,
    }
  })
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
  function cancel() {
    const pointerId = selection.value?.pointerId
    selection.value = null
    const element = capturedElement
    capturedElement = null
    if (pointerId != null && element?.hasPointerCapture?.(pointerId)) element.releasePointerCapture(pointerId)
  }
  function start(event) {
    if (!enabled.value || event.button !== 0 || event.defaultPrevented || readOnly.value || regionDeleting.value || selection.value) return
    event.preventDefault()
    const start = point(event)
    if (!start || !pageData.value) return
    selection.value = { pointerId: event.pointerId, start, end: start, clientX: event.clientX, clientY: event.clientY, page: pageData.value, type: type.value }
    capturedElement = event.currentTarget
    capturedElement.setPointerCapture?.(event.pointerId)
  }
  function move(event) {
    if (selection.value?.pointerId !== event.pointerId) return
    event.preventDefault()
    const end = point(event, true)
    if (end) selection.value.end = end
  }
  function finish(event) {
    const value = selection.value
    if (!value || value.pointerId !== event.pointerId) return
    move(event)
    if (enabled.value && !readOnly.value && !regionDeleting.value && pageData.value === value.page && type.value === value.type && Math.hypot(event.clientX - value.clientX, event.clientY - value.clientY) >= 4) {
      const bounds = canvasSurface.value?.getBoundingClientRect()
      const layout = canvasLayout.value
      if (bounds?.width && bounds.height) {
        addMarker(
          bounds.left + (layout.target.x + value.start.x * layout.target.width) / layout.width * bounds.width,
          bounds.top + (layout.target.y + value.start.y * layout.target.height) / layout.height * bounds.height,
          { x: value.end.x * value.page.width, y: value.end.y * value.page.height },
        )
      }
    }
    cancel()
  }
  watch([mode, editing, type, pageData, readOnly, regionDeleting], cancel, { flush: 'sync' })
  return { enabled, preview, markerPreview, start, move, finish, cancel }
}
