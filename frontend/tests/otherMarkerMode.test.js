import assert from 'node:assert/strict'
import test from 'node:test'

import { matchesModificationType } from '../src/markerClipboard.js'

test('other modification mode only exposes special markers', () => {
  assert.equal(matchesModificationType({ specialMarker: true }, 'other'), true)
  assert.equal(matchesModificationType({ componentKind: 'special-marker' }, 'other'), true)
  assert.equal(matchesModificationType({ componentType: 'valve' }, 'other'), false)
  assert.equal(matchesModificationType({ componentKind: 'manual-weld' }, 'other'), false)
})
