import { normalizeAssistantSource } from './assistantSources.js'

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(payload.error || `请求失败：HTTP ${response.status}`)
    error.status = response.status
    error.payload = payload
    if (response.status === 401) globalThis.window?.dispatchEvent?.(new Event('drawing-marker:authentication-required'))
    throw error
  }
  return payload
}

async function streamAssistantChat(messages, context, onDelta, options = {}) {
  const response = await fetch('/api/ai/chat/stream', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, context }),
    signal: options.signal,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const error = new Error(payload.error || `请求失败：HTTP ${response.status}`)
    error.status = response.status
    error.payload = payload
    if (response.status === 401) globalThis.window?.dispatchEvent?.(new Event('drawing-marker:authentication-required'))
    throw error
  }
  if (!response.body?.getReader) throw new Error('浏览器不支持 AI 流式响应')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let content = ''
  let completed = false
  const sources = []
  const sourceKeys = new Set()

  function consumeEvent(block) {
    let eventName = 'message'
    const dataLines = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim()
      if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const payload = JSON.parse(dataLines.join('\n'))
    if (eventName === 'delta') {
      const delta = typeof payload.content === 'string' ? payload.content : ''
      if (delta) {
        content += delta
        onDelta?.(delta)
      }
    } else if (eventName === 'status') {
      if (typeof payload.message === 'string' && payload.message.trim()) options.onStatus?.(payload.message.trim())
    } else if (eventName === 'source') {
      const source = normalizeAssistantSource(payload)
      if (!source) return
      const key = `${source.documentId}\0${source.section}\0${source.startLine}`
      if (!sourceKeys.has(key)) {
        sourceKeys.add(key)
        sources.push(source)
        options.onSource?.(source)
      }
    } else if (eventName === 'done') {
      completed = true
    } else if (eventName === 'error') {
      throw new Error(payload.error || 'AI 回答传输中断')
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer = (buffer + decoder.decode(value || new Uint8Array(), { stream: !done })).replaceAll('\r\n', '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      consumeEvent(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
  if (buffer.trim()) consumeEvent(buffer)
  if (!completed) throw new Error('AI 回答传输意外中断')
  if (!content.trim()) throw new Error('AI 服务返回了空回答')
  return { message: { role: 'assistant', content, sources } }
}

function requestWithUploadProgress(url, options, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    let uploaded = 0
    let uploadTotal = 0
    xhr.open(options.method || 'GET', url)
    xhr.withCredentials = true
    xhr.setRequestHeader('Content-Type', options.contentType || 'application/json')
    Object.entries(options.headers || {}).forEach(([name, value]) => xhr.setRequestHeader(name, value))
    xhr.upload.onprogress = event => {
      uploaded = event.loaded
      uploadTotal = event.lengthComputable ? event.total : 0
      onProgress?.({ loaded: uploaded, total: uploadTotal, complete: false })
    }
    xhr.upload.onload = () => onProgress?.({ loaded: uploadTotal || uploaded, total: uploadTotal || uploaded, complete: true })
    xhr.onerror = () => reject(new Error('上传失败：无法连接服务器'))
    xhr.onabort = () => reject(new Error('上传已取消'))
    xhr.onload = () => {
      let payload = {}
      try { payload = xhr.responseText ? JSON.parse(xhr.responseText) : {} } catch { /* use the HTTP status below */ }
      if (xhr.status < 200 || xhr.status >= 300) {
        const error = new Error(payload.error || `请求失败：HTTP ${xhr.status}`)
        error.status = xhr.status
        error.payload = payload
        if (xhr.status === 401) globalThis.window?.dispatchEvent?.(new Event('drawing-marker:authentication-required'))
        reject(error)
        return
      }
      resolve(payload)
    }
    xhr.send(options.body || null)
  })
}

async function getJobReferenceManifest(jobId) {
  const manifest = await request(jobId === 'tutorial-000207' ? '/api/tutorial/references' : `/api/jobs/${jobId}/references`)
  return (manifest.files || []).map(item => ({
    ...item,
    weldMarkerReferenceIndex: Number(item.index),
    lazyReference: true,
  }))
}

async function getJobReferenceFile(item) {
  const response = await fetch(item.url)
  if (!response.ok) throw new Error(`无法恢复对照 PDF：${item.name || `文件 ${Number(item.index) + 1}`}`)
  const blob = await response.blob()
  const file = new File([blob], item.name || `reference-${Number(item.index) + 1}.pdf`, {
    type: blob.type || 'application/pdf', lastModified: Date.now()
  })
  Object.defineProperty(file, 'weldMarkerReferenceIndex', { configurable: true, value: Number(item.index) })
  return file
}

async function getJobTargetFile(jobId, fileName = 'target.pdf') {
  const response = await fetch('/api/jobs/' + jobId + '/target')
  if (!response.ok) throw new Error('无法从服务器恢复图纸：HTTP ' + response.status)
  const blob = await response.blob()
  return new File([blob], fileName, { type: blob.type || 'application/pdf', lastModified: Date.now() })
}

