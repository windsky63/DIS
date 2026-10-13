import { nextTick, ref } from 'vue'

import { boundedCanvasPixelFactor, trimCanvasCache } from '../canvasPerformance.js'
import { referenceFileSlot } from '../referenceIdentity.js'
import { loadSnapshot, saveSnapshot } from '../workspaceStorage.js'

function scheduleIdleWork(callback) {
  if (typeof globalThis.requestIdleCallback === 'function') {
    return globalThis.requestIdleCallback(callback, { timeout: 800 })
  }
  return globalThis.setTimeout(callback, 240)
}

function cancelIdleWork(handle) {
  if (typeof globalThis.cancelIdleCallback === 'function') globalThis.cancelIdleCallback(handle)
  else globalThis.clearTimeout(handle)
}

const PREVIEW_MAX_PIXELS = 4_000_000
const HIGH_RESOLUTION_SETTLE_MS = 180
const REFERENCE_PREFETCH_SETTLE_MS = 550

function scheduleDelayedWork(callback, delay) {
  return globalThis.setTimeout(callback, delay)
}

function cancelDelayedWork(handle) {
  globalThis.clearTimeout(handle)
}

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

export function useCanvasRenderer({
  state,
  fitCanvas,
  scheduleIdle = scheduleIdleWork,
  cancelIdle = cancelIdleWork,
  scheduleDelay = scheduleDelayedWork,
  cancelDelay = cancelDelayedWork,
}) {
  const rendering = ref(false)
  const pageBitmapCache = new Map()
  let renderTasks = []
  let generation = 0
  let resolutionTimer
  let prefetchHandle
  let referencePrefetchHandle
  let referencePrefetchTimer
  let upgradeTimer
  let upgradeResolve

  function cancelUpgradeWait() {
    if (upgradeTimer !== undefined) cancelDelay(upgradeTimer)
    upgradeTimer = undefined
    upgradeResolve?.(false)
    upgradeResolve = undefined
  }

  function waitForUpgrade() {
    cancelUpgradeWait()
    return new Promise(resolve => {
      upgradeResolve = resolve
      upgradeTimer = scheduleDelay(() => {
        upgradeTimer = undefined
        upgradeResolve = undefined
        resolve(true)
      }, HIGH_RESOLUTION_SETTLE_MS)
    })
  }

  function cancelPrefetch() {
    if (prefetchHandle !== undefined) cancelIdle(prefetchHandle)
    if (referencePrefetchHandle !== undefined) cancelIdle(referencePrefetchHandle)
    if (referencePrefetchTimer !== undefined) cancelDelay(referencePrefetchTimer)
    prefetchHandle = undefined
    referencePrefetchHandle = undefined
    referencePrefetchTimer = undefined
  }

  function cancelTasks() {
    renderTasks.forEach(task => {
      try { task.cancel() } catch { /* render already completed */ }
    })
    renderTasks = []
  }

  function invalidate() {
    generation += 1
    cancelUpgradeWait()
    cancelPrefetch()
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

  async function restorePageBitmap(cacheKey, width, height) {
    if (pageBitmapCache.has(cacheKey)) return pageBitmapCache.get(cacheKey)
    const persisted = await loadSnapshot(cacheKey).catch(() => null)
    if (!persisted?.blob || persisted.width !== width || persisted.height !== height) return null
    try {
      if (typeof globalThis.createImageBitmap !== 'function') return null
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
    } catch {
      return null
    }
  }

  async function renderPageOffscreen(page, viewport, pixelFactor, cacheKey, { restore = true, persist = true } = {}) {
    const width = Math.ceil(viewport.width * pixelFactor)
    const height = Math.ceil(viewport.height * pixelFactor)
    if (restore) {
      const cached = await restorePageBitmap(cacheKey, width, height)
      if (cached) return cached
    } else if (pageBitmapCache.has(cacheKey)) return pageBitmapCache.get(cacheKey)
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
    try {
      await task.promise
    } finally {
      renderTasks = renderTasks.filter(activeTask => activeTask !== task)
    }
    cachePageBitmap(cacheKey, buffer)
    if (persist) {
      buffer.toBlob(blob => {
        if (blob) void saveSnapshot(cacheKey, blob, width, height).catch(() => {})
      }, 'image/webp', .88)
    }
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

  async function prefetchCandidate(request, candidate, baseScale, pixelFactor, requestGeneration) {
    if (!candidate || !requestIsCurrent(request, requestGeneration)) return
    try {
      const page = await candidate.document.getPage(candidate.pageNumber)
      if (!requestIsCurrent(request, requestGeneration)) return
      const viewport = page.getViewport({ scale: baseScale })
      const candidatePixelFactor = state.performanceProfile.value.key === 'low'
        ? Math.min(pixelFactor, Math.sqrt(PREVIEW_MAX_PIXELS / Math.max(viewport.width * viewport.height, 1)))
        : pixelFactor
      const key = bitmapCacheKey(candidate.kind, candidate.slot, candidate.file, candidate.pageNumber, viewport, candidatePixelFactor)
      await renderPageOffscreen(page, viewport, candidatePixelFactor, key)
    } catch (cause) {
      if (cause?.name !== 'RenderingCancelledException') return
    }
  }

  function scheduleAdjacentPrefetch(request, pageNumber, referencePageNumber, baseScale, pixelFactor, requestGeneration) {
    const nextTargetPage = pageNumber + 1
    if (nextTargetPage <= request.targetDocument.numPages) {
      const candidate = { kind: 'target', document: request.targetDocument, file: request.targetFile, slot: request.projectIndex, pageNumber: nextTargetPage }
      prefetchHandle = scheduleIdle(async () => {
        prefetchHandle = undefined
        await prefetchCandidate(request, candidate, baseScale, pixelFactor, requestGeneration)
      })
    }
    const nextReferencePage = referencePageNumber + 1
    if (!request.referenceDocument || !referencePageNumber || nextReferencePage > request.referenceDocument.numPages) return
    const referenceCandidate = {
      kind: 'reference', document: request.referenceDocument, file: request.referenceFile,
      slot: referenceFileSlot(request.referenceFile, request.referenceIndex), pageNumber: nextReferencePage,
    }
    referencePrefetchTimer = scheduleDelay(() => {
      referencePrefetchTimer = undefined
      if (!requestIsCurrent(request, requestGeneration)) return
      referencePrefetchHandle = scheduleIdle(async () => {
        referencePrefetchHandle = undefined
        await prefetchCandidate(request, referenceCandidate, baseScale, pixelFactor, requestGeneration)
      })
    }, REFERENCE_PREFETCH_SETTLE_MS)
  }

  async function render(fitAfter = false, indicateLoading = true) {
    if (!state.targetDocument.value || !state.pdfCanvas.value) return
    const requestGeneration = ++generation
    cancelUpgradeWait()
    cancelPrefetch()
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
      const referencePageNumber = request.referenceDocument
        ? Math.min(Math.max(1, request.referencePage), request.referenceDocument.numPages)
        : 0
      const targetLoad = request.targetDocument.getPage(pageNumber)
      let referenceResult
      // Handle reference failures immediately, even if the design page is
      // still loading, without making the first design paint wait for it.
      const referenceLoad = referencePageNumber
        ? request.referenceDocument.getPage(referencePageNumber).then(
          page => (referenceResult = { page }), error => (referenceResult = { error }),
        )
        : Promise.resolve({ page: null })
      const targetPage = await targetLoad
      if (!requestIsCurrent(request, requestGeneration)) return

      const baseScale = 1.35
      const targetViewport = targetPage.getViewport({ scale: baseScale })
      let referenceViewport = referenceResult?.page?.getViewport({ scale: baseScale }) || null
      // Reserve the reference column while it loads. Keep the target origin
      // and drawing scale fixed, and do not refit the user's view on arrival.
      const reservedReference = referencePageNumber
        ? referenceViewport || state.canvasLayout.value.reference || targetViewport : null
      let layout = calculateCanvasPageLayout(targetViewport, reservedReference, request.displayReference)
      const headerHeight = layout.target.y
      const resolutionScale = Math.max(1, Math.min(4, state.zoom.value))
      const desiredPixelFactor = (window.devicePixelRatio || 1) * resolutionScale
      const profile = state.performanceProfile.value
      let pixelFactor = boundedCanvasPixelFactor(layout.width, layout.height, desiredPixelFactor, profile)
      const canvas = state.pdfCanvas.value
      let targetBitmap = null
      let targetBitmapFactor = 0
      let referenceBitmap = null
      let referenceBitmapFactor = 0
      let referenceStatus = 'loading'
      let fitted = false

      async function paintFrame() {
        if (!targetBitmap || !requestIsCurrent(request, requestGeneration)) return
        const logicalWidth = layout.width
        const logicalHeight = layout.height
        const referenceX = layout.reference?.x || 0
        const frameFactor = boundedCanvasPixelFactor(logicalWidth, logicalHeight,
          Math.max(targetBitmapFactor, referenceBitmapFactor, Number.EPSILON), profile)
        canvas.width = Math.ceil(logicalWidth * frameFactor)
        canvas.height = Math.ceil(logicalHeight * frameFactor)
        canvas.style.width = `${logicalWidth}px`
        canvas.style.height = `${logicalHeight}px`
        state.canvasLayout.value = layout
        const context = canvas.getContext('2d', { alpha: false })
        if (!context) throw new Error('浏览器无法创建 Canvas 2D 上下文，请检查硬件加速或浏览器策略')
        context.setTransform(1, 0, 0, 1, 0, 0)
        context.fillStyle = '#dfe5e8'
        context.fillRect(0, 0, canvas.width, canvas.height)
        context.drawImage(targetBitmap, 0, headerHeight * frameFactor, targetViewport.width * frameFactor, targetViewport.height * frameFactor)
        if (referenceBitmap && referenceViewport) {
          context.drawImage(referenceBitmap, referenceX * frameFactor, headerHeight * frameFactor, referenceViewport.width * frameFactor, referenceViewport.height * frameFactor)
        }
        context.setTransform(frameFactor, 0, 0, frameFactor, 0, 0)
        context.fillStyle = '#173e57'
        context.font = '600 13px Microsoft YaHei UI, sans-serif'
        context.fillText('待标识 ISO PDF', 10, 22)
        if (layout.reference) {
          context.fillStyle = '#2d8b89'
          const referenceCaption = state.referencePdfs.value.length > 1
            ? `对照 PDF ${request.referenceIndex + 1}/${state.referencePdfs.value.length} · P${referencePageNumber} · ${request.referenceFile?.name || ''}`
            : `对照 PDF · P${referencePageNumber}`
          const suffix = referenceStatus === 'failed' ? ' · 加载失败' : !referenceBitmap ? ' · 加载中' : ''
          context.fillText(referenceCaption + suffix, referenceX + 10, 22)
          context.strokeStyle = '#8798a2'
          context.lineWidth = 1
          context.beginPath()
          context.moveTo(targetViewport.width + 14, 0)
          context.lineTo(targetViewport.width + 14, logicalHeight)
          context.stroke()
        }
        if (fitAfter && !fitted) {
          fitted = true
          await nextTick().then(() => fitCanvas(false))
        }
      }

      async function renderProgressivePage({ page, viewport, kind, slot, file, number, assign }) {
        const highKey = bitmapCacheKey(kind, slot, file, number, viewport, pixelFactor)
        const highWidth = Math.ceil(viewport.width * pixelFactor)
        const highHeight = Math.ceil(viewport.height * pixelFactor)
        const cachedHigh = await restorePageBitmap(highKey, highWidth, highHeight)
        if (!requestIsCurrent(request, requestGeneration)) return false
        if (cachedHigh) {
          assign(cachedHigh, pixelFactor)
          await paintFrame()
          return true
        }

        const highPixels = viewport.width * viewport.height * pixelFactor * pixelFactor
        const previewFactor = Math.min(pixelFactor, Math.sqrt(PREVIEW_MAX_PIXELS / Math.max(viewport.width * viewport.height, 1)))
        const usePreview = highPixels > PREVIEW_MAX_PIXELS && previewFactor < pixelFactor * .92
        if (usePreview) {
          const previewKey = bitmapCacheKey(kind, slot, file, number, viewport, previewFactor)
          const preview = await renderPageOffscreen(page, viewport, previewFactor, previewKey, { restore: false, persist: false })
          if (!requestIsCurrent(request, requestGeneration)) return false
          assign(preview, previewFactor)
          await paintFrame()
          if (profile.key === 'low') return true
          if (!await waitForUpgrade() || !requestIsCurrent(request, requestGeneration)) return false
        }

        const high = await renderPageOffscreen(page, viewport, pixelFactor, highKey, { restore: false, persist: true })
        if (!requestIsCurrent(request, requestGeneration)) return false
        assign(high, pixelFactor)
        await paintFrame()
        return true
      }

      const targetComplete = await renderProgressivePage({
        page: targetPage, viewport: targetViewport, kind: 'target', slot: request.projectIndex,
        file: request.targetFile, number: pageNumber,
        assign(bitmap, factor) { targetBitmap = bitmap; targetBitmapFactor = factor },
      })
      if (!targetComplete || !requestIsCurrent(request, requestGeneration)) return
      const settledReference = await referenceLoad
      if (!requestIsCurrent(request, requestGeneration)) return
      if (settledReference.error) {
        referenceStatus = 'failed'
        await paintFrame()
        throw new Error(`对照页加载失败：${settledReference.error.message || settledReference.error}`)
      }
      const referencePage = settledReference.page
      referenceViewport = referencePage?.getViewport({ scale: baseScale }) || null
      layout = calculateCanvasPageLayout(targetViewport, referenceViewport, request.displayReference)
      pixelFactor = boundedCanvasPixelFactor(layout.width, layout.height, desiredPixelFactor, profile)
      if (referencePage && referenceViewport) {
        const referenceComplete = await renderProgressivePage({
          page: referencePage, viewport: referenceViewport, kind: 'reference',
          slot: referenceFileSlot(request.referenceFile, request.referenceIndex),
          file: request.referenceFile, number: referencePageNumber,
          assign(bitmap, factor) { referenceBitmap = bitmap; referenceBitmapFactor = factor },
        })
        if (!referenceComplete || !requestIsCurrent(request, requestGeneration)) return
      }
      scheduleAdjacentPrefetch(request, pageNumber, referencePageNumber, baseScale, pixelFactor, requestGeneration)
    } catch (cause) {
      if (requestGeneration === generation && cause?.name !== 'RenderingCancelledException') state.error.value = `Canvas 渲染失败：${cause.message || cause}`
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
    cancelPrefetch()
    invalidate()
    clearCache()
  }

  return { rendering, render, schedule, invalidate, cancelTasks, clearCache, dispose }
}
