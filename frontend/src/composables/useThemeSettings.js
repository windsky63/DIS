import { onBeforeUnmount, ref, watch } from 'vue'
import { useTheme } from 'vuetify'
import { loadThemePreference, normalizeThemePreference, resolveThemeName, THEME_OPTIONS, THEME_STORAGE_KEY } from '../themePreferences.js'

export function useThemeSettings() {
  const theme = useTheme()
  const preference = ref(loadThemePreference())
  const apply = () => theme.change(resolveThemeName(preference.value))
  watch(preference, value => {
    preference.value = normalizeThemePreference(value)
    try { localStorage.setItem(THEME_STORAGE_KEY, preference.value) } catch { /* Storage may be unavailable. */ }
    void apply()
  })
  const synchronize = event => {
    if (event.key === THEME_STORAGE_KEY || event.key === null) preference.value = loadThemePreference()
  }
  globalThis.addEventListener?.('storage', synchronize)
  void apply()
  onBeforeUnmount(() => {
    globalThis.removeEventListener?.('storage', synchronize)
  })
  return { preference, options: THEME_OPTIONS }
}
