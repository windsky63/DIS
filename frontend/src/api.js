const readAsBase64 = file => new Promise((resolve, reject) => {
  const reader = new FileReader()
  reader.onerror = () => reject(new Error(`无法读取文件：${file.name}`))
  reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
  reader.readAsDataURL(file)
})

export async function encodeFile(file) {
  if (!file) return null
  return { name: file.name, dataBase64: await readAsBase64(file) }
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || `请求失败：HTTP ${response.status}`)
  return payload
}

async function getJobReferenceFiles(jobId) {
  const manifest = await request(jobId === 'tutorial-000207' ? '/api/tutorial/references' : `/api/jobs/${jobId}/references`)
  return Promise.all((manifest.files || []).map(async item => {
    const response = await fetch(item.url)
    if (!response.ok) throw new Error(`无法恢复对照 PDF：${item.name || `文件 ${item.index + 1}`}`)
    const blob = await response.blob()
    const file = new File([blob], item.name || `reference-${item.index + 1}.pdf`, {
      type: blob.type || 'application/pdf', lastModified: Date.now()
    })
    Object.defineProperty(file, 'weldMarkerReferenceIndex', { configurable: true, value: Number(item.index) })
    return file
  }))
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
  getPcfFolders: () => request('/api/pcf-folders'),
  getRecentBatch: () => request('/api/jobs/recent-batch'),
  getAnalysisQueue: () => request('/api/analysis-queue'),
  moveQueuedJob: (jobId, direction) => request('/api/jobs/' + jobId + '/queue-position', { method: 'POST', body: JSON.stringify({ direction }) }),
  archiveJob: (jobId, archived = true) => request('/api/jobs/' + jobId + '/archive', { method: 'POST', body: JSON.stringify({ archived }) }),
  deleteJob: jobId => request('/api/jobs/' + jobId, { method: 'DELETE' }),
  getJobTargetFile,
  getTutorialSession: () => request('/api/tutorial/session'),
  getTutorialFile,
  getJobReferenceFiles,
  getJobReferenceManifest,
  getJobReferenceFile,
  createJob: payload => request('/api/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  getJob: jobId => request(`/api/jobs/${jobId}`),
  getJobWorkspace: (jobId, page = null) => request(`/api/jobs/${jobId}?view=workspace${page == null ? '' : `&page=${Math.max(1, Number(page) || 1)}`}`),
  cancelJob: jobId => request(`/api/jobs/${jobId}/cancel`, { method: 'POST', body: '{}' }),
  getJobPage: (jobId, page) => request(`/api/jobs/${jobId}/pages/${page}`),
  getJobPageDetails: jobId => request(`/api/jobs/${jobId}/page-details`),
  saveJob: (jobId, pages, markerAppearances = {}, baseRevision = null) => request(`/api/jobs/${jobId}`, {
    method: 'PUT',
    body: JSON.stringify({ pages, ...markerAppearances, baseRevision })
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
