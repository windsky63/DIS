import assert from 'node:assert/strict'
import test from 'node:test'
import { createJsonRequester } from '../src/httpRequest.js'

function fixture(fetchRequest) {
  let expire, duration, cleared = 0
  const request = createJsonRequester({ fetchRequest,
    setTimer: (callback, ms) => { expire = callback; duration = ms; return 1 },
    clearTimer: () => { cleared += 1 },
  })
  return { request, expire: () => expire(), duration: () => duration, cleared: () => cleared }
}
test('read deadlines abort stalled requests and never issue automatic retries', async () => {
  let signal, calls = 0
  const f = fixture(async (_url, options) => { calls++; signal = options.signal; return new Promise(() => {}) })
  const pending = f.request('/api/jobs/a')
  assert.equal(f.duration(), 30_000)
  f.expire()
  await assert.rejects(pending, error => error.code === 'REQUEST_TIMEOUT' && !error.outcomeUnknown)
  assert.equal(signal.aborted, true)
  assert.equal(calls, 1)
  assert.equal(f.cleared(), 1)
})
test('write timeout reports an unknown outcome instead of claiming the write failed', async () => {
  const f = fixture(() => new Promise(() => {}))
  const pending = f.request('/api/jobs/a/pages/1', { method: 'PUT' })
  assert.equal(f.duration(), 45_000)
  f.expire()
  await assert.rejects(pending, error => error.outcomeUnknown === true)
})
test('the deadline also covers a response body that stalls after HTTP headers', async () => {
  const f = fixture(async () => ({ ok: true, json: () => new Promise(() => {}) }))
  const pending = f.request('/api/jobs/a')
  await Promise.resolve(); f.expire()
  await assert.rejects(pending, error => error.code === 'REQUEST_TIMEOUT')
})
test('HTTP errors retain their status and successful requests release deadline resources', async () => {
  const f = fixture(async () => ({ ok: false, status: 409, json: async () => ({ error: 'revision conflict' }) }))
  await assert.rejects(f.request('/api/jobs/a'), error => error.status === 409 && !error.outcomeUnknown)
  assert.equal(f.cleared(), 1)
  const success = fixture(async () => ({ ok: true, json: async () => ({ ready: true }) }))
  assert.deepEqual(await success.request('/api/health'), { ready: true })
  assert.equal(success.cleared(), 1)
})
test('transport failure and broken successful write receipts are uncertain writes', async () => {
  for (const fetchRequest of [async () => { throw new TypeError('connection reset') },
    async () => ({ ok: true, json: async () => { throw new SyntaxError('partial JSON') } })]) {
    const f = fixture(fetchRequest)
    await assert.rejects(f.request('/api/save', { method: 'PUT' }), error => error.outcomeUnknown === true)
  }
})
test('caller cancellation is distinguished from a request timeout', async () => {
  const f = fixture(() => new Promise(() => {})), controller = new AbortController()
  const pending = f.request('/api/jobs/a', { signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, error => error.name === 'AbortError')
  assert.equal(f.cleared(), 1)
})
