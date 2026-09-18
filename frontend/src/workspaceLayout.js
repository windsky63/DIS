export function workspaceLayoutForWidth(width) {
  const viewportWidth = Math.max(1024, Number(width) || 1024)
  let drawerWidth
  if (viewportWidth <= 1100) drawerWidth = 216
  else if (viewportWidth <= 1366) drawerWidth = 232
  else if (viewportWidth <= 1440) drawerWidth = 244
  else drawerWidth = Math.max(250, Math.min(320, Math.round(viewportWidth * 0.15)))

  return {
    leftDrawerWidth: drawerWidth,
    reviewDrawerWidth: drawerWidth,
    hideContextChips: viewportWidth <= 1440,
    narrowDrawers: viewportWidth <= 1440,
    twoRowToolbar: viewportWidth <= 1440,
  }
}
