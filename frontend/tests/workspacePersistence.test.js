import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'

import { useWorkspacePersistence } from '../src/composables/useWorkspacePersistence.js'

function fixture() {
  const calls = []
  const persistence = useWorkspacePersistence({
    state: {
      result: ref({ status: 'complete' }), pages: ref([{ page: 1, candidates: [] }]), activeProjectIndex: ref(0),
      activeFileFingerprint: ref('drawing'), applyingHistory: ref(false), hydratingWorkspace: ref(false),
      archivingWorkspace: ref(false), error: ref(''),
    },
    operations: {
      markDirty() {}, logAudit() {}, buildDraftPayload: () => ({ result: true }),
      saveDraft: async () => { calls.push('workspace') },
      saveDraftPage: async () => { calls.push('page') },
    },
  })
  return { calls, persistence }
}

test('page-only edits persist only the active page record', async () => {
  const { calls, persistence } = fixture()
  persistence.scheduleDraftSave()
  await persistence.persistPageDraft(1)
  assert.deepEqual(calls, ['page'])
  assert.equal(persistence.draftDirty.value, false)
})

test('workspace metadata edits fall back to a complete draft at the page boundary', async () => {
  const { calls, persistence } = fixture()
  persistence.scheduleDraftSave()
  persistence.scheduleWorkspaceDraftSave()
  await persistence.persistPageDraft(1)
  assert.deepEqual(calls, ['workspace'])
  assert.equal(persistence.draftDirty.value, false)
})