async function getTutorialFile() {
  const response = await fetch('/api/tutorial/sample')
  if (!response.ok) throw new Error(`无法载入教程图纸：HTTP ${response.status}`)
  const blob = await response.blob()
  return new File([blob], '000207.pdf', { type: blob.type || 'application/pdf', lastModified: Date.now() })
}

export const api = {
  health: () => request('/api/health'),
  me: () => request('/api/auth/me'),
  register: payload => request('/api/auth/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: payload => request('/api/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  logout: () => request('/api/auth/logout', { method: 'POST', body: '{}' }),
  getAdminOverview: () => request('/api/admin/overview'),
  getAdminAiBalance: () => request('/api/admin/ai-balance'),
  getAdminUsers: ({ search = '', page = 1, pageSize = 20 } = {}) => {
    const query = new URLSearchParams({ search, page: String(page), pageSize: String(pageSize) })
    return request(`/api/admin/users?${query}`)
  },
  getAiStatus: () => request('/api/ai/status'),
  getAiConversations: () => request('/api/ai/conversations'),
  saveAiConversations: conversations => request('/api/ai/conversations', {
    method: 'PUT', body: JSON.stringify({ conversations })
  }),
  chatStream: (messages, context = {}, onDelta, options = {}) => streamAssistantChat(messages, context, onDelta, options),
  getPcfFolders: () => request('/api/pcf-folders'),
  getRecentBatch: () => request('/api/jobs/recent-batch'),
  getAnalysisQueue: ({ scope = 'current', page = 1, pageSize = 20 } = {}) => {
    const query = new URLSearchParams({ scope, page: String(page), pageSize: String(pageSize) })
    return request(`/api/analysis-queue?${query}`)
  },
  moveQueuedJob: (jobId, direction) => request('/api/jobs/' + jobId + '/queue-position', { method: 'POST', body: JSON.stringify({ direction }) }),
  prioritizeQueuedJob: jobId => request('/api/jobs/' + jobId + '/queue-position', { method: 'POST', body: JSON.stringify({ direction: 'front' }) }),
  archiveJob: (jobId, archived = true) => request('/api/jobs/' + jobId + '/archive', { method: 'POST', body: JSON.stringify({ archived }) }),
  deleteJob: jobId => request('/api/jobs/' + jobId, { method: 'DELETE' }),
  getJobTargetFile,
  getTutorialSession: () => request('/api/tutorial/session?view=workspace'),
  getTutorialFile,
  getJobReferenceManifest,
  getJobReferenceFile,
  uploadFile: (file, onUploadProgress) => requestWithUploadProgress('/api/uploads', {
    method: 'POST',
    body: file,
    contentType: file?.type || 'application/octet-stream',
    headers: { 'X-File-Name': encodeURIComponent(file?.name || 'upload.bin') },
  }, onUploadProgress),
  createJob: (payload, onUploadProgress) => {
    const options = { method: 'POST', body: JSON.stringify(payload) }
    return onUploadProgress ? requestWithUploadProgress('/api/jobs', options, onUploadProgress) : request('/api/jobs', options)
  },
  getJob: jobId => request(`/api/jobs/${jobId}`),
  getJobWorkspace: (jobId, page = null) => request(`/api/jobs/${jobId}?view=workspace${page == null ? '' : `&page=${Math.max(1, Number(page) || 1)}`}`),
  cancelJob: jobId => request(`/api/jobs/${jobId}/cancel`, { method: 'POST', body: '{}' }),
  getJobPage: (jobId, page) => request(jobId === 'tutorial-000207' ? `/api/tutorial/pages/${page}` : `/api/jobs/${jobId}/pages/${page}`),
  getPageLocks: jobId => request(`/api/jobs/${jobId}/locks`),
  acquirePageLock: (jobId, page, clientInstanceId) => request(`/api/jobs/${jobId}/pages/${page}/lock`, {
    method: 'POST', body: JSON.stringify({ clientInstanceId })
  }),
  heartbeatPageLock: (jobId, page, clientInstanceId, lockToken) => request(`/api/jobs/${jobId}/pages/${page}/lock/heartbeat`, {
    method: 'POST', body: JSON.stringify({ clientInstanceId, lockToken })
  }),
  releasePageLock: (jobId, page, clientInstanceId, lockToken, keepalive = false) => request(`/api/jobs/${jobId}/pages/${page}/lock`, {
    method: 'DELETE', body: JSON.stringify({ clientInstanceId, lockToken }), keepalive
  }),
  savePage: (jobId, page, pageData, basePageRevision, clientInstanceId, lockToken) => request(`/api/jobs/${jobId}/pages/${page}`, {
    method: 'PUT', body: JSON.stringify({ page: pageData, basePageRevision, clientInstanceId, lockToken })
  }),
  exportPdf: (jobId, candidates) => request(`/api/jobs/${jobId}/export`, {
    method: 'POST',
    body: JSON.stringify({ candidates })
  }),
  exportTutorialPdf: candidates => request('/api/tutorial/export', {
    method: 'POST',
    body: JSON.stringify({ candidates })
  }),
  audit: (action, details = {}) => request('/api/audit', {
    method: 'POST', body: JSON.stringify({ action, details })
  })
}
