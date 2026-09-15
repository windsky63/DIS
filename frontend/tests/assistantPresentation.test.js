import assert from 'node:assert/strict'
import test from 'node:test'

import { assistantConfigurationAlert, visibleAssistantError } from '../src/assistantPresentation.js'


test('configuration failure is represented by only the persistent configuration warning', () => {
  assert.equal(visibleAssistantError(false, 'AI 助手尚未配置，请联系管理员设置 API 地址、密钥和模型'), '')
})

test('non-configuration request failures remain visible', () => {
  assert.equal(visibleAssistantError(true, '无法连接 AI 服务'), '无法连接 AI 服务')
})

test('missing AI configuration is presented as an error alert', () => {
  assert.deepEqual(assistantConfigurationAlert(false), {
    level: 'error',
    message: 'AI API 尚未配置，请由管理员在服务端设置 API 地址、密钥和模型后重启服务。',
  })
  assert.equal(assistantConfigurationAlert(true), null)
})

test('quick starts randomly select four distinct questions from a larger pool', async () => {
  const presentation = await import('../src/assistantPresentation.js')
  assert.equal(typeof presentation.selectQuickStarts, 'function')
  const pool = ['A', 'B', 'C', 'D', 'E', 'F']
  const rolls = [.99, 0, .5, .25, .75]
  let index = 0
  const selected = presentation.selectQuickStarts(pool, 4, () => rolls[index++])
  assert.equal(selected.length, 4)
  assert.equal(new Set(selected).size, 4)
  assert.ok(selected.every(item => pool.includes(item)))
  assert.notDeepEqual(selected, pool.slice(0, 4))
})

test('refreshing one quick start replaces only that question without creating duplicates', async () => {
  const presentation = await import('../src/assistantPresentation.js')
  assert.equal(typeof presentation.replaceQuickStart, 'function')
  const visible = ['A', 'B', 'C', 'D']
  assert.deepEqual(
    presentation.replaceQuickStart(visible, ['A', 'B', 'C', 'D', 'E', 'F'], 1, () => .99),
    ['A', 'F', 'C', 'D'],
  )
})
