import assert from 'node:assert/strict'
import test from 'node:test'

import { canvasFitDecision } from '../src/canvasFitPolicy.js'

test('normal page renders do not fit when automatic fitting is disabled', () => {
  assert.deepEqual(canvasFitDecision({ requested: true, autoFit: false, initialFitPending: false, waitingForReference: false }), {
    fit: false,
    consumeInitialFit: false,
  })
})

test('the first task render fits once even when automatic fitting is disabled', () => {
  assert.deepEqual(canvasFitDecision({ requested: true, autoFit: false, initialFitPending: true, waitingForReference: false }), {
    fit: true,
    consumeInitialFit: true,
  })
})

test('the initial fit waits until the task reference PDF is ready', () => {
  assert.deepEqual(canvasFitDecision({ requested: true, autoFit: false, initialFitPending: true, waitingForReference: true }), {
    fit: false,
    consumeInitialFit: false,
  })
})
