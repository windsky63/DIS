<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import {
  isReferenceMessage,
  referenceChannelFromLocation,
  referenceChannelName,
  referenceMessage,
} from '../detachedReferenceProtocol.js'
import { createReferenceViewerState } from '../composables/useReferenceWindowViewer.js'
import { createDetachedReferenceFocus, resolveDetachedReferenceViewport } from '../detachedReferenceFocus.js'
import { loadPdfDocument } from '../services/pdfDocument.js'
import { useCanvasViewport } from '../composables/useCanvasViewport.js'

import { useThemeSettings } from '../composables/useThemeSettings.js'

useThemeSettings()

const channelId = referenceChannelFromLocation()
const channel = channelId && typeof BroadcastChannel !== 'undefined'
  ? new BroadcastChannel(referenceChannelName(channelId))
  : null
const viewer = createReferenceViewerState()
const connected = ref(false)
const loading = ref(false)
const error = ref('')
const pageCount = ref(0)
const pdfDocument = shallowRef(null)
const canvas = ref(null)
const viewportElement = ref(null)
const layout = ref({ width: 1, height: 1 })
const hintFocus = ref(null)
const showAllHints = ref(false)
let renderTask = null
let loadGeneration = 0
let resolutionTimer

const {
  zoom, pan, panState, surfaceStyle, fit, zoomBy, onWheel,
  startPan, movePan, endPan,
} = useCanvasViewport({
  canvasLayout: layout,
  canvasViewport: viewportElement,
  onResolutionChange: scheduleResolutionRender,
})

const title = computed(() => viewer.active.value?.fileName || '等待主窗口发送对照图')
const subtitle = computed(() => viewer.following.value ? '正在跟随设计图匹配页' : '已暂停跟随，可独立翻页')
const showReferenceHints = computed(() => viewer.active.value?.hintsLocation !== 'design')
const hintPageInfo = computed(() => Number(viewer.active.value?.page) === Number(viewer.currentPage.value) ? viewer.active.value?.pageInfo : null)
const hintGroups = computed(() => {
  const definitions = [
    ['weld', '焊口', '#d4143c'], ['valve', '阀门', '#1769d2'],
    ['flange', '法兰', '#1769d2'], ['support', '支架', '#1769d2'],
  ]
  const items = hintPageInfo.value?.items || []
  return definitions.map(([key, title, color]) => ({ key, title, color, items: items.filter(item => item.type === key) }))
})
const markerItems = computed(() => {
  const snapshot = viewer.active.value
  const pageInfo = snapshot?.pageInfo
  if (!pageInfo || Number(snapshot.page) !== Number(viewer.currentPage.value)) return []
  const focus = hintFocus.value
  const items = showAllHints.value
    ? pageInfo.items || []
    : focus && focus.page === pageInfo.page && focus.file === snapshot.hintDocumentFile ? [focus] : []
  const colors = { weld: '#d4143c', valve: '#1769d2', flange: '#1769d2', support: '#1769d2' }
  return items.map((item, index) => ({
    key: `${item.type}-${item.label}-${index}`,
    label: item.label,
    type: item.type,
    item,
    style: {
      left: `${Number(item.point?.[0]) / Math.max(1, pageInfo.width) * layout.value.width}px`,
      top: `${Number(item.point?.[1]) / Math.max(1, pageInfo.height) * layout.value.height}px`,
      '--reference-marker-color': colors[item.type] || '#ff8a3d',
    },
  }))
})

function post(type, payload) {
  channel?.postMessage(referenceMessage(channelId, type, payload))
}

function handleMessage(event) {
  const message = event?.data
  if (!isReferenceMessage(message, channelId)) return
  if (message.type === 'reference-state') {
    connected.value = true
    error.value = ''
    const previous = viewer.active.value
    viewer.accept(message.payload)
    if (message.payload?.hintsLocation === 'design' || previous?.fileKey !== message.payload?.fileKey || Number(previous?.page) !== Number(message.payload?.page)) {
      showAllHints.value = Boolean(message.payload?.showAll)
      if (message.payload?.focus) void selectHint(message.payload.focus, { toggle: false })
      else hintFocus.value = null
    }
    document.body.classList.toggle('tooltips-disabled', message.payload?.tooltipsEnabled === false)
  } else if (message.type === 'parent-closing') {
    connected.value = false
    error.value = '主编辑窗口已关闭或退出，对照窗口已断开。'
  }
}

