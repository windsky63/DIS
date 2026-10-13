export const OPERATION_MESSAGES_KEY = 'weld-marker.operation-messages-enabled'
export const ERROR_MESSAGES_KEY = 'weld-marker.error-messages-enabled'
export const MESSAGE_LOCATION_KEY = 'weld-marker.message-location'

function readPreference(storage, key, fallback) {
  const value = storage?.getItem(key)
  if (value === 'true') return true
  if (value === 'false') return false
  return fallback
}

export function loadMessagePreferences(storage = globalThis.localStorage) {
  return {
    operationMessagesEnabled: readPreference(storage, OPERATION_MESSAGES_KEY, true),
    errorMessagesEnabled: readPreference(storage, ERROR_MESSAGES_KEY, true),
    messageLocation: storage?.getItem(MESSAGE_LOCATION_KEY) === 'top' ? 'top' : 'bottom',
  }
}

export function saveMessagePreference(storage = globalThis.localStorage, key, enabled) {
  storage?.setItem(key, String(Boolean(enabled)))
}

export function saveMessageLocation(storage = globalThis.localStorage, location) {
  storage?.setItem(MESSAGE_LOCATION_KEY, location === 'top' ? 'top' : 'bottom')
}
