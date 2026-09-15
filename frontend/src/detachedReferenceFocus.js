export function createDetachedReferenceFocus(item, snapshot) {
  if (!Array.isArray(item?.point) || item.point.length < 2) return null
  return {
    ...item,
    file: snapshot?.hintDocumentFile || '',
    page: Number(snapshot?.page) || 1,
  }
}

export function resolveDetachedReferenceViewport({ focus, pageInfo, layout, viewport, zoom, minimumZoom = 2.2 } = {}) {
  const pointX = Number(focus?.point?.[0])
  const pointY = Number(focus?.point?.[1])
  const pageWidth = Number(pageInfo?.width)
  const pageHeight = Number(pageInfo?.height)
  const layoutWidth = Number(layout?.width)
  const layoutHeight = Number(layout?.height)
  const viewportWidth = Number(viewport?.width)
  const viewportHeight = Number(viewport?.height)
  if (![pointX, pointY, pageWidth, pageHeight, layoutWidth, layoutHeight, viewportWidth, viewportHeight].every(Number.isFinite)
      || pageWidth <= 0 || pageHeight <= 0 || layoutWidth <= 0 || layoutHeight <= 0) return null
  const nextZoom = Math.max(minimumZoom, Number(zoom) || 1)
  const worldX = pointX / pageWidth * layoutWidth
  const worldY = pointY / pageHeight * layoutHeight
  return {
    zoom: nextZoom,
    pan: {
      x: viewportWidth / 2 - worldX * nextZoom,
      y: viewportHeight / 2 - worldY * nextZoom,
    },
  }
}
