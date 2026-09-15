import assert from 'node:assert/strict'
import test from 'node:test'

import { resolvePageJump } from '../src/composables/usePageNavigation.js'

test('page jump accepts only an available design page', () => {
  assert.equal(resolvePageJump('15', [1, 8, 15, 20]), 15)
  assert.equal(resolvePageJump('9', [1, 8, 15, 20]), null)
  assert.equal(resolvePageJump('', [1, 8, 15, 20]), null)
  assert.equal(resolvePageJump('15x', [1, 8, 15, 20]), null)
})
