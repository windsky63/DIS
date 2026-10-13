import assert from 'node:assert/strict'
import test from 'node:test'

import { boundedCanvasPixelFactor, CANVAS_PERFORMANCE_PROFILES, selectCanvasPerformanceProfile } from '../src/canvasPerformance.js'

test('each canvas profile exposes device and available memory guidance', () => {
  assert.deepEqual(
    Object.values(CANVAS_PERFORMANCE_PROFILES).map(profile => ({
      key: profile.key,
      device: profile.recommendedDeviceMemoryGb,
      available: profile.recommendedAvailableMemoryGb,
    })),
    [
      { key: 'low', device: 4, available: 1 },
      { key: 'balanced', device: 8, available: 2 },
      { key: 'high', device: 16, available: 4 },
    ],
  )
})

test('8 GB class devices use balanced rendering instead of the high-memory profile', () => {
  assert.equal(selectCanvasPerformanceProfile({ deviceMemory: 8, hardwareConcurrency: 12 }).key, 'balanced')
})

test('high rendering profile requires at least a 16 GB class device', () => {
  assert.equal(selectCanvasPerformanceProfile({ deviceMemory: 16, hardwareConcurrency: 8 }).key, 'high')
})

test('large reference canvases obey the selected pixel and dimension budgets', () => {
  for (const profile of Object.values(CANVAS_PERFORMANCE_PROFILES)) {
    for (const [width, height] of [[5000, 3000], [12000, 1000], [600, 800]]) {
      const factor = boundedCanvasPixelFactor(width, height, 4, profile)
      assert.ok(width * height * factor * factor <= profile.maxPixels + .001)
      assert.ok(Math.max(width, height) * factor <= profile.maxDimension + .001)
    }
  }
})

test('canvas resolution is unchanged when it already fits the selected budget', () => {
  assert.equal(boundedCanvasPixelFactor(600, 800, 2, CANVAS_PERFORMANCE_PROFILES.balanced), 2)
})
