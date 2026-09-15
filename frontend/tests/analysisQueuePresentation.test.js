import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'


test('queue summary presents concurrency as a chip alongside running and waiting counts', async () => {
  const presentation = await import('../src/analysisQueuePresentation.js').catch(() => ({}))
  assert.equal(typeof presentation.queueSummaryChips, 'function')
  assert.deepEqual(presentation.queueSummaryChips({ runningCount: 1, queuedCount: 2, maxConcurrent: 3 }), [
    { key: 'running', label: '解析中 1', color: 'accent' },
    { key: 'queued', label: '等待 2', color: 'warning' },
    { key: 'concurrency', label: '并发上限 3', color: 'secondary' },
  ])
})

test('archived queue action explains that it moves the job back to the queue', async () => {
  const presentation = await import('../src/analysisQueuePresentation.js').catch(() => ({}))
  assert.equal(typeof presentation.archiveActionLabel, 'function')
  assert.equal(presentation.archiveActionLabel({ isArchived: true }), '移回解析队列')
  assert.equal(presentation.archiveActionLabel({ isArchived: false }), '归档')
})

test('queue creator uses the same metadata text style as the creation time', async () => {
  const source = await readFile(new URL('../src/components/AnalysisQueueDialog.vue', import.meta.url), 'utf8')
  const styles = await readFile(new URL('../src/style.css', import.meta.url), 'utf8')
  assert.match(source, /class="analysis-queue-start-time"[^>]*>创建人：/)
  assert.match(styles, /\.analysis-queue-start-time \{[^}]*font-family: "Microsoft YaHei"[^}]*font-size: 10px[^}]*font-weight: 400[^}]*line-height: 1\.4/)
})

test('queue task rows expand from non-action space without a dedicated expand icon', async () => {
  const source = await readFile(new URL('../src/components/AnalysisQueueDialog.vue', import.meta.url), 'utf8')
  assert.match(source, /const expandedJobId = ref\(''\)/)
  assert.match(source, /expandedJobId\.value === job\.jobId \? '' : job\.jobId/)
  assert.match(source, /<v-list-item[^>]*@click="toggleExpanded\(job\)"/s)
  assert.doesNotMatch(source, /analysis-queue-expand-button/)
})

test('queue details treat drawing count as pages and show only recent multi-user changes', async () => {
  const source = await readFile(new URL('../src/components/AnalysisQueueDialog.vue', import.meta.url), 'utf8')
  assert.match(source, /<span>图纸数<\/span><strong>\{\{ job\.pageCount \|\| job\.totalPages \|\| 0 \}\} 页<\/strong>/)
  assert.doesNotMatch(source, /designDrawingCount|referenceDrawingCount|解析页数/)
  assert.match(source, /最近修改记录/)
  assert.match(source, /job\.recentReviewEvents/)
  assert.match(source, /event\.username/)
  assert.match(source, /P\{\{ event\.page \}\}/)
})

test('tutorial seen value is written and checked consistently', async () => {
  const source = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.match(source, /localStorage\.setItem\(tutorialStorageKey, 'true'\)/)
  assert.match(source, /if \(!localStorage\.getItem\(tutorialStorageKey\)\)/)
  assert.match(source, /function updateTutorialCatalogOpen\(open\)[\s\S]*if \(!open\) finishTutorial\(\)/)
  assert.match(source, /@update:model-value="updateTutorialCatalogOpen"/)
})

test('clicking anywhere in the drawing clears a selected reference hint', async () => {
  const source = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  assert.match(source, /function clearReferenceHintSelection\(\)/)
  assert.match(source, /@pointerdown\.capture="clearReferenceHintSelection"/)
})
