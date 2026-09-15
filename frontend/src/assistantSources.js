const DOCUMENT_ID_PATTERN = /^[a-z0-9-]{1,64}$/

export function normalizeAssistantSource(value) {
  if (!value || typeof value !== 'object') return null
  const documentId = String(value.documentId || '')
  const title = String(value.title || '').trim().slice(0, 160)
  const path = String(value.path || '').replaceAll('\\', '/').trim()
  const section = String(value.section || '').trim().slice(0, 240)
  const startLine = Number(value.startLine)
  const approvedPath = path === 'README.md' || /^docs\/[a-z0-9-]+\.md$/i.test(path)
  if (!DOCUMENT_ID_PATTERN.test(documentId) || !title || !section || !approvedPath) return null
  if (!Number.isInteger(startLine) || startLine < 1) return null
  return { documentId, title, path, section, startLine }
}

export function normalizeAssistantSources(values, limit = 12) {
  const sources = []
  const seen = new Set()
  for (const value of Array.isArray(values) ? values : []) {
    const source = normalizeAssistantSource(value)
    if (!source) continue
    const key = `${source.documentId}\0${source.section}\0${source.startLine}`
    if (seen.has(key)) continue
    seen.add(key)
    sources.push(source)
    if (sources.length >= limit) break
  }
  return sources
}
