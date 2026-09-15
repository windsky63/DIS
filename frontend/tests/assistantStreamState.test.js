import assert from 'node:assert/strict'
import test from 'node:test'

import { effect, nextTick, ref, stop } from 'vue'

import { createStreamingAssistantMessage } from '../src/assistantStreamState.js'


test('each assistant delta invalidates the rendered message before the stream finishes', async () => {
  const messages = ref([])
  const streamedMessage = createStreamingAssistantMessage()
  messages.value.push(streamedMessage)
  let renderedContent = ''
  let renderCount = 0
  const renderEffect = effect(() => {
    renderCount += 1
    renderedContent = messages.value.map(message => message.content).join('')
  })

  streamedMessage.content += '第一段'
  await nextTick()
  const countAfterFirstDelta = renderCount
  assert.equal(renderedContent, '第一段')

  streamedMessage.content += '回答'
  await nextTick()
  assert.equal(renderedContent, '第一段回答')
  assert.ok(renderCount > countAfterFirstDelta)
  stop(renderEffect)
})
