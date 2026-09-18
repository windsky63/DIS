import { nextTick } from 'vue'

import { isDesignComponent, isSpecialMarker, numberDocumentResult } from '../numbering.js'
import { createSpecialMarkerAppearance } from '../defaultMarkerAppearance.js'
import { createPastedMarker, matchesModificationType } from '../markerClipboard.js'
import { manualLabelPosition } from '../manualMarkerPlacement.js'
import { manualMarkerNumber, manualNumberingSequenceKey } from '../manualNumberingSettings.js'
import { translateMarkerPair } from './useMarkerPresentation.js'

export function useMarkerEditor({ state, operations }) {
  const {
    result, pageData, pages, candidates, selectedCandidate,
    targetDocument, currentPage, previewPage, projectResults, activeProjectIndex, manualAddMode,
    manualAddType, canvasSurface, canvasLayout, canvasPointerPosition, referenceFocus, referenceShowAll,
    startNumber, numberPrefix, numberSuffix, useReferenceNumber, selectedId, swapSourceId,
    editingId, editingValue,
    pendingHistory, labelDragState, anchorDragState, groupDragState, manualLeaderLength, manualNumberingSettings, error,
  } = state
  let copiedMarker = null
  let pasteSequence = 0
  const manualNumberingSessions = new Map()

  function nextManualNumber(type) {
    const sequenceKey = manualNumberingSequenceKey(manualNumberingSettings?.value, type)
    const sessionKey = type === 'weld' ? type : `${type}:${Number(pageData.value?.page) || Number(currentPage.value) || 1}`
    let session = manualNumberingSessions.get(sessionKey)
    if (!session || session.document !== result.value || session.sequenceKey !== sequenceKey) {
      session = {
        document: result.value,
        sequenceKey,
        count: 0,
      }
      manualNumberingSessions.set(sessionKey, session)
    }

    const generated = manualMarkerNumber(manualNumberingSettings?.value, type, session.count)
    session.count += 1
    return generated
  }

  async function ensureManualWorkspace() {
    if (result.value && pageData.value) return true
    if (!targetDocument.value) { error.value = '请先选择并加载待标识 ISO PDF。'; return false }
    const generatedPages = await Promise.all(Array.from({ length: targetDocument.value.numPages }, async (_, index) => {
      const pdfPage = await targetDocument.value.getPage(index + 1)
      const viewport = pdfPage.getViewport({ scale: 1 })
      return {
        page: index + 1, width: viewport.width, height: viewport.height, candidates: [], candidateCount: 0,
        weldCandidateCount: 0, designComponentCount: 0, layoutObstacles: { textRects: [], processSegments: [] },
      }
    }))
    result.value = {
      schema: 'weld-marker.topology.v1', status: 'manual', jobId: null, pages: generatedPages,
      analyzedRange: [1, generatedPages.length], manualWorkspace: true,
    }
    currentPage.value = Math.max(1, Math.min(generatedPages.length, previewPage.value))
    projectResults.value[activeProjectIndex.value] = result.value
    await nextTick()
    return true
  }

  async function toggleManualAddMode(type = 'weld') {
    if (!await ensureManualWorkspace()) return
    const sameActiveType = manualAddMode.value && manualAddType.value === type
    manualAddType.value = type
    manualAddMode.value = !sameActiveType
    if (manualAddMode.value && ['weld', 'valve', 'flange', 'support'].includes(type)) {
      operations.selectMarkerAppearanceTab?.(type)
    }
  }

  function mergeStagedManualCandidates(snapshot, stagedPages) {
    if (!snapshot?.pages?.length || !stagedPages?.length) return snapshot
    const stagedByPage = new Map(stagedPages.map(page => [page.page, (page.candidates || []).filter(item => item.origin === 'manual')]))
    snapshot.pages.forEach(page => {
      const existingIds = new Set((page.candidates || []).map(item => item.id))
      const additions = (stagedByPage.get(page.page) || []).filter(item => !existingIds.has(item.id)).map(operations.cloneValue)
      if (!additions.length) return
      page.candidates = [...(page.candidates || []), ...additions]
      page.candidateCount = page.candidates.length
    })
    return snapshot
  }

  function onCanvasClick(event) {
    if (referenceFocus.value) referenceFocus.value = null
    if (referenceShowAll.value) referenceShowAll.value = false
    canvasPointerPosition.value = { clientX: event.clientX, clientY: event.clientY }
  }

  function formattedWeldNumber(number) {
    return `${numberPrefix.value || ''}${number}${numberSuffix.value || ''}`
  }

  function addManualAtClientPoint(clientX, clientY) {
    if (!manualAddMode.value || !canvasSurface.value || !pageData.value) return
    const bounds = canvasSurface.value.getBoundingClientRect()
    const layout = canvasLayout.value
    const worldX = (clientX - bounds.left) / bounds.width * layout.width
    const worldY = (clientY - bounds.top) / bounds.height * layout.height
    const localX = worldX - layout.target.x
    const localY = worldY - layout.target.y
    if (localX < 0 || localY < 0 || localX > layout.target.width || localY > layout.target.height) {
      error.value = '人工标识只能添加在待标识 ISO PDF 区域内。'
      return
    }
    const page = pageData.value
    const xNorm = localX / layout.target.width
    const yNorm = localY / layout.target.height
    const x = xNorm * page.width
    const y = yNorm * page.height
    const type = manualAddType.value === 'all' ? 'special' : manualAddType.value
    const typeTitle = ({ weld: '焊口', valve: '阀门', flange: '法兰', support: '支架', special: '特殊标识' })[type]
    operations.beginHistory(`人工增加${typeTitle}`)
    const special = type === 'special'
    const componentType = ['valve', 'flange', 'support'].includes(type) ? type : (special ? 'special' : null)
    const generatedNumber = special
      ? { number: `M${page.candidates.filter(item => item.included !== false && isSpecialMarker(item)).length + 1}`, prefix: 'M' }
      : nextManualNumber(type)
    const label = manualLabelPosition({ x, y }, page.width, page.height, manualLeaderLength.value)
    const specialStyle = special ? createSpecialMarkerAppearance() : null
    const item = {
      id: `p${page.page}-manual-${type}-${Date.now()}`, page: page.page, x, y, xNorm, yNorm,
      labelX: label.x, labelY: label.y,
      labelXNorm: label.x / page.width, labelYNorm: label.y / page.height,
      confidence: 1, tier: 'manual', glyphValidated: false, evidence: '人工添加',
      componentKind: special ? 'special-marker' : (componentType ? 'design-component' : 'manual-weld'), componentType,
      specialMarker: special || undefined,
      autoNumberPrefix: generatedNumber.prefix,
      defaultMarkerStyle: special ? { ...specialStyle } : (componentType ? { shape: 'rectangle', color: '#1769d2' } : { shape: 'circle', color: '#d4143c' }),
      markerStyle: special ? { ...specialStyle } : undefined,
      symbolShape: 'manual', included: true, origin: 'manual',
      number: generatedNumber.number,
      referenceLabel: '', referenceMatched: false,
    }
    page.candidates.push(item)
    page.candidateCount = page.candidates.length
    selectedId.value = item.id
    operations.commitHistory()
    operations.logAudit(`${type}.manual_added`, { id: item.id, number: item.number })
    operations.showNotice(`已人工增加${typeTitle} ${item.number}；可分别拖动定位点和标识框调整引线两端。`)
  }

  function candidateMatchesModificationType(item) {
    return matchesModificationType(item, manualAddType.value)
  }

  function copySelectedCandidate() {
    const item = selectedCandidate.value
    if (!item) { error.value = '请先选中要复制的标识。'; return false }
    copiedMarker = operations.cloneValue(item)
    operations.showNotice(`已复制标识 ${item.number || '?'}，将鼠标移到目标位置后按 Ctrl+V 粘贴。`)
    return true
  }

  function pasteCopiedAtClientPoint(clientX, clientY) {
    if (!copiedMarker) { error.value = '请先选中标识并按 Ctrl+C 复制。'; return false }
    if (!canvasSurface.value || !pageData.value || !Number.isFinite(clientX) || !Number.isFinite(clientY)) {
      error.value = '请把鼠标移到待标识 PDF 的目标位置后再粘贴。'
      return false
    }
    const bounds = canvasSurface.value.getBoundingClientRect()
    const layout = canvasLayout.value
    const worldX = (clientX - bounds.left) / bounds.width * layout.width
    const worldY = (clientY - bounds.top) / bounds.height * layout.height
    const localX = worldX - layout.target.x
    const localY = worldY - layout.target.y
    if (localX < 0 || localY < 0 || localX > layout.target.width || localY > layout.target.height) {
      error.value = '标识只能粘贴在待标识 ISO PDF 区域内。'
      return false
    }
    const page = pageData.value
    const xNorm = localX / layout.target.width
    const yNorm = localY / layout.target.height
    const item = createPastedMarker(copiedMarker, {
      id: `p${page.page}-copy-${Date.now()}-${++pasteSequence}`,
      page: page.page, pageWidth: page.width, pageHeight: page.height, xNorm, yNorm,
    })
    operations.beginHistory('粘贴标识')
    page.candidates.push(item)
    page.candidateCount = page.candidates.length
    selectedId.value = item.id
    operations.commitHistory()
    operations.logAudit('marker.pasted', { id: item.id, number: item.number, componentType: item.componentType || 'weld' })
    operations.showNotice(`已粘贴标识 ${item.number || '?'}；可继续调整端点、标识框或编号。`)
    return true
  }

  function numberResult(documentResult, startingNumberValue, options = {}) {
    numberDocumentResult(documentResult, startingNumberValue, {
      useReferenceNumber: useReferenceNumber.value,
      formatWeldNumber: formattedWeldNumber,
      ...options,
    })
  }

  function pageMatchSummary(page) {
    if (page?.detailsLoaded === false && page?.matchSummary) return page.matchSummary
    const included = (page?.candidates || []).filter(item => item.included !== false && !isDesignComponent(item) && !isSpecialMarker(item))
    const unmatched = included.filter(item => !item.referenceMatched || !String(item.referenceLabel || '').trim()).length
    const unresolved = Math.max(0, Number(page?.reference?.unresolvedCalloutGap) || 0)
    return { unmatched, unresolved, complete: included.length > 0 && unmatched === 0 && unresolved === 0 }
  }

  function swapSelectedWeldNumbers() {
    const selected = selectedCandidate.value
    if (!selected || selected.included === false) { error.value = '请先选中一个参与编号的焊口，再按 X。'; return }
    if (isSpecialMarker(selected)) { error.value = '特殊标识使用独立编号，不能参与编号交换。'; return }
    if (!swapSourceId.value) { swapSourceId.value = selected.id; operations.showNotice(`已暂存焊口 ${selected.number || '?'}；请选择另一个焊口并再次按 X。`); return }
    if (swapSourceId.value === selected.id) { swapSourceId.value = ''; operations.showNotice('已取消交换编号。'); return }
    const source = candidates.value.find(item => item.id === swapSourceId.value && item.included !== false)
    if (!source) { swapSourceId.value = selected.id; operations.showNotice(`原交换对象已失效，现已暂存焊口 ${selected.number || '?'}。`); return }
    const sourceNumber = source.number
    operations.beginHistory('交换焊口编号')
    source.number = selected.number
    selected.number = sourceNumber
    operations.commitHistory()
    swapSourceId.value = ''
    operations.showNotice(`已交换焊口 ${source.number || '?'} 与 ${selected.number || '?'} 的编号。`)
  }

  function selectCandidate(item) { selectedId.value = item.id }
  function reflowCurrentPageLabelPositions(announce = false) {
    if (!result.value) return
    operations.beginHistory('重新优化当前页标识位置')
    const totals = operations.reflowCurrentPageLabelPositions(pageData.value)
    operations.commitHistory()
    if (announce) operations.showNotice(totals.moved
      ? `标识避让优化完成：重新放置 ${totals.placed} 个标识，其中移动 ${totals.moved} 个。`
      : `已检查 ${totals.placed} 个标识，当前布局已经是本轮避让规则下的最优结果。`)
  }
  function beginInlineEdit(event, item) {
    if (event.button !== 0 || item.included === false) return
    event.stopPropagation(); selectCandidate(item); operations.beginHistory('修改标识编号')
    editingId.value = item.id; editingValue.value = String(item.number || '')
    nextTick(() => document.querySelector('.inline-weld-input')?.select())
  }
  function commitInlineEdit(item) {
    if (editingId.value !== item.id) return
    item.number = editingValue.value.trim(); editingId.value = ''; operations.commitHistory()
    operations.logAudit('weld.number_changed', { id: item.id, number: item.number })
  }
  function cancelInlineEdit() { editingId.value = ''; editingValue.value = ''; pendingHistory.value = null }
  function onApplicationPointerDown(event) {
    if (!editingId.value || event.target?.closest?.('.inline-weld-input')) return
    const item = candidates.value.find(candidate => candidate.id === editingId.value)
    if (item) commitInlineEdit(item); else cancelInlineEdit()
  }
  function startLabelDrag(event, item) {
    if (event.button !== 0 || editingId.value === item.id || item.included === false) return
    event.preventDefault(); event.stopPropagation(); selectCandidate(item); operations.beginHistory('移动焊口标识')
    const layout = canvasLayout.value
    const initialXNorm = item.labelXNorm ?? item.xNorm; const initialYNorm = item.labelYNorm ?? item.yNorm
    labelDragState.value = {
      id: item.id, pointerId: event.pointerId, bounds: canvasSurface.value?.getBoundingClientRect?.() || null,
      layout: { width: layout.width, height: layout.height, target: { ...layout.target } },
      initialWorldX: layout.target.x + initialXNorm * layout.target.width,
      initialWorldY: layout.target.y + initialYNorm * layout.target.height,
      element: event.currentTarget, line: operations.getLeaderLine(item.id), pending: null, animationFrame: 0,
    }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function applyLabelDragPreview(dragState, item) {
    const preview = dragState?.pending
    if (!preview) return
    dragState.animationFrame = 0
    if (dragState.element) dragState.element.style.translate = `${preview.worldX - dragState.initialWorldX}px ${preview.worldY - dragState.initialWorldY}px`
    if (dragState.line) {
      const end = operations.leaderEnd(item, preview)
      dragState.line.setAttribute('x2', String(end.x)); dragState.line.setAttribute('y2', String(end.y))
    }
  }
  function moveLabel(event, item) {
    const dragState = labelDragState.value
    if (dragState?.id !== item.id || !dragState.bounds || !pageData.value) return
    event.preventDefault(); event.stopPropagation()
    const { bounds, layout } = dragState
    const worldX = (event.clientX - bounds.left) / bounds.width * layout.width
    const worldY = (event.clientY - bounds.top) / bounds.height * layout.height
    const xNorm = Math.max(.015, Math.min(.985, (worldX - layout.target.x) / layout.target.width))
    const yNorm = Math.max(.015, Math.min(.985, (worldY - layout.target.y) / layout.target.height))
    dragState.pending = { xNorm, yNorm, x: xNorm * pageData.value.width, y: yNorm * pageData.value.height,
      worldX: layout.target.x + xNorm * layout.target.width, worldY: layout.target.y + yNorm * layout.target.height }
    if (!dragState.animationFrame) dragState.animationFrame = window.requestAnimationFrame(() => applyLabelDragPreview(dragState, item))
  }
  function endLabelDrag(event, item) {
    const dragState = labelDragState.value
    if (dragState?.id !== item.id) return
    event.preventDefault(); event.stopPropagation()
    if (dragState.animationFrame) window.cancelAnimationFrame(dragState.animationFrame)
    applyLabelDragPreview(dragState, item)
    if (dragState.pending) Object.assign(item, { labelXNorm: dragState.pending.xNorm, labelYNorm: dragState.pending.yNorm, labelX: dragState.pending.x, labelY: dragState.pending.y })
    event.currentTarget.releasePointerCapture?.(event.pointerId); labelDragState.value = null
    void nextTick(() => { if (dragState.element) dragState.element.style.translate = '' })
    operations.commitHistory(); operations.logAudit('weld.label_moved', { id: item.id })
  }
  function setLeaderLineElement(id, element) { operations.setLeaderLine(id, element) }
  function startAnchorDrag(event, item) {
    if (event.button !== 0 || item.included === false || !manualAddMode.value || !candidateMatchesModificationType(item)) return
    event.preventDefault(); event.stopPropagation(); selectCandidate(item)
    operations.beginHistory(item.origin === 'manual' ? '移动人工标识引线端点' : '调整识别标识引线端点')
    anchorDragState.value = { id: item.id, pointerId: event.pointerId }; event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function moveAnchor(event, item) {
    if (anchorDragState.value?.id !== item.id || !canvasSurface.value || !pageData.value) return
    event.stopPropagation()
    const bounds = canvasSurface.value.getBoundingClientRect(); const layout = canvasLayout.value
    const worldX = (event.clientX - bounds.left) / bounds.width * layout.width
    const worldY = (event.clientY - bounds.top) / bounds.height * layout.height
    const xNorm = Math.max(0, Math.min(1, (worldX - layout.target.x) / layout.target.width))
    const yNorm = Math.max(0, Math.min(1, (worldY - layout.target.y) / layout.target.height))
    item.xNorm = xNorm; item.yNorm = yNorm; item.x = xNorm * pageData.value.width; item.y = yNorm * pageData.value.height
  }
  function endAnchorDrag(event, item) {
    if (anchorDragState.value?.id !== item.id) return
    event.stopPropagation(); event.currentTarget.releasePointerCapture?.(event.pointerId); anchorDragState.value = null
    operations.commitHistory(); operations.logAudit('weld.anchor_moved', { id: item.id })
  }
  function startGroupDrag(event, item) {
    if (
      event.button !== 0
      || item.included === false
      || !manualAddMode.value
      || !candidateMatchesModificationType(item)
      || labelDragState.value
      || anchorDragState.value
    ) return
    event.preventDefault(); event.stopPropagation(); selectCandidate(item)
    const bounds = canvasSurface.value?.getBoundingClientRect?.()
    const layout = canvasLayout.value
    if (!bounds || !layout?.target?.width || !layout?.target?.height) return
    operations.beginHistory('整体移动标识和引线')
    groupDragState.value = {
      id: item.id,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      bounds,
      layout: { width: layout.width, height: layout.height, target: { ...layout.target } },
      initial: {
        xNorm: item.xNorm,
        yNorm: item.yNorm,
        labelXNorm: item.labelXNorm ?? item.xNorm,
        labelYNorm: item.labelYNorm ?? item.yNorm,
      },
    }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }
  function moveGroup(event, item) {
    const dragState = groupDragState.value
    if (dragState?.id !== item.id || !pageData.value) return
    event.preventDefault(); event.stopPropagation()
    const { bounds, layout } = dragState
    const deltaWorldX = (event.clientX - dragState.startClientX) / bounds.width * layout.width
    const deltaWorldY = (event.clientY - dragState.startClientY) / bounds.height * layout.height
    const translated = translateMarkerPair(
      dragState.initial,
      deltaWorldX / layout.target.width,
      deltaWorldY / layout.target.height,
    )
    Object.assign(item, translated, {
      x: translated.xNorm * pageData.value.width,
      y: translated.yNorm * pageData.value.height,
      labelX: translated.labelXNorm * pageData.value.width,
      labelY: translated.labelYNorm * pageData.value.height,
    })
  }
  function endGroupDrag(event, item) {
    if (groupDragState.value?.id !== item.id) return
    event.preventDefault(); event.stopPropagation()
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    groupDragState.value = null
    operations.commitHistory(); operations.logAudit('weld.marker_group_moved', { id: item.id })
  }
  function toggleCandidate(item) {
    const restoring = item.included === false
    operations.beginHistory(restoring ? '恢复焊口' : '排除误识别焊口')
    if (restoring) { item.included = true; if (item.numberBeforeExclusion !== undefined) item.number = item.numberBeforeExclusion }
    else { item.numberBeforeExclusion = item.number; item.included = false }
    operations.commitHistory(); operations.logAudit('weld.inclusion_changed', { id: item.id, included: item.included })
  }
  function deleteSelectedWeld() {
    const item = selectedCandidate.value
    if (!item) { error.value = '请先选中要删除的焊口。'; return }
    const page = pages.value.find(value => value.page === item.page)
    const index = page?.candidates?.findIndex(value => value.id === item.id) ?? -1
    if (!page || index < 0) { error.value = '选中的焊口已不存在。'; return }
    operations.beginHistory('删除焊口'); page.candidates.splice(index, 1); page.candidateCount = page.candidates.length
    selectedId.value = ''; swapSourceId.value = swapSourceId.value === item.id ? '' : swapSourceId.value
    operations.commitHistory(); operations.showNotice(`已删除焊口 ${item.number || '?'}，可使用 Ctrl+Z 撤销。`)
    operations.logAudit('weld.deleted', { id: item.id, number: item.number, origin: item.origin || 'recognized' })
  }

  return {
    ensureManualWorkspace, toggleManualAddMode, mergeStagedManualCandidates, onCanvasClick, addManualAtClientPoint,
    candidateMatchesModificationType, copySelectedCandidate, pasteCopiedAtClientPoint, formattedWeldNumber, numberResult,
    pageMatchSummary,
    swapSelectedWeldNumbers, selectCandidate, reflowCurrentPageLabelPositions, beginInlineEdit, commitInlineEdit,
    cancelInlineEdit, onApplicationPointerDown, startLabelDrag, moveLabel, endLabelDrag, setLeaderLineElement,
    startAnchorDrag, moveAnchor, endAnchorDrag, startGroupDrag, moveGroup, endGroupDrag,
    toggleCandidate, deleteSelectedWeld,
  }
}
