import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'

import { tutorialCatalog, tutorialStepsById } from '../src/tutorialCatalog.js'

test('tutorial catalog provides multiple staged task-focused tutorials', () => {
  assert.equal(tutorialCatalog.length, 4)
  assert.deepEqual(tutorialCatalog.map(item => item.id), ['overview', 'analysis', 'review', 'collaboration'])
  for (const tutorial of tutorialCatalog) {
    assert.equal(tutorial.steps, tutorialStepsById[tutorial.id].length)
    assert.ok(new Set(tutorialStepsById[tutorial.id].map(step => step.stage)).size >= 2)
    assert.ok(tutorialStepsById[tutorial.id].every(step => step.description.length <= 120))
  }
})

test('system introduction covers every header action without repeating workflow tutorials', async () => {
  const overviewTargets = new Set(tutorialStepsById.overview.map(step => step.target))
  for (const target of [
    '[data-tour="analysis-queue-button"]', '[data-tour="mineru-status"]',
    '[data-tour="project-switch-button"]', '[data-tour="assistant-button"]',
    '[data-tour="tutorial-button"]', '[data-tour="shortcut-button"]',
    '[data-tour="settings-button"]', '[data-tour="user-menu-button"]',
  ]) assert.equal(overviewTargets.has(target), true, `missing header tutorial target ${target}`)
  assert.equal(tutorialCatalog[0].title, '系统介绍')
  assert.equal(tutorialCatalog[0].requiresSample, false)
  assert.equal(tutorialStepsById.overview.some(step => ['认识工作区', '画布校对', '右侧校对'].includes(step.stage)), false)

  const review = tutorialStepsById.review
  assert.equal(review.at(-1).title, '查看选中对象并完成校对')
  assert.equal(review.some(step => step.title === '保存并导出成果'), false)
  assert.match(review.at(-1).description, /特殊标识/)
  assert.equal(review.at(-1).target, '[data-tour="selected-object"]')
  assert.equal(review.find(step => step.title === '手动执行位置优化').target, '[data-tour="position-optimization"]')

  const header = await readFile(new URL('../src/components/AppHeader.vue', import.meta.url), 'utf8')
  for (const name of ['mineru-status', 'project-switch-button', 'assistant-button', 'user-menu-button']) {
    assert.match(header, new RegExp(`data-tour="${name}"`))
  }
})

test('tutorial button opens the catalog before a concrete tour', async () => {
  const source = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.match(source, /tutorialCatalogOpen\.value = true/)
  assert.match(source, /async function startTutorial\(tutorial\)/)
  assert.match(source, /<TutorialCatalogDialog/)
  assert.match(source, /:steps="activeTutorialSteps"/)
})
