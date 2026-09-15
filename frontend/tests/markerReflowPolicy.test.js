import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'


test('frontend label optimization is available only through the manual review action', async () => {
  const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
  const workspace = await readFile(new URL('../src/composables/useProjectWorkspace.js', import.meta.url), 'utf8')

  assert.doesNotMatch(app, /scheduleMarkerReflow/)
  assert.doesNotMatch(workspace, /operations\.reflowLabelPositions/)
  assert.match(app, /@click="reflowCurrentPageLabelPositions\(true\)"/)
})
