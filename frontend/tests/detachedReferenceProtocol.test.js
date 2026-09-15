import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createReferenceChannelId,
  isReferenceMessage,
  referenceWindowUrl,
} from '../src/detachedReferenceProtocol.js'
import { useDetachedReferenceWindow } from '../src/composables/useDetachedReferenceWindow.js'


test('detached reference URL keeps its private channel in the fragment', () => {
  const channelId = createReferenceChannelId({ randomUUID: () => 'channel-123' })
  assert.equal(channelId, 'channel-123')
  assert.equal(referenceWindowUrl(channelId), '/?view=reference#channel=channel-123')
})

test('detached reference messages reject another channel or protocol version', () => {
  const valid = { protocol: 'weld-marker.reference-window.v1', channelId: 'mine', type: 'ready' }
  assert.equal(isReferenceMessage(valid, 'mine'), true)
  assert.equal(isReferenceMessage({ ...valid, channelId: 'other' }, 'mine'), false)
  assert.equal(isReferenceMessage({ ...valid, protocol: 'old' }, 'mine'), false)
})

test('detached controller resends the latest state when the viewer becomes ready', () => {
  const sent = []
  let receive
  const channel = {
    postMessage: message => sent.push(message),
    addEventListener: (_type, listener) => { receive = listener },
    removeEventListener: () => undefined,
    close: () => undefined,
  }
  const popup = { closed: false, focus: () => undefined, close: () => undefined }
  const controller = useDetachedReferenceWindow({
    createChannel: () => channel,
    openWindow: () => popup,
    setIntervalFn: () => 7,
    clearIntervalFn: () => undefined,
    randomUUID: () => 'mine',
  })

  assert.equal(controller.open(), true)
  controller.publish({ page: 6, fileName: 'reference.pdf' })
  receive({ data: { protocol: 'weld-marker.reference-window.v1', channelId: 'mine', type: 'ready' } })

  assert.equal(sent.at(-1).type, 'reference-state')
  assert.equal(sent.at(-1).payload.page, 6)
  controller.dispose()
})

test('detached controller reports popup blocking and viewer closure', () => {
  let closed = 0
  let poll
  const blocked = useDetachedReferenceWindow({
    createChannel: () => ({ addEventListener: () => undefined, close: () => undefined }),
    openWindow: () => null,
    randomUUID: () => 'blocked',
  })
  assert.equal(blocked.open(), false)
  assert.equal(blocked.detached.value, false)

  const popup = { closed: false, focus: () => undefined, close: () => undefined }
  const controller = useDetachedReferenceWindow({
    createChannel: () => ({ postMessage: () => undefined, addEventListener: () => undefined, removeEventListener: () => undefined, close: () => undefined }),
    openWindow: () => popup,
    setIntervalFn: callback => { poll = callback; return 9 },
    clearIntervalFn: () => undefined,
    randomUUID: () => 'mine',
    onViewerClosed: () => { closed += 1 },
  })
  controller.open()
  popup.closed = true
  poll()
  assert.equal(controller.detached.value, false)
  assert.equal(closed, 1)
  controller.dispose()
})
