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

export const api = {
  health: () => request('/api/health'),
  getPcfFolders: () => request('/api/pcf-folders'),
  getRecentBatch: () => request('/api/jobs/recent-batch'),
  createJob: payload => request('/api/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  getJob: jobId => request(`/api/jobs/${jobId}`),
  cancelJob: jobId => request(`/api/jobs/${jobId}/cancel`, { method: 'POST', body: '{}' }),
  getJobPage: (jobId, page) => request(`/api/jobs/${jobId}/pages/${page}`),
  saveJob: (jobId, pages, markerAppearances = {}, baseRevision = null) => request(`/api/jobs/${jobId}`, {
    method: 'PUT',
    body: JSON.stringify({ pages, ...markerAppearances, baseRevision })
  }),
  exportPdf: (jobId, candidates) => request(`/api/jobs/${jobId}/export`, {
    method: 'POST',
    body: JSON.stringify({ candidates })
  }),
  audit: (action, details = {}) => request('/api/audit', {
    method: 'POST', body: JSON.stringify({ action, details })
  })
}
