export function createPageSaver({ request, getPage, makeId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}` }) {
  const uncertain = new Map()
  async function confirm(jobId, page, attempt) {
    const response = await getPage(jobId, page)
    const latest = response.page || response
    if (Number(latest.page) !== Number(page)) return null
    for (let candidate = attempt; candidate; candidate = candidate.previous) {
      if (latest.reviewSaveRequestId !== candidate.id || Number(latest.reviewRevision) !== candidate.baseRevision + 1) continue
      return { attempt: candidate, page: latest, metadata: { saved: true, page: Number(page), reviewRevision: latest.reviewRevision,
        reviewedBy: latest.reviewedBy, reviewedAt: latest.reviewedAt, reviewSaveRequestId: latest.reviewSaveRequestId } }
    }
    return null
  }
  function unknown(cause) {
    const error = new Error('保存回执未收到，暂时无法确认服务器是否已保存。修改已保留，请恢复网络后再点击保存核实。', { cause })
    error.code = 'SAVE_OUTCOME_UNKNOWN'; error.outcomeUnknown = true
    return error
  }
  function resolveConfirmed(key, confirmed, signature) {
    uncertain.delete(key)
    if (signature === confirmed.attempt.signature) return confirmed.metadata
    const error = new Error('上次保存已确认，但当前内容已经变化。修改已保留，请再次保存当前内容。')
    error.code = 'SAVE_CONTENT_CHANGED'; error.confirmedSave = confirmed.metadata; error.confirmedPage = confirmed.page
    throw error
  }
  return async function savePage(jobId, page, pageData, basePageRevision, clientInstanceId, lockToken) {
    const key = JSON.stringify([jobId, Number(page), clientInstanceId])
    const values = { page: pageData, basePageRevision, clientInstanceId, lockToken }
    const signature = JSON.stringify(values)
    let attempt = uncertain.get(key)
    if (attempt) {
      let saved
      try { saved = await confirm(jobId, page, attempt) } catch (cause) { throw unknown(cause) }
      if (saved) {
        return resolveConfirmed(key, saved, signature)
      }
      // A deliberate user save with changed content gets a new ID. Retain the
      // old ID: if the late old write wins the revision race, it must never be
      // mistaken for successful persistence of these newer edits.
      if (signature !== attempt.signature) attempt = { id: makeId(), signature, baseRevision: Number(basePageRevision) || 0, previous: attempt }
    } else attempt = { id: makeId(), signature, baseRevision: Number(basePageRevision) || 0 }
    try {
      const saved = await request(`/api/jobs/${jobId}/pages/${page}`, {
        // Send the immutable snapshot, even if verification awaited a slow
        // read while the caller's reactive page object changed.
        method: 'PUT', body: signature.slice(0, -1) + ',"saveRequestId":' + JSON.stringify(attempt.id) + '}',
      })
      if (!saved?.saved || Number(saved.reviewRevision) !== attempt.baseRevision + 1) {
        const error = new Error('保存回执不完整'); error.outcomeUnknown = true; throw error
      }
      uncertain.delete(key)
      return saved
    } catch (cause) {
      if (!cause?.outcomeUnknown && !(uncertain.has(key) && cause?.status === 409)) throw cause
      uncertain.set(key, attempt)
      let confirmed
      try {
        confirmed = await confirm(jobId, page, attempt)
      } catch { /* Keep the request ID for verification after connectivity returns. */ }
      if (confirmed) return resolveConfirmed(key, confirmed, signature)
      throw unknown(cause)
    }
  }
}