async function loadSnapshot(snapshot) {
  const generation = ++loadGeneration
  renderTask?.cancel?.()
  const previous = pdfDocument.value
  pdfDocument.value = null
  pageCount.value = 0
  previous?.destroy?.()
  if (!snapshot?.file) return
  loading.value = true
  error.value = ''
  try {
    const documentProxy = await loadPdfDocument(snapshot.file)
    if (generation !== loadGeneration) { documentProxy.destroy?.(); return }
    pdfDocument.value = documentProxy
    pageCount.value = documentProxy.numPages
    viewer.goToPage(viewer.currentPage.value, pageCount.value)
    await nextTick()
    await renderPage(true)
    if (hintFocus.value) centerHint(hintFocus.value)
  } catch (cause) {
    if (generation === loadGeneration) error.value = `对照 PDF 加载失败：${cause?.message || cause}`
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

async function renderPage(fitAfter = false) {
  const documentProxy = pdfDocument.value
  const targetCanvas = canvas.value
  if (!documentProxy || !targetCanvas) return
  const generation = loadGeneration
  renderTask?.cancel?.()
  try {
    const page = await documentProxy.getPage(viewer.currentPage.value)
    if (generation !== loadGeneration) return
    const pageViewport = page.getViewport({ scale: 1.35 })
    layout.value = { width: pageViewport.width, height: pageViewport.height }
    const pixelFactor = Math.max(1, Math.min(4, (window.devicePixelRatio || 1) * Math.max(1, zoom.value)))
    targetCanvas.width = Math.ceil(pageViewport.width * pixelFactor)
    targetCanvas.height = Math.ceil(pageViewport.height * pixelFactor)
    targetCanvas.style.width = `${pageViewport.width}px`
    targetCanvas.style.height = `${pageViewport.height}px`
    const context = targetCanvas.getContext('2d', { alpha: false })
    if (!context) throw new Error('浏览器无法创建 Canvas 2D 上下文')
    renderTask = page.render({
      canvasContext: context,
      viewport: pageViewport,
      transform: [pixelFactor, 0, 0, pixelFactor, 0, 0],
    })
    await renderTask.promise
    if (fitAfter) await nextTick().then(() => fit(false))
  } catch (cause) {
    if (cause?.name !== 'RenderingCancelledException') error.value = `对照 PDF 渲染失败：${cause?.message || cause}`
  }
}

function scheduleResolutionRender() {
  clearTimeout(resolutionTimer)
  resolutionTimer = window.setTimeout(() => { void renderPage(false) }, 140)
}

function toggleFollowing(value) {
  viewer.setFollowing(value)
}

function stepPage(delta) {
  viewer.navigate(viewer.currentPage.value + delta, pageCount.value)
}

function jumpToPage(page) {
  viewer.navigate(page, pageCount.value)
}

async function selectHint(item, { toggle = true } = {}) {
  const focus = createDetachedReferenceFocus(item, viewer.active.value)
  if (!focus) return
  const selected = hintFocus.value?.file === focus.file && hintFocus.value?.page === focus.page
    && hintFocus.value?.label === focus.label && hintFocus.value?.type === focus.type
  if (toggle && selected) {
    hintFocus.value = null
    return
  }
  hintFocus.value = focus
  showAllHints.value = false
  await nextTick()
  centerHint(focus)
}

function centerHint(focus) {
  const viewport = viewportElement.value
  const nextView = resolveDetachedReferenceViewport({
    focus,
    pageInfo: hintPageInfo.value,
    layout: layout.value,
    viewport: { width: viewport?.clientWidth, height: viewport?.clientHeight },
    zoom: zoom.value,
  })
  if (!nextView) return
  zoom.value = nextView.zoom
  pan.value = nextView.pan
  scheduleResolutionRender()
}

function toggleAllHints() {
  showAllHints.value = !showAllHints.value
  hintFocus.value = null
}

function clearHintFocus() {
  if (!showAllHints.value) hintFocus.value = null
}

function startViewerPan(event) {
  if (event.button === 0) {
    Object.defineProperty(event, 'button', { configurable: true, value: 1 })
  }
  startPan(event)
}

watch(() => viewer.active.value?.fileKey, () => { void loadSnapshot(viewer.active.value) })
watch(viewer.currentPage, (page, previous) => {
  if (page !== previous && pdfDocument.value) void renderPage(false).then(() => {
    if (hintFocus.value) centerHint(hintFocus.value)
  })
})

onMounted(() => {
  if (!channel) {
    error.value = '无法建立对照窗口通信，请从主系统重新打开。'
    return
  }
  channel.addEventListener('message', handleMessage)
  post('ready')
})

onBeforeUnmount(() => {
  post('closed')
  channel?.removeEventListener('message', handleMessage)
  channel?.close()
  clearTimeout(resolutionTimer)
  renderTask?.cancel?.()
  loadGeneration += 1
  pdfDocument.value?.destroy?.()
})
</script>

<template>
  <v-app class="reference-window-app">
    <v-app-bar color="header" density="compact" class="reference-window-toolbar">
      <div class="reference-document-meta ml-3">
        <v-chip size="small" color="secondary">对照 PDF</v-chip>
        <div class="reference-window-title"><strong>{{ title }}</strong><span>{{ subtitle }}</span></div>
        <span class="reference-connection-dot" :class="{ connected }" :title="connected ? '主窗口已连接' : '等待主窗口连接'" />
      </div>
      <v-divider vertical class="mx-3" />
      <div class="reference-page-tools" aria-label="对照图页码导航">
        <v-btn size="small" icon variant="text" class="reference-toolbar-button" aria-label="第一页" :disabled="!pdfDocument || viewer.currentPage.value <= 1" @click="jumpToPage(1)"><span aria-hidden="true">«</span><v-tooltip activator="parent">第一页</v-tooltip></v-btn>
        <v-btn size="small" icon variant="text" class="reference-toolbar-button" aria-label="上一页" :disabled="!pdfDocument || viewer.currentPage.value <= 1" @click="stepPage(-1)"><span aria-hidden="true">‹</span><v-tooltip activator="parent">上一页</v-tooltip></v-btn>
        <span class="reference-page-total">P{{ viewer.currentPage.value }} / {{ pageCount || '—' }}</span>
        <v-btn size="small" icon variant="text" class="reference-toolbar-button" aria-label="下一页" :disabled="!pdfDocument || viewer.currentPage.value >= pageCount" @click="stepPage(1)"><span aria-hidden="true">›</span><v-tooltip activator="parent">下一页</v-tooltip></v-btn>
        <v-btn size="small" icon variant="text" class="reference-toolbar-button" aria-label="最后一页" :disabled="!pdfDocument || viewer.currentPage.value >= pageCount" @click="jumpToPage(pageCount)"><span aria-hidden="true">»</span><v-tooltip activator="parent">最后一页</v-tooltip></v-btn>
      </div>
      <v-spacer />
      <div class="reference-view-tools">
        <button type="button" class="reference-follow-button" :class="{ active: viewer.following.value }" :aria-pressed="viewer.following.value" @click="toggleFollowing(!viewer.following.value)"><i aria-hidden="true" />{{ viewer.following.value ? '跟随设计页' : '独立浏览' }}</button>
        <v-divider vertical class="mx-2" />
        <v-btn size="small" variant="text" class="reference-toolbar-button" @click="zoomBy(.8)">−<v-tooltip activator="parent">缩小</v-tooltip></v-btn>
        <span class="reference-zoom-indicator">{{ Math.round(zoom * 100) }}%</span>
        <v-btn size="small" variant="text" class="reference-toolbar-button" @click="zoomBy(1.25)">+<v-tooltip activator="parent">放大</v-tooltip></v-btn>
        <v-btn size="small" variant="text" class="reference-toolbar-button" @click="fit">适合<v-tooltip activator="parent">适合窗口</v-tooltip></v-btn>
      </div>
    </v-app-bar>

    <v-main>
      <v-alert v-if="error" type="error" variant="tonal" density="compact" class="ma-3">{{ error }}</v-alert>
      <aside v-if="showReferenceHints" class="reference-hints-panel">
        <div class="reference-hints-panel__header"><div><strong>对照图提示</strong><span>{{ viewer.active.value?.hintDocumentFile || title }} · 第 {{ viewer.currentPage.value }} 页</span></div><v-chip size="x-small" color="secondary" variant="tonal">{{ hintPageInfo?.items?.length || 0 }}</v-chip></div>
        <div class="reference-hints-panel__scroll">
          <v-alert v-if="!hintPageInfo" type="info" variant="tonal" density="compact">当前对照页暂无识别提示。</v-alert>
          <template v-else>
            <v-btn block class="reference-show-all-button" :color="showAllHints ? 'secondary' : 'primary'" :variant="showAllHints ? 'flat' : 'tonal'" @click="toggleAllHints">{{ showAllHints ? '隐藏全部对照标识' : '显示全部对照标识' }}</v-btn>
            <section v-for="group in hintGroups" :key="group.key" class="reference-hint-group">
              <div class="reference-hint-group__heading"><span><i :style="{ backgroundColor: group.color }" />{{ group.title }}</span><strong>{{ group.items.length }}</strong></div>
              <v-list v-if="group.items.length" density="compact" class="reference-hint-list" border>
                <v-list-item v-for="item in group.items" :key="`${group.key}-${item.label}-${(item.point || []).join('-')}`" :active="hintFocus?.label === item.label && hintFocus?.type === item.type" color="secondary" @click="selectHint(item)">
                  <v-list-item-title>{{ item.label }}</v-list-item-title>
                  <v-list-item-subtitle>{{ item.geometryVerified === false ? '引线定位 · 几何待确认' : '已识别 · 点击定位' }}</v-list-item-subtitle>
                  <template #append><span class="reference-hint-locate" aria-hidden="true">⌖</span></template>
                </v-list-item>
              </v-list>
              <div v-else class="reference-hint-empty">未识别到{{ group.title }}</div>
            </section>
          </template>
        </div>
      </aside>
      <div
        ref="viewportElement"
        class="reference-window-viewport"
        :class="{ panning: panState, 'reference-window-viewport--with-hints': showReferenceHints }"
        @wheel.prevent="onWheel"
        @pointerdown="startViewerPan"
        @pointermove="movePan"
        @pointerup="endPan"
        @pointercancel="endPan"
      >
        <div class="reference-window-surface" :style="surfaceStyle" @click="clearHintFocus">
          <canvas ref="canvas" />
          <div
            v-for="marker in markerItems"
            :key="marker.key"
            class="reference-focus-marker reference-focus-marker--all"
            :class="`reference-focus-marker--${marker.type}`"
            :style="marker.style"
          @click.stop="selectHint(marker.item)"><span>{{ marker.label }}</span></div>
        </div>
        <div v-if="!pdfDocument && !loading" class="reference-window-empty">
          <strong>等待对照图</strong>
          <span>请保持主编辑窗口开启，并选择含对照 PDF 的图纸任务。</span>
        </div>
        <v-progress-circular v-if="loading" class="reference-window-loading" indeterminate color="secondary" size="42" />
      </div>
    </v-main>
  </v-app>
</template>

<style scoped>
.reference-window-app { background: #dfe5e8; }
.reference-window-toolbar :deep(.v-toolbar__content) { overflow: visible; }
.reference-document-meta, .reference-page-tools, .reference-view-tools { display: flex; align-items: center; }
.reference-view-tools { margin-right: 12px; }
.reference-document-meta { min-width: 250px; gap: 9px; }.reference-window-title { min-width: 0; display: grid; line-height: 1.15; }
.reference-window-title strong { max-width: 260px; overflow: hidden; color: #fff; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.reference-window-title span { color: #a9bec9; font-size: 10px; }
.reference-connection-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: #d59c52; box-shadow: 0 0 0 3px rgba(213,156,82,.13); }.reference-connection-dot.connected { background: #78d0ad; box-shadow: 0 0 0 3px rgba(120,208,173,.14); }
.reference-page-tools { gap: 3px; }.reference-page-total { margin-inline: 6px; color: #c7d6dd; font: 700 11px Consolas, monospace; white-space: nowrap; }
.reference-toolbar-button { border: 0 !important; background: transparent !important; box-shadow: none !important; font-size: 10px !important; }
.reference-toolbar-button:hover { background: #304653 !important; }
.reference-follow-button { height: 30px; display: inline-flex; align-items: center; gap: 7px; padding: 0 10px; border: 0; border-radius: 5px; background: transparent; color: #c6d4da; font: inherit; font-size: 10px; cursor: pointer; }.reference-follow-button:hover { background: rgba(255,255,255,.1); }.reference-follow-button i { width: 7px; height: 7px; border-radius: 50%; background: #81939b; }.reference-follow-button.active { background: rgba(45,139,137,.18); color: #a9e1dc; }.reference-follow-button.active i { background: #78d0ad; box-shadow: 0 0 0 3px rgba(120,208,173,.12); }
.reference-zoom-indicator { min-width: 48px; color: #dce7ec; font-size: 11px; text-align: center; }
.reference-window-viewport { position: fixed; inset: 48px 0 0; overflow: hidden; background: radial-gradient(circle at center, #edf1f3 0, #ccd5da 100%); cursor: grab; touch-action: none; }
.reference-window-viewport--with-hints { left: 286px; }
.reference-hints-panel { position: fixed; z-index: 2; left: 0; top: 48px; bottom: 0; width: 286px; display: flex; flex-direction: column; border-right: 1px solid #b9c8cf; background: #f7fafb; box-shadow: 5px 0 18px rgba(16,42,67,.09); }
.reference-hints-panel__header { min-height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 10px 12px; border-bottom: 1px solid #d4dfe4; background: #fff; }.reference-hints-panel__header > div { min-width: 0; display: grid; gap: 2px; }.reference-hints-panel__header strong { color: #173e57; font-size: 13px; }.reference-hints-panel__header span { overflow: hidden; color: #6b7f89; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.reference-hints-panel__scroll { flex: 1; overflow: auto; padding: 12px; }.reference-hints-panel .reference-hint-group { margin-top: 14px; }.reference-hints-panel .reference-hint-list { background: #fff; }
.reference-window-viewport.panning { cursor: grabbing; }
.reference-window-surface { position: absolute; left: 0; top: 0; transform-origin: 0 0; box-shadow: 0 12px 34px rgba(16, 42, 67, .2); }
.reference-window-surface canvas { position: absolute; inset: 0; display: block; background: #fff; }
.reference-window-empty { position: absolute; inset: 0; display: grid; place-content: center; gap: 8px; color: #58717e; text-align: center; }
.reference-window-empty strong { color: #173e57; font-size: 20px; }
.reference-window-empty span { font-size: 12px; }
.reference-window-loading { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); }
@media (max-width: 900px) {
  .reference-window-title span, .reference-page-tools > :first-child, .reference-page-tools > :last-child { display: none; }
  .reference-document-meta { min-width: 160px; }.reference-window-title strong { max-width: 130px; }
  .reference-follow-button { width: 30px; padding: 0; justify-content: center; overflow: hidden; color: transparent; gap: 0; }.reference-follow-button i { flex: 0 0 auto; }
}

.reference-window-app { background: rgb(var(--v-theme-background)); }
.reference-window-title strong, .reference-page-total, .reference-zoom-indicator, .reference-follow-button { color: rgb(var(--v-theme-on-header)); }
.reference-window-title span { color: rgb(var(--v-theme-on-surface-muted)); }
.reference-toolbar-button:hover, .reference-follow-button:hover { background: rgb(var(--v-theme-surface-muted)) !important; }
.reference-follow-button.active { background: rgb(var(--v-theme-surface-selected)); color: rgb(var(--v-theme-on-surface)); }
.reference-hints-panel, .reference-hints-panel__header, .reference-hints-panel .reference-hint-list { background: rgb(var(--v-theme-surface)); border-color: rgb(var(--v-theme-outline)); }
.reference-hints-panel__header strong, .reference-window-empty strong { color: rgb(var(--v-theme-on-surface)); }
.reference-hints-panel__header span, .reference-window-empty { color: rgb(var(--v-theme-on-surface-muted)); }
.reference-window-viewport { background: rgb(var(--v-theme-background)); }
</style>
