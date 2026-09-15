export const AUTO_REFERENCE_WINDOW_KEY = 'weld-marker.auto-reference-window'
export const REFERENCE_HINT_LOCATION_KEY = 'weld-marker.reference-hint-location'

export function loadAutoReferenceWindow(storage = globalThis.localStorage) {
  try { return storage?.getItem(AUTO_REFERENCE_WINDOW_KEY) === 'true' }
  catch { return false }
}

export function saveAutoReferenceWindow(storage = globalThis.localStorage, enabled) {
  try { storage?.setItem(AUTO_REFERENCE_WINDOW_KEY, String(Boolean(enabled))) }
  catch { /* local preferences are optional */ }
}

export function loadReferenceHintLocation(storage = globalThis.localStorage) {
  try { return storage?.getItem(REFERENCE_HINT_LOCATION_KEY) === 'design' ? 'design' : 'reference' }
  catch { return 'reference' }
}

export function saveReferenceHintLocation(storage = globalThis.localStorage, location) {
  try { storage?.setItem(REFERENCE_HINT_LOCATION_KEY, location === 'design' ? 'design' : 'reference') }
  catch { /* local preferences are optional */ }
}

export function shouldShowHintsInReferenceWindow(detached, location) {
  return Boolean(detached && location === 'reference')
}
