import assert from 'node:assert/strict'
import test from 'node:test'

import { resizeComposerTextarea, shouldSubmitComposer } from '../src/assistantComposer.js'


test('composer sends on Enter but preserves Shift+Enter and IME composition', () => {
  assert.equal(shouldSubmitComposer({ key: 'Enter', shiftKey: false, isComposing: false }), true)
  assert.equal(shouldSubmitComposer({ key: 'Enter', shiftKey: true, isComposing: false }), false)
  assert.equal(shouldSubmitComposer({ key: 'Enter', shiftKey: false, isComposing: true }), false)
  assert.equal(shouldSubmitComposer({ key: 'a', shiftKey: false, isComposing: false }), false)
})

test('composer grows with multiline text and scrolls only after its height cap', () => {
  const textarea = { scrollHeight: 116, style: {} }
  assert.equal(resizeComposerTextarea(textarea), 116)
  assert.equal(textarea.style.height, '116px')
  assert.equal(textarea.style.overflowY, 'hidden')

  textarea.scrollHeight = 240
  assert.equal(resizeComposerTextarea(textarea), 168)
  assert.equal(textarea.style.height, '168px')
  assert.equal(textarea.style.overflowY, 'auto')

  textarea.scrollHeight = 20
  assert.equal(resizeComposerTextarea(textarea), 52)
  assert.equal(textarea.style.height, '52px')
})
