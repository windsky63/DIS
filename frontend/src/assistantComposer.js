export function shouldSubmitComposer(event) {
  return event?.key === 'Enter'
    && !event.shiftKey
    && !event.isComposing
    && event.keyCode !== 229
}

export function resizeComposerTextarea(textarea, { minHeight = 52, maxHeight = 168 } = {}) {
  if (!textarea?.style) return minHeight
  textarea.style.height = 'auto'
  const contentHeight = Number(textarea.scrollHeight) || minHeight
  const height = Math.min(maxHeight, Math.max(minHeight, contentHeight))
  textarea.style.height = `${height}px`
  textarea.style.overflowY = contentHeight > maxHeight ? 'auto' : 'hidden'
  return height
}
