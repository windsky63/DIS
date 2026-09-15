export const REFERENCE_WINDOW_PROTOCOL = 'weld-marker.reference-window.v1'
export const REFERENCE_CHANNEL_PREFIX = 'weld-marker.reference-window:'

export function createReferenceChannelId(cryptoLike = globalThis.crypto) {
  if (typeof cryptoLike?.randomUUID === 'function') return cryptoLike.randomUUID()
  const bytes = new Uint8Array(16)
  cryptoLike?.getRandomValues?.(bytes)
  const encoded = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  return encoded || `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function referenceChannelName(channelId) {
  return `${REFERENCE_CHANNEL_PREFIX}${channelId}`
}

export function referenceWindowUrl(channelId) {
  return `/?view=reference#channel=${encodeURIComponent(channelId)}`
}

export function referenceChannelFromLocation(locationLike = globalThis.location) {
  const hash = String(locationLike?.hash || '').replace(/^#/, '')
  return new URLSearchParams(hash).get('channel') || ''
}

export function referenceMessage(channelId, type, payload = undefined) {
  return { protocol: REFERENCE_WINDOW_PROTOCOL, channelId, type, ...(payload === undefined ? {} : { payload }) }
}

export function isReferenceMessage(value, channelId) {
  return Boolean(value && typeof value === 'object'
    && value.protocol === REFERENCE_WINDOW_PROTOCOL
    && value.channelId === channelId
    && typeof value.type === 'string')
}
