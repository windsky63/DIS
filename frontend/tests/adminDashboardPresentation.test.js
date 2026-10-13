import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

import { nextEffectRankingMode } from '../src/adminEffectRanking.js'

test('effect ranking switches between project and drawing modes with one action', () => {
  assert.equal(nextEffectRankingMode('project'), 'drawing')
  assert.equal(nextEffectRankingMode('drawing'), 'project')
})

test('admin dashboard uses the shared header treatment and concise ranking presentation', async () => {
  const source = await readFile(new URL('../src/components/AdminApp.vue', import.meta.url), 'utf8')

  assert.match(source, /class="admin-settings-button header-icon-button"/)
  assert.match(source, /<small>图纸标识识别系统<\/small>/)
  assert.doesNotMatch(source, /<v-btn-toggle/)
  assert.doesNotMatch(source, /\{\{ effectRankingRows\.length \}\}/)
  assert.match(source, /:aria-label="effectRankingMode === 'project' \? '切换至图纸效果排行' : '切换至项目效果排行'"/)
  assert.match(source, /<th>自动标识占比<\/th>/)
  assert.match(source, /formatRate\(item\.automaticShare\)/)
  assert.doesNotMatch(source, /<thead v-else><tr>[^\n]*<th>状态<\/th>/)
})
