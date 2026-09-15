import assert from 'node:assert/strict'
import test from 'node:test'

import { calculateAnalysisProgress } from '../src/analysisProgress.js'


test('analysis progress uses upload progress before creation and canonical server progress afterwards', () => {
  assert.equal(calculateAnalysisProgress({ phase: 'uploading', transferPercent: 50, fileCount: 1, fileIndex: 0 }), 7.5)
  assert.equal(calculateAnalysisProgress({ phase: 'server-validation', transferPercent: 100 }), 15)
  assert.equal(calculateAnalysisProgress({
    phase: 'analysis', progressPercent: 55,
    progressCompletedUnits: 1, progressTotalUnits: 99,
  }), 55)
  assert.equal(calculateAnalysisProgress({
    phase: 'analysis', progressPercent: 100,
  }), 100)
})
