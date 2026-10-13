import assert from 'node:assert/strict'
import test from 'node:test'

import { loadDrawerSize, workspaceLayoutForWidth } from '../src/workspaceLayout.js'

test('workspace drawer size gears adjust each side independently', () => {
  const adjusted = workspaceLayoutForWidth(1440, { leftSize: 'compact', rightSize: 'wide' })
  const standard = workspaceLayoutForWidth(1440)

  assert.ok(adjusted.leftDrawerWidth < standard.leftDrawerWidth)
  assert.ok(adjusted.reviewDrawerWidth > standard.reviewDrawerWidth)
})

test('invalid stored drawer sizes fall back to standard', () => {
  const storage = { getItem: () => 'oversized' }
  assert.equal(loadDrawerSize(storage, 'drawer'), 'standard')
})
