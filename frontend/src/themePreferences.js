export const THEME_STORAGE_KEY = 'weld-marker.theme'
export const THEME_OPTIONS = Object.freeze([
  { title: '浅色', value: 'light' },
  { title: '深色', value: 'dark' },
  { title: '跟随系统', value: 'system' },
])

export function normalizeThemePreference(value) {
  return THEME_OPTIONS.some(option => option.value === value) ? value : 'light'
}

export function loadThemePreference(storage = globalThis.localStorage) {
  try { return normalizeThemePreference(storage?.getItem(THEME_STORAGE_KEY)) } catch { return 'light' }
}

export function resolveThemeName(preference, systemDark = false) {
  return preference === 'dark' || (preference === 'system' && systemDark) ? 'weldDark' : 'weldLight'
}

export const APP_THEMES = {
  weldLight: {
    dark: false,
    colors: {
      primary: '#24465d', secondary: '#527681', accent: '#c45d3c',
      background: '#f2f5f7', surface: '#ffffff', 'on-surface': '#203b4d',
      header: '#fcfdfe', 'on-header': '#24465d',
      'surface-muted': '#f5f7f9', 'surface-selected': '#fdfefe',
      'on-surface-muted': '#5c7280', outline: '#d8e2e8',
      error: '#b64932', info: '#527681', success: '#297a51', warning: '#9a671e',
    },
  },
  weldDark: {
    dark: true,
    colors: {
      primary: '#adc9de', secondary: '#a1c4c9', accent: '#e69b7b',
      background: '#121c25', surface: '#1d2a35', 'on-surface': '#e1ebf2',
      header: '#202f3b', 'on-header': '#e1ebf2',
      'surface-muted': '#253541', 'surface-selected': '#344956',
      'on-surface-muted': '#adbecb', outline: '#435764',
      error: '#f49b89', info: '#a1c4c9', success: '#8ed4ac', warning: '#e5bf7d',
    },
  },
}
