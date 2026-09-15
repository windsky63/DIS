import { computed, ref } from 'vue'

export function useCanvasViewport({ canvasLayout, canvasViewport, onResolutionChange, onPointerMove } = {}) {
  const zoom = ref(1)
  const pan = ref({ x: 0, y: 0 })
  const panState = ref(null)
  const transform = computed(() => ({ transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${zoom.value})` }))
  const surfaceStyle = computed(() => ({
    width: `${canvasLayout.value.width}px`,
    height: `${canvasLayout.value.height}px`,
    ...transform.value,
  }))

  function fit(scheduleRender = true) {
    const viewport = canvasViewport.value
    const layout = canvasLayout.value
    if (!viewport || !layout.width || !layout.height) return
    const scale = Math.max(.1, Math.min(2, Math.min(
      (viewport.clientWidth - 40) / layout.width,
      (viewport.clientHeight - 40) / layout.height,
    )))
    zoom.value = scale
    pan.value = {
      x: (viewport.clientWidth - layout.width * scale) / 2,
      y: (viewport.clientHeight - layout.height * scale) / 2,
    }
    if (scheduleRender) onResolutionChange?.()
  }

  function zoomAt(clientX, clientY, nextZoom) {
    const viewport = canvasViewport.value
    if (!viewport) return
    const bounds = viewport.getBoundingClientRect()
    const localX = clientX - bounds.left
    const localY = clientY - bounds.top
    const bounded = Math.max(.1, Math.min(6, nextZoom))
    const worldX = (localX - pan.value.x) / zoom.value
    const worldY = (localY - pan.value.y) / zoom.value
    pan.value = { x: localX - worldX * bounded, y: localY - worldY * bounded }
    zoom.value = bounded
    onResolutionChange?.()
  }

  function onWheel(event) {
    zoomAt(event.clientX, event.clientY, zoom.value * (event.deltaY < 0 ? 1.12 : .89))
  }

  function zoomBy(factor) {
    const bounds = canvasViewport.value?.getBoundingClientRect()
    if (bounds) zoomAt(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2, zoom.value * factor)
  }

  function startPan(event) {
    if (event.button !== 1) return
    event.preventDefault()
    panState.value = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      panX: pan.value.x,
      panY: pan.value.y,
    }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function movePan(event) {
    onPointerMove?.(event)
    const state = panState.value
    if (state?.pointerId === event.pointerId) {
      pan.value = { x: state.panX + event.clientX - state.startX, y: state.panY + event.clientY - state.startY }
    }
  }

  function endPan(event) {
    if (panState.value?.pointerId !== event.pointerId) return
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    panState.value = null
  }

  return { zoom, pan, panState, transform, surfaceStyle, fit, zoomAt, zoomBy, onWheel, startPan, movePan, endPan }
}
