import { ref } from 'vue'

import {
  createReferenceChannelId,
  isReferenceMessage,
  referenceChannelName,
  referenceMessage,
  referenceWindowUrl,
} from '../detachedReferenceProtocol.js'


export function useDetachedReferenceWindow({
  createChannel = name => new BroadcastChannel(name),
  openWindow = (url, name, features) => window.open(url, name, features),
  setIntervalFn = (callback, delay) => window.setInterval(callback, delay),
  clearIntervalFn = timer => window.clearInterval(timer),
  randomUUID = () => createReferenceChannelId(),
  onViewerClosed = () => undefined,
  onError = () => undefined,
} = {}) {
  const detached = ref(false)
  const connected = ref(false)
  let channelId = ''
  let channel = null
  let popup = null
  let closeTimer
  let latestPayload = null

  function post(type, payload) {
    if (!channel || !channelId) return
    try { channel.postMessage(referenceMessage(channelId, type, payload)) }
    catch (cause) { onError(cause) }
  }

  function handleMessage(event) {
    if (!isReferenceMessage(event?.data, channelId)) return
    if (event.data.type === 'ready') {
      connected.value = true
      if (latestPayload) post('reference-state', latestPayload)
    }
    if (event.data.type === 'closed') handleViewerClosed()
  }

  function releaseResources() {
    if (closeTimer !== undefined) clearIntervalFn(closeTimer)
    closeTimer = undefined
    channel?.removeEventListener?.('message', handleMessage)
    channel?.close?.()
    channel = null
    popup = null
    channelId = ''
    connected.value = false
  }

  function handleViewerClosed() {
    if (!detached.value) return
    detached.value = false
    releaseResources()
    onViewerClosed()
  }

  function open() {
    if (popup && !popup.closed) {
      popup.focus?.()
      return true
    }
    channelId = String(randomUUID())
    try {
      channel = createChannel(referenceChannelName(channelId))
      channel.addEventListener?.('message', handleMessage)
      popup = openWindow(
        referenceWindowUrl(channelId),
        `weld-marker-reference-${channelId}`,
        'popup=yes,width=1200,height=900,resizable=yes,scrollbars=no',
      )
    } catch (cause) {
      onError(cause)
      releaseResources()
      return false
    }
    if (!popup) {
      releaseResources()
      return false
    }
    detached.value = true
    closeTimer = setIntervalFn(() => {
      if (popup?.closed) handleViewerClosed()
    }, 750)
    popup.focus?.()
    return true
  }

  function publish(payload) {
    latestPayload = payload
    if (detached.value) post('reference-state', payload)
  }

  function close({ restoreEmbedded = true } = {}) {
    if (!detached.value && !popup) return
    post('parent-closing')
    try { popup?.close?.() } catch { /* browser may deny scripted close */ }
    detached.value = false
    releaseResources()
    if (restoreEmbedded) onViewerClosed()
  }

  function dispose() {
    close({ restoreEmbedded: false })
    latestPayload = null
  }

  return { detached, connected, open, publish, close, dispose }
}
