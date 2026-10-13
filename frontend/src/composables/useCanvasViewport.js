import { computed, ref } from 'vue'

export function useCanvasViewport({ canvasLayout, canvasViewport, onResolutionChange, onPointerMove, onPageStep, blockLeftPan } = {}) {
  const zoom = ref(1)
  const pan = ref({ x: 0, y: 0 })
  const panState = ref(null)
  let lastPageWheelAt = Number.NEGATIVE_INFINITY
  let suppressPanClick = false
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
    if (event.ctrlKey) {
      zoomAt(event.clientX, event.clientY, zoom.value * (event.deltaY < 0 ? 1.12 : .89))
      return 'zoom'
    }
    if (Math.abs(Number(event.deltaY) || 0) < 4) return null
    const timestamp = Number(event.timeStamp) || Date.now()
    if (timestamp - lastPageWheelAt < 180) return null
    lastPageWheelAt = timestamp
    onPageStep?.(event.deltaY > 0 ? 1 : -1)
    return 'page'
  }

  function zoomBy(factor) {
    const bounds = canvasViewport.value?.getBoundingClientRect()
    if (bounds) zoomAt(bounds.left + bounds.width / 2, bounds.top + bounds.height / 2, zoom.value * factor)
  }

  function startPan(event) {
    if (event.defaultPrevented || ![0, 1].includes(event.button) || (event.button === 0 && blockLeftPan?.())) return
    panState.value = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      panX: pan.value.x,
      panY: pan.value.y,
      moved: false,
    }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  function movePan(event) {
    onPointerMove?.(event)
    const state = panState.value
    if (state?.pointerId === event.pointerId) {
      const deltaX = event.clientX - state.startX
      const deltaY = event.clientY - state.startY
      if (Math.hypot(deltaX, deltaY) >= 3) state.moved = true
      if (state.moved) event.preventDefault?.()
      pan.value = { x: state.panX + deltaX, y: state.panY + deltaY }
    }
  }

  function endPan(event) {
    if (panState.value?.pointerId !== event.pointerId) return
    suppressPanClick = Boolean(panState.value.moved)
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    panState.value = null
  }

  function consumePanClick() {
    const suppressed = suppressPanClick
    suppressPanClick = false
    return suppressed
  }

  return { zoom, pan, panState, transform, surfaceStyle, fit, zoomAt, zoomBy, onWheel, startPan, movePan, endPan, consumePanClick }
}
