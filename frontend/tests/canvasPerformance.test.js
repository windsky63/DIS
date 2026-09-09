import assert from 'node:assert/strict'
import test from 'node:test'
import { selectCanvasPerformanceProfile, trimCanvasCache } from '../src/canvasPerformance.js'

test('canvas profile scales quality with available device resources', () => {
  assert.equal(selectCanvasPerformanceProfile({ deviceMemory: 4, hardwareConcurrency: 4 }).key, 'low')
  assert.equal(selectCanvasPerformanceProfile({ deviceMemory: 8, hardwareConcurrency: 12 }).key, 'high')
  assert.equal(selectCanvasPerformanceProfile({ hardwareConcurrency: 8 }).key, 'balanced')
  assert.equal(selectCanvasPerformanceProfile({ deviceMemory: 8, hardwareConcurrency: 8, maxTouchPoints: 5, viewportWidth: 600 }).key, 'low')
})

test('canvas cache evicts oldest entries to stay within its byte budget', () => {
  const cache = new Map([
    ['oldest', { width: 100, height: 100 }],
    ['middle', { width: 100, height: 100 }],
    ['latest', { width: 100, height: 100 }],
  ])
  const bytes = trimCanvasCache(cache, 80_000)
  assert.deepEqual([...cache.keys()], ['middle', 'latest'])
  assert.equal(bytes, 80_000)
})
