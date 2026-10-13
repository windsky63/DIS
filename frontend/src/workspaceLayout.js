export const DRAWER_SIZE_OPTIONS = [
  { title: '紧凑', value: 'compact' },
  { title: '标准', value: 'standard' },
  { title: '宽松', value: 'wide' },
]

export function normalizeDrawerSize(value) {
  return DRAWER_SIZE_OPTIONS.some(option => option.value === value) ? value : 'standard'
}

export function loadDrawerSize(storage, key) {
  try { return normalizeDrawerSize(storage?.getItem?.(key)) } catch { return 'standard' }
}

export function saveDrawerSize(storage, key, value) {
  try { storage?.setItem?.(key, normalizeDrawerSize(value)) } catch { /* Storage may be unavailable. */ }
}

function drawerWidthForSize(baseWidth, size) {
  const adjustment = { compact: -24, standard: 0, wide: 40 }[normalizeDrawerSize(size)]
  return Math.max(196, Math.min(360, baseWidth + adjustment))
}

export function workspaceLayoutForWidth(width, { leftSize = 'standard', rightSize = 'standard' } = {}) {
  const viewportWidth = Math.max(1024, Number(width) || 1024)
  let drawerWidth
  if (viewportWidth <= 1100) drawerWidth = 216
  else if (viewportWidth <= 1366) drawerWidth = 232
  else if (viewportWidth <= 1440) drawerWidth = 244
  else drawerWidth = Math.max(250, Math.min(320, Math.round(viewportWidth * 0.15)))

  return {
    leftDrawerWidth: drawerWidthForSize(drawerWidth, leftSize),
    reviewDrawerWidth: drawerWidthForSize(drawerWidth, rightSize),
    hideContextChips: viewportWidth <= 1440,
    narrowDrawers: viewportWidth <= 1440,
    twoRowToolbar: viewportWidth <= 1440,
  }
}
