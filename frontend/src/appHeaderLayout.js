export function appHeaderHeightForViewport(viewportHeight) {
  return Math.round((Number(viewportHeight) || 0) * 0.06)
}
