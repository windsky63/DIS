import assert from 'node:assert/strict'
import test from 'node:test'

import { createLatestMessageChannel } from '../src/messageChannel.js'

test('frequent messages display once immediately and then coalesce to the latest value', () => {
  let clock = 1000
  let scheduled = null
  const displayed = []
  const channel = createLatestMessageChannel(value => displayed.push(value), {
    interval: 300,
    now: () => clock,
    schedule: callback => { scheduled = callback; return 1 },
    cancel: () => { scheduled = null }
  })

  channel.publish('first')
  clock += 20
  channel.publish('second')
  channel.publish('latest')

  assert.deepEqual(displayed, ['first'])
  scheduled()
  assert.deepEqual(displayed, ['first', 'latest'])
})

test('clearing a message cancels a pending display', () => {
  let clock = 1000
  let scheduled = null
  const displayed = []
  const channel = createLatestMessageChannel(value => displayed.push(value), {
    interval: 300,
    now: () => clock,
    schedule: callback => { scheduled = callback; return 1 },
    cancel: () => { scheduled = null }
  })

  channel.publish('first')
  clock += 10
  channel.publish('pending')
  channel.clear()

  assert.equal(scheduled, null)
  assert.deepEqual(displayed, ['first', ''])
})
