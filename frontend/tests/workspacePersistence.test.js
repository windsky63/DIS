import assert from 'node:assert/strict'
import test from 'node:test'

import { ref } from 'vue'

import { useWorkspacePersistence } from '../src/composables/useWorkspacePersistence.js'


function harness() {
  let markedPages = 0
  const persistence = useWorkspacePersistence({
    state: {
      activeFileFingerprint: ref('drawing-a'),
      result: ref({ status: 'complete' }),
      applyingHistory: ref(false),
      hydratingWorkspace: ref(false),
      archivingWorkspace: ref(false),
      error: ref(''),
    },
    operations: {
      markDirty: () => { markedPages += 1 },
      buildDraftPayload: () => ({}),
      logAudit: () => {},
    },
  })
  return { persistence, markedPages: () => markedPages }
}

test('passive workspace changes remain draft-only and do not dirty the review page', () => {
  const { persistence, markedPages } = harness()

  persistence.scheduleWorkspaceDraftSave()

  assert.equal(persistence.draftDirty.value, true)
  assert.equal(markedPages(), 0)
  assert.equal(persistence.backendSaveState.value, 'idle')
})

test('review edits still mark both the browser draft and current review page dirty', () => {
  const { persistence, markedPages } = harness()

  persistence.scheduleDraftSave()

  assert.equal(persistence.draftDirty.value, true)
  assert.equal(markedPages(), 1)
  assert.equal(persistence.backendSaveState.value, 'pending')
})
