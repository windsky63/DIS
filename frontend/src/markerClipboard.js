import { isDesignComponent, isSpecialMarker } from './numbering.js'


function cloneMarker(marker) {
  if (typeof globalThis.structuredClone === 'function') return globalThis.structuredClone(marker)
  return JSON.parse(JSON.stringify(marker))
}

export function matchesModificationType(item, type) {
  if (type === 'all') return true
  return type === 'weld' ? !isDesignComponent(item) && !isSpecialMarker(item) : item?.componentType === type
}

export function createPastedMarker(source, { id, page, pageWidth, pageHeight, xNorm, yNorm }) {
  const pasted = cloneMarker(source)
  const sourceXNorm = Number.isFinite(Number(source?.xNorm)) ? Number(source.xNorm) : 0
  const sourceYNorm = Number.isFinite(Number(source?.yNorm)) ? Number(source.yNorm) : 0
  const sourceLabelXNorm = Number.isFinite(Number(source?.labelXNorm)) ? Number(source.labelXNorm) : sourceXNorm
  const sourceLabelYNorm = Number.isFinite(Number(source?.labelYNorm)) ? Number(source.labelYNorm) : sourceYNorm
  const labelOffsetX = sourceLabelXNorm - sourceXNorm
  const labelOffsetY = sourceLabelYNorm - sourceYNorm
  const labelXNorm = Math.max(0, Math.min(1, xNorm + labelOffsetX))
  const labelYNorm = Math.max(0, Math.min(1, yNorm + labelOffsetY))
  return {
    ...pasted,
    id, page,
    xNorm, yNorm, x: xNorm * pageWidth, y: yNorm * pageHeight,
    labelXNorm, labelYNorm, labelX: labelXNorm * pageWidth, labelY: labelYNorm * pageHeight,
    confidence: 1, tier: 'manual', glyphValidated: false, evidence: '复制的人工标识',
    included: true, origin: 'manual', referenceLabel: '', referenceMatched: false,
  }
}
