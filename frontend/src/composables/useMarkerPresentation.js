import { isDesignComponent, isSpecialMarker } from '../numbering.js'
import { reflowCurrentPageLabelPositions as calculateCurrentPagePosition } from '../markerLayout.js'

export function midpoint(first, second) {
  return { x: (first.x + second.x) / 2, y: (first.y + second.y) / 2 }
}

export function translateMarkerPair(item, deltaXNorm, deltaYNorm) {
  const labelXNorm = item.labelXNorm ?? item.xNorm
  const labelYNorm = item.labelYNorm ?? item.yNorm
  const limitedX = Math.max(-Math.min(item.xNorm, labelXNorm), Math.min(1 - Math.max(item.xNorm, labelXNorm), deltaXNorm))
  const limitedY = Math.max(-Math.min(item.yNorm, labelYNorm), Math.min(1 - Math.max(item.yNorm, labelYNorm), deltaYNorm))
  const clean = value => Math.round(value * 1e12) / 1e12
  return {
    xNorm: clean(item.xNorm + limitedX),
    yNorm: clean(item.yNorm + limitedY),
    labelXNorm: clean(labelXNorm + limitedX),
    labelYNorm: clean(labelYNorm + limitedY),
  }
}

export function useMarkerPresentation({
  markerStyle,
  componentMarkerStyles,
  selectedId,
  editingId,
  swapSourceId,
  labelDragState,
  canvasLayout,
  pages,
  pageData,
}) {
  function styleFor(item) {
    if (isSpecialMarker(item)) return { ...(item?.defaultMarkerStyle || {}), ...(item?.markerStyle || {}) }
    const liveStyle = isDesignComponent(item)
      ? componentMarkerStyles.value[item.componentType] || componentMarkerStyles.value.support
      : markerStyle.value
    return { ...(item?.defaultMarkerStyle || {}), ...(item?.markerStyle || {}), ...liveStyle }
  }

  function markerClass(item) {
    const style = styleFor(item)
    return {
      selected: selectedId.value === item.id,
      editing: editingId?.value === item.id,
      excluded: item.included === false,
      matched: item.referenceMatched,
      swapSource: swapSourceId.value === item.id,
      dragging: labelDragState.value?.id === item.id,
      [`shape-${style.shape}`]: true,
    }
  }

  function unitScale(item) {
    const target = canvasLayout.value.target
    const page = pages.value.find(value => value.page === item.page) || pageData.value
    return target?.width && page?.width ? target.width / page.width : 1
  }

  function dimensions(item) {
    const style = styleFor(item)
    const scale = unitScale(item)
    const size = (Number(style.frameSize) || 28) * scale
    const textLength = String(item.number || '?').length
    const configuredFontSize = (Number(style.fontSize) || 18) * scale
    const width = style.shape === 'rectangle'
      ? Math.max(size * 1.35, 10 * scale + textLength * configuredFontSize * .68)
      : size
    const fontSize = Math.min(configuredFontSize, Math.max(4 * scale, (width - 6 * scale) / Math.max(textLength * .58, 1)))
    return { width, height: size, fontSize, scale }
  }

  function reflowCurrentPage(page) {
    return calculateCurrentPagePosition(page, styleFor)
  }

  function hexToRgba(value, alpha) {
    const match = /^#([0-9a-f]{6})$/i.exec(String(value || ''))
    if (!match) return `rgba(212,20,60,${alpha})`
    const number = Number.parseInt(match[1], 16)
    return `rgba(${number >> 16},${number >> 8 & 255},${number & 255},${alpha})`
  }

  function labelPosition(item) {
    const style = styleFor(item)
    const target = canvasLayout.value.target
    const markerDimensions = dimensions(item)
    const diamondSide = markerDimensions.width / Math.SQRT2
    return {
      left: `${target.x + (item.labelXNorm ?? item.xNorm) * target.width}px`,
      top: `${target.y + (item.labelYNorm ?? item.yNorm) * target.height}px`,
      width: `${style.shape === 'diamond' ? diamondSide : markerDimensions.width}px`,
      height: `${style.shape === 'diamond' ? diamondSide : markerDimensions.height}px`,
      borderColor: hexToRgba(style.color, .86),
      color: hexToRgba(style.color, .92),
      fontSize: `${markerDimensions.fontSize}px`,
      borderWidth: `${Math.max(.5, Number(style.lineWidth) || 1.35) * markerDimensions.scale}px`,
      backgroundColor: `rgba(255,255,255,${style.fillOpacity})`,
    }
  }

  function anchorPoint(item) {
    const target = canvasLayout.value.target
    return { x: target.x + item.xNorm * target.width, y: target.y + item.yNorm * target.height }
  }

  function leaderEnd(item, preview = null) {
    const style = styleFor(item)
    const target = canvasLayout.value.target
    const start = anchorPoint(item)
    const center = {
      x: target.x + (preview?.xNorm ?? item.labelXNorm ?? item.xNorm) * target.width,
      y: target.y + (preview?.yNorm ?? item.labelYNorm ?? item.yNorm) * target.height,
    }
    const dx = center.x - start.x
    const dy = center.y - start.y
    const distance = Math.hypot(dx, dy)
    if (distance < .01) return center
    const ux = dx / distance
    const uy = dy / distance
    const markerDimensions = dimensions(item)
    let boundary = markerDimensions.height / 2
    if (style.shape === 'diamond') boundary = (markerDimensions.height / 2) / Math.max(Math.abs(ux) + Math.abs(uy), .001)
    if (style.shape === 'rectangle') {
      boundary = Math.min(
        Math.abs(ux) > .001 ? markerDimensions.width / 2 / Math.abs(ux) : Infinity,
        Math.abs(uy) > .001 ? markerDimensions.height / 2 / Math.abs(uy) : Infinity,
      )
    }
    return { x: center.x - ux * boundary, y: center.y - uy * boundary }
  }

  function groupHandlePosition(item) {
    return midpoint(anchorPoint(item), leaderEnd(item))
  }

  function leaderStyle(item) {
    const style = styleFor(item)
    const scale = dimensions(item).scale
    return {
      stroke: style.color,
      strokeWidth: Math.max(.5, (Number(style.lineWidth) || 1.35) * .78) * scale,
      strokeOpacity: .68,
    }
  }

  return {
    candidateMarkerStyle: styleFor,
    markerClass,
    markerDimensions: dimensions,
    reflowCurrentPageLabelPositions: reflowCurrentPage,
    labelPosition,
    anchorPoint,
    leaderEnd,
    groupHandlePosition,
    leaderStyle,
  }
}
