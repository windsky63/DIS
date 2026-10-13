import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { ref } from 'vue'

// Exercise the application boundary too: the collaboration layer deliberately
// returns null for superseded callers, which must never be coerced to page 0.
const app = await readFile(new URL('../src/App.vue', import.meta.url), 'utf8')
const handler = app.slice(app.indexOf('async function changePage('), app.indexOf('\nfunction pageLockOwner('))
function fixture(enteredPage) {
  const cleaned = [], notices = []
  const factory = new Function('autoReferenceWindow', 'referenceDetached', 'referencePdf', 'result', 'projectWorkspace', 'pageCollaboration', 'showNotice', 'error', `return (${handler})`)
  const navigate = factory(ref(false), ref(false), ref(null), ref({ jobId: 'job', status: 'complete' }), {
    releasePageDetails: (...args) => cleaned.push(args),
  }, {
    enterPage: async () => enteredPage, dirtyPages: ref(new Set([1])),
  }, message => notices.push(message), ref(''))
  return { navigate, cleaned, notices }
}

test('superseded navigation cannot clear the winning page markers or show a false lock fallback', async () => {
  const f = fixture(null)
  assert.equal(await f.navigate(2), false)
  assert.deepEqual(f.cleaned, [])
  assert.deepEqual(f.notices, [])
})

test('a successful navigation protects its page details and only reports a real fallback', async () => {
  const direct = fixture(2)
  assert.equal(await direct.navigate(2), 2)
  assert.deepEqual(direct.cleaned, [[2, [1]]])
  assert.deepEqual(direct.notices, [])
  const fallback = fixture(3)
  assert.equal(await fallback.navigate(2), 3)
  assert.deepEqual(fallback.cleaned, [[3, [1]]])
  assert.equal(fallback.notices.length, 1)
})
