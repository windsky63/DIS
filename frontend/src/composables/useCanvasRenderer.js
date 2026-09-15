import { nextTick, ref } from 'vue'

import { trimCanvasCache } from '../canvasPerformance.js'
import { referenceFileSlot } from '../referenceIdentity.js'
import { loadSnapshot, saveSnapshot } from '../workspaceStorage.js'

export function calculateCanvasPageLayout(targetViewport, referenceViewport, displayReference = true) {
  const visibleReference = displayReference ? referenceViewport : null
  const headerHeight = 34
  const gap = visibleReference ? 28 : 0
  const referenceX = visibleReference ? targetViewport.width + gap : 0
  return {
    width: targetViewport.width + (visibleReference ? gap + visibleReference.width : 0),
    height: headerHeight + Math.max(targetViewport.height, visibleReference?.height || 0),
    target: { x: 0, y: headerHeight, width: targetViewport.width, height: targetViewport.height },
    reference: visibleReference
      ? { x: referenceX, y: headerHeight, width: visibleReference.width, height: visibleReference.height }
      : null,
  }
}

export function useCanvasRenderer({ state, fitCanvas }) {
  const rendering = ref(false)
  const pageBitmapCache = new Map()
  let renderTasks = []
  let generation = 0
  let resolutionTimer

  function cancelTasks() {
    renderTasks.forEach(task => {
      try { task.cancel() } catch { /* render already completed */ }
    })
    renderTasks = []
  }

  function invalidate() {
    generation += 1
    cancelTasks()
  }

  function clearCache() {
    pageBitmapCache.clear()
  }

  function bitmapCacheKey(kind, slot, file, pageNumber, viewport, pixelFactor) {
    return `${kind}:${slot}:${file?.webkitRelativePath || file?.name || 'pdf'}:${file?.size || 0}:${file?.lastModified || 0}:p${pageNumber}:${Math.ceil(viewport.width * pixelFactor)}x${Math.ceil(viewport.height * pixelFactor)}`
  }

  function cachePageBitmap(cacheKey, canvas) {
    pageBitmapCache.delete(cacheKey)
    pageBitmapCache.set(cacheKey, canvas)
    trimCanvasCache(pageBitmapCache, state.performanceProfile.value.cacheBytes)
  }

  async function renderPageOffscreen(page, viewport, pixelFactor, cacheKey) {
    const width = Math.ceil(viewport.width * pixelFactor)
    const height = Math.ceil(viewport.height * pixelFactor)
    if (pageBitmapCache.has(cacheKey)) return pageBitmapCache.get(cacheKey)
    const persisted = await loadSnapshot(cacheKey).catch(() => null)
    if (persisted?.blob && persisted.width === width && persisted.height === height) {
      try {
        if (typeof globalThis.createImageBitmap === 'function') {
          const bitmap = await globalThis.createImageBitmap(persisted.blob)
          const restored = document.createElement('canvas')
          restored.width = width
          restored.height = height
          const restoredContext = restored.getContext('2d', { alpha: false })
          if (!restoredContext) throw new Error('浏览器无法创建 Canvas 2D 上下文')
          restoredContext.drawImage(bitmap, 0, 0)
          bitmap.close?.()
          cachePageBitmap(cacheKey, restored)
          return restored
        }
      } catch { /* Bitmap cache decoding failed; render the PDF page again. */ }
    }
    const buffer = document.createElement('canvas')
    buffer.width = width
    buffer.height = height
    const bufferContext = buffer.getContext('2d', { alpha: false })
    if (!bufferContext) throw new Error('浏览器无法创建 Canvas 2D 上下文，请检查硬件加速或浏览器策略')
    const task = page.render({
      canvasContext: bufferContext,
      viewport,
      transform: [pixelFactor, 0, 0, pixelFactor, 0, 0],
    })
    renderTasks.push(task)
    await task.promise
    cachePageBitmap(cacheKey, buffer)
    buffer.toBlob(blob => {
      if (blob) void saveSnapshot(cacheKey, blob, width, height).catch(() => {})
    }, 'image/webp', .88)
    return buffer
  }

  function requestIsCurrent(request, requestGeneration) {
    const displayReference = state.displayReference?.value !== false
    return requestGeneration === generation
      && request.targetDocument === state.targetDocument.value
      && request.displayReference === displayReference
      && request.referenceDocument === (displayReference ? state.referenceDocument.value : null)
      && request.projectIndex === state.activeProjectIndex.value
      && request.referenceIndex === state.activeReferenceIndex.value
      && request.shownPage === Number(state.shownPage.value)
      && request.referencePage === Number(state.activeReferencePage.value)
  }

  async function render(fitAfter = false, indicateLoading = true) {
    if (!state.targetDocument.value || !state.pdfCanvas.value) return
    const requestGeneration = ++generation
    cancelTasks()
    if (indicateLoading) rendering.value = true
    try {
      const request = {
        targetDocument: state.targetDocument.value,
        displayReference: state.displayReference?.value !== false,
        referenceDocument: state.displayReference?.value !== false ? state.referenceDocument.value : null,
        targetFile: state.targetPdf.value,
        referenceFile: state.referencePdf.value,
        projectIndex: state.activeProjectIndex.value,
        referenceIndex: state.activeReferenceIndex.value,
        shownPage: Number(state.shownPage.value) || 1,
        referencePage: Number(state.activeReferencePage.value) || 1,
      }
      const pageNumber = Math.max(1, Math.min(request.targetDocument.numPages, request.shownPage))
      const targetPage = await request.targetDocument.getPage(pageNumber)
      const referencePageNumber = request.referenceDocument
        ? Math.min(Math.max(1, request.referencePage), request.referenceDocument.numPages)
        : 0
      const referencePage = referencePageNumber ? await request.referenceDocument.getPage(referencePageNumber) : null
      if (!requestIsCurrent(request, requestGeneration)) return

      const baseScale = 1.35
      const targetViewport = targetPage.getViewport({ scale: baseScale })
      const referenceViewport = referencePage?.getViewport({ scale: baseScale }) || null
      const layout = calculateCanvasPageLayout(targetViewport, referenceViewport, request.displayReference)
      const headerHeight = layout.target.y
      const referenceX = layout.reference?.x || 0
      const logicalWidth = layout.width
      const logicalHeight = layout.height
      const resolutionScale = Math.max(1, Math.min(4, state.zoom.value))
      const desiredPixelFactor = (window.devicePixelRatio || 1) * resolutionScale
      const profile = state.performanceProfile.value
      const dimensionLimit = profile.maxDimension / Math.max(logicalWidth, logicalHeight)
      const areaLimit = Math.sqrt(profile.maxPixels / Math.max(logicalWidth * logicalHeight, 1))
      const pixelFactor = Math.max(Number.EPSILON, Math.min(desiredPixelFactor, dimensionLimit, areaLimit))
      const canvas = state.pdfCanvas.value
      canvas.width = Math.ceil(logicalWidth * pixelFactor)
      canvas.height = Math.ceil(logicalHeight * pixelFactor)
      canvas.style.width = `${logicalWidth}px`
      canvas.style.height = `${logicalHeight}px`
      state.canvasLayout.value = layout

      const [targetBitmap, referenceBitmap] = await Promise.all([
        renderPageOffscreen(targetPage, targetViewport, pixelFactor, bitmapCacheKey('target', request.projectIndex, request.targetFile, pageNumber, targetViewport, pixelFactor)),
        referencePage && referenceViewport
          ? renderPageOffscreen(referencePage, referenceViewport, pixelFactor, bitmapCacheKey('reference', referenceFileSlot(request.referenceFile, request.referenceIndex), request.referenceFile, referencePageNumber, referenceViewport, pixelFactor))
          : null,
      ])
      if (!requestIsCurrent(request, requestGeneration)) return
      const context = canvas.getContext('2d', { alpha: false })
      if (!context) throw new Error('浏览器无法创建 Canvas 2D 上下文，请检查硬件加速或浏览器策略')
      context.setTransform(1, 0, 0, 1, 0, 0)
      context.fillStyle = '#dfe5e8'
      context.fillRect(0, 0, canvas.width, canvas.height)
      context.drawImage(targetBitmap, 0, headerHeight * pixelFactor)
      if (referenceBitmap) context.drawImage(referenceBitmap, referenceX * pixelFactor, headerHeight * pixelFactor)
      context.setTransform(pixelFactor, 0, 0, pixelFactor, 0, 0)
      context.fillStyle = '#173e57'
      context.font = '600 13px Microsoft YaHei UI, sans-serif'
      context.fillText('待标识 ISO PDF', 10, 22)
      if (layout.reference) {
        context.fillStyle = '#2d8b89'
        const referenceCaption = state.referencePdfs.value.length > 1
          ? `对照 PDF ${request.referenceIndex + 1}/${state.referencePdfs.value.length} · P${referencePageNumber} · ${request.referenceFile?.name || ''}`
          : `对照 PDF · P${referencePageNumber}`
        context.fillText(referenceCaption, referenceX + 10, 22)
        context.strokeStyle = '#8798a2'
        context.lineWidth = 1
        context.beginPath()
        context.moveTo(targetViewport.width + 14, 0)
        context.lineTo(targetViewport.width + 14, logicalHeight)
        context.stroke()
      }
      if (fitAfter) await nextTick().then(() => fitCanvas(false))
    } catch (cause) {
      if (cause?.name !== 'RenderingCancelledException') state.error.value = `Canvas 渲染失败：${cause.message || cause}`
    } finally {
      if (requestGeneration === generation) rendering.value = false
    }
  }

  function schedule() {
    clearTimeout(resolutionTimer)
    resolutionTimer = setTimeout(() => { void render(false, false) }, 140)
  }

  function dispose() {
    clearTimeout(resolutionTimer)
    invalidate()
    clearCache()
  }

  return { rendering, render, schedule, invalidate, cancelTasks, clearCache, dispose }
}
