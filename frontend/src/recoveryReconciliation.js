function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(Object.keys(value).sort().map(key => [key, stableValue(value[key])]))
}

function candidateSignature(page) {
  return JSON.stringify(stableValue(Array.isArray(page?.candidates) ? page.candidates : []))
}

export function classifyRecoveryPages(localPages = [], serverPages = []) {
  const serverByPage = new Map(serverPages.map(page => [Number(page?.page), page]))
  const unchangedPages = []
  const safePages = []
  const conflictPages = []
  const unavailablePages = []
  for (const localPage of localPages) {
    const page = Number(localPage?.page)
    if (!Number.isInteger(page) || page <= 0) continue
    const serverPage = serverByPage.get(page)
    if (!serverPage) { unavailablePages.push(page); continue }
    if (candidateSignature(localPage) === candidateSignature(serverPage)) {
      unchangedPages.push(page)
    } else if (Number(localPage.reviewRevision || 0) === Number(serverPage.reviewRevision || 0)) {
      safePages.push(page)
    } else {
      conflictPages.push(page)
    }
  }
  return { unchangedPages, safePages, conflictPages, unavailablePages, serverByPage }
}
