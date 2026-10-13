// Frames remain independent of circular label contents and font size.
export function markerGeometry(item, style = {}) {
  const size = Math.max(18, Math.min(64, Number(style.frameSize) || 28))
  const configuredFont = Math.max(7, Math.min(24, Number(style.fontSize) || 15))
  const split = style.shape === 'split-circle'
  const textLength = Math.max(String(item.number || '?').length, split ? String(item.secondaryText || '').length : 0)
  const width = style.shape === 'rectangle' ? Math.max(size * 1.35, 10 + textLength * configuredFont * .68) : size
  let fontSize = Math.min(configuredFont, Math.max(4, (width - 6) / Math.max(textLength * .58, 1)))
  if (split) fontSize = Math.min(fontSize, Math.max(4, (size - 6) / 2.4))
  return { width, height: size, fontSize }
}
