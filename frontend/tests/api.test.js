import assert from 'node:assert/strict'
import test from 'node:test'

import { api } from '../src/api.js'


test('analysis queue request includes its server-side page and scope', async () => {
  const originalFetch = globalThis.fetch
  let requestedUrl
  globalThis.fetch = async url => {
    requestedUrl = String(url)
    return { ok: true, json: async () => ({ jobs: [], pagination: { page: 3 } }) }
  }
  try {
    await api.getAnalysisQueue({ scope: 'archived', page: 3, pageSize: 40 })
    assert.equal(requestedUrl, '/api/analysis-queue?scope=archived&page=3&pageSize=40')
  } finally {
    globalThis.fetch = originalFetch
  }
})


test('tutorial reference manifest uses the tutorial endpoint and remains lazy', async () => {
  const originalFetch = globalThis.fetch
  const requested = []
  globalThis.fetch = async url => {
    requested.push(String(url))
    return {
      ok: true,
      json: async () => ({ files: [{ index: 0, name: 'reference.pdf', url: '/api/tutorial/references/0' }] }),
    }
  }
  try {
    const files = await api.getJobReferenceManifest('tutorial-000207')
    assert.deepEqual(requested, ['/api/tutorial/references'])
    assert.equal(files.length, 1)
    assert.equal(files[0].lazyReference, true)
    assert.equal(files[0].weldMarkerReferenceIndex, 0)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('binary file upload reports XMLHttpRequest progress without base64 encoding', async () => {
  const OriginalXHR = globalThis.XMLHttpRequest
  const progress = []
  let request
  class FakeXHR {
    constructor() { request = this; this.upload = {}; this.status = 201; this.responseText = '{"uploadId":"abc","size":100}' }
    open(method, url) { this.method = method; this.url = url }
    setRequestHeader(name, value) { this.headers ||= {}; this.headers[name] = value }
    send(body) {
      this.body = body
      this.upload.onprogress?.({ loaded: 60, total: 100, lengthComputable: true })
      this.upload.onload?.()
      this.onload()
    }
  }
  globalThis.XMLHttpRequest = FakeXHR
  try {
    const file = { name: '图纸 a.pdf', type: 'application/pdf', size: 100 }
    const result = await api.uploadFile(file, event => progress.push(event))
    assert.equal(result.uploadId, 'abc')
    assert.equal(request.method, 'POST')
    assert.equal(request.url, '/api/uploads')
    assert.equal(request.body, file)
    assert.equal(request.headers['Content-Type'], 'application/pdf')
    assert.equal(request.headers['X-File-Name'], encodeURIComponent(file.name))
    assert.deepEqual(progress, [
      { loaded: 60, total: 100, complete: false },
      { loaded: 100, total: 100, complete: true },
    ])
  } finally {
    globalThis.XMLHttpRequest = OriginalXHR
  }
})

test('interactive analysis can request queue-front priority', async () => {
  const originalFetch = globalThis.fetch
  let requestInfo
  globalThis.fetch = async (url, options) => {
    requestInfo = { url, options }
    return { ok: true, json: async () => ({ moved: true, queuePosition: 1, previousQueuePosition: 3 }) }
  }
  try {
    const result = await api.prioritizeQueuedJob('a'.repeat(32))
    assert.equal(requestInfo.url, `/api/jobs/${'a'.repeat(32)}/queue-position`)
    assert.equal(JSON.parse(requestInfo.options.body).direction, 'front')
    assert.equal(result.queuePosition, 1)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('AI assistant streams split SSE events and reports each content delta', async () => {
  const originalFetch = globalThis.fetch
  const encoder = new TextEncoder()
  const deltas = []
  const statuses = []
  const sources = []
  const encodedChunks = [
    encoder.encode('event: status\r\ndata: {"message":"正在分析问题"}\r\n\r\nevent: status\r'),
    encoder.encode('\ndata: {"message":"正在检索系统文档"}\r\n\r\nevent: source\r\ndata: {"documentId":"usage-guide","title":"使用指南","path":"docs/usage.md","section":"保存并关闭","startLine":30,"absolutePath":"C:/secret"}\r'),
    encoder.encode('\n\r\nevent: source\r\ndata: {"documentId":"usage-guide","title":"使用指南","path":"docs/usage.md","section":"保存并关闭","startLine":30}\r\n\r\nevent: delta\r'),
    encoder.encode('\ndata: {"content":"第一段"}\r\n\r'),
    encoder.encode('\nevent: delta\r\ndata: {"content":"回答"}\r'),
    encoder.encode('\n\r\nevent: done\r\ndata: {}\r\n\r'),
    encoder.encode('\n'),
  ]
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    body: {
      getReader() {
        let index = 0
        return { read: async () => index < encodedChunks.length
          ? { done: false, value: encodedChunks[index++] }
          : { done: true, value: undefined } }
      },
    },
  })
  try {
    const result = await api.chatStream(
      [{ role: 'user', content: '怎么导出？' }],
      { currentPage: 2 },
      delta => deltas.push(delta),
      {
        onStatus: status => statuses.push(status),
        onSource: source => sources.push(source),
      },
    )
    assert.deepEqual(deltas, ['第一段', '回答'])
    assert.deepEqual(statuses, ['正在分析问题', '正在检索系统文档'])
    assert.deepEqual(sources, [{
      documentId: 'usage-guide', title: '使用指南', path: 'docs/usage.md', section: '保存并关闭', startLine: 30,
    }])
    assert.deepEqual(result, { message: { role: 'assistant', content: '第一段回答', sources } })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('AI assistant conversation history loads from and persists to the server', async () => {
  const originalFetch = globalThis.fetch
  const requests = []
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url: String(url), options })
    return { ok: true, json: async () => ({ conversations: [] }) }
  }
  try {
    await api.getAiConversations()
    await api.saveAiConversations([{ id: 'c-1', title: '识别流程', updatedAt: 1, messages: [] }])
    assert.equal(requests[0].url, '/api/ai/conversations')
    assert.equal(requests[1].url, '/api/ai/conversations')
    assert.equal(requests[1].options.method, 'PUT')
    assert.deepEqual(JSON.parse(requests[1].options.body), {
      conversations: [{ id: 'c-1', title: '识别流程', updatedAt: 1, messages: [] }],
    })
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('tutorial workspace and page details use tutorial lazy endpoints', async () => {
  const originalFetch = globalThis.fetch
  const requested = []
  globalThis.fetch = async url => {
    requested.push(String(url))
    return { ok: true, json: async () => ({ pages: [], page: { page: 9 } }) }
  }
  try {
    await api.getTutorialSession()
    await api.getJobPage('tutorial-000207', 9)
    assert.deepEqual(requested, [
      '/api/tutorial/session?view=workspace',
      '/api/tutorial/pages/9',
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})
