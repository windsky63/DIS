import { ref } from 'vue'


export function browserClientInstanceId(storage = globalThis.sessionStorage) {
  const key = 'drawing-marker.client-instance-id'
  try {
    const existing = storage?.getItem(key)
    if (existing) return existing
    const value = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
    storage?.setItem(key, value)
    return value
  } catch {
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`
  }
}

export function usePageCollaboration({
  client,
  clientInstanceId = browserClientInstanceId(),
  getJobId,
  getCurrentPage,
  getPageData,
  saveDraftBoundary = async () => {},
  hasPendingDraftChanges = () => false,
  switchPage,
  heartbeatIntervalMs = 30_000,
}) {
  const dirtyPages = ref(new Set())
  const activeLock = ref(null)
  const lockSummaries = ref([])
  const saveState = ref('idle')
  const readOnly = ref(false)
  const leaseUncertain = ref(false)
  let heartbeatTimer = null
  let navigationGeneration = 0

  const replaceDirty = updater => {
    const next = new Set(dirtyPages.value)
    updater(next)
    dirtyPages.value = next
  }

  function markDirty(page = getCurrentPage()) {
    if (!Number.isFinite(Number(page))) return
    replaceDirty(next => next.add(Number(page)))
    saveState.value = 'pending'
  }

  async function refreshLocks() {
    const jobId = getJobId()
    if (!jobId || jobId === 'tutorial-000207') return []
    const response = await client.getPageLocks(jobId)
    lockSummaries.value = response.locks || []
    return lockSummaries.value
  }

  function startHeartbeat() {
    clearInterval(heartbeatTimer)
    if (!activeLock.value || heartbeatIntervalMs <= 0) return
    heartbeatTimer = setInterval(() => { void heartbeat() }, heartbeatIntervalMs)
    heartbeatTimer?.unref?.()
  }

  async function acquire(page) {
    const jobId = getJobId()
    if (!jobId || jobId === 'tutorial-000207') return null
    const response = await client.acquirePageLock(jobId, page, clientInstanceId)
    activeLock.value = { ...response.lock, jobId, page: Number(page) }
    readOnly.value = false
    leaseUncertain.value = false
    startHeartbeat()
    void refreshLocks().catch(() => {})
    return activeLock.value
  }

  async function heartbeat() {
    const lock = activeLock.value
    if (!lock) return null
    try {
      const response = await client.heartbeatPageLock(lock.jobId, lock.page, clientInstanceId, lock.lockToken)
      activeLock.value = { ...lock, ...response.lock, lockToken: lock.lockToken }
      leaseUncertain.value = false
      void refreshLocks().catch(() => {})
      return activeLock.value
    } catch (cause) {
      readOnly.value = true
      leaseUncertain.value = !cause?.status
      clearInterval(heartbeatTimer)
      throw cause
    }
  }

  async function saveCurrentPage() {
    const page = Number(getCurrentPage())
    if (!dirtyPages.value.has(page)) return null
    const jobId = getJobId()
    const pageData = getPageData(page)
    saveState.value = 'saving'
    try {
      await saveDraftBoundary()
      if (jobId !== 'tutorial-000207') {
        const lock = activeLock.value
        if (!lock || lock.page !== page || readOnly.value) throw new Error('当前页面锁已失效，不能保存')
        const saved = await client.savePage(
          jobId, page, pageData, Number(pageData?.reviewRevision || 0), clientInstanceId, lock.lockToken,
        )
        if (pageData) Object.assign(pageData, {
          reviewRevision: saved.reviewRevision,
          reviewedBy: saved.reviewedBy,
          reviewedAt: saved.reviewedAt,
        })
      }
      replaceDirty(next => next.delete(page))
      saveState.value = 'saved'
      return pageData
    } catch (cause) {
      saveState.value = 'error'
      throw cause
    }
  }

  async function release({ keepalive = false } = {}) {
    const lock = activeLock.value
    if (!lock) return false
    clearInterval(heartbeatTimer)
    try {
      await client.releasePageLock(lock.jobId, lock.page, clientInstanceId, lock.lockToken, keepalive)
      leaseUncertain.value = false
    } catch (cause) {
      readOnly.value = true
      leaseUncertain.value = true
      throw cause
    } finally {
      if (activeLock.value === lock) activeLock.value = null
      void refreshLocks().catch(() => {})
    }
    return true
  }

  async function enterPage(page) {
    const targetPage = Number(page)
    const oldPage = Number(getCurrentPage())
    const oldLock = activeLock.value
    if (oldLock?.page === targetPage) return true
    const generation = ++navigationGeneration
    if (dirtyPages.value.has(oldPage)) await saveCurrentPage()
    else if (hasPendingDraftChanges()) await saveDraftBoundary()
    if (oldLock) await release()
    try {
      await acquire(targetPage)
    } catch (cause) {
      readOnly.value = true
      if (oldLock && generation === navigationGeneration) {
        await acquire(oldPage).catch(() => { readOnly.value = true })
      }
      throw cause
    }
    if (generation !== navigationGeneration) return false
    await switchPage(targetPage)
    return true
  }

  async function leavePage() {
    if (dirtyPages.value.has(Number(getCurrentPage()))) await saveCurrentPage()
    else if (hasPendingDraftChanges()) await saveDraftBoundary()
    await release()
  }

  function dispose() {
    navigationGeneration += 1
    clearInterval(heartbeatTimer)
    if (activeLock.value) void release({ keepalive: true }).catch(() => {})
  }

  return {
    clientInstanceId, dirtyPages, activeLock, lockSummaries, saveState, readOnly, leaseUncertain,
    markDirty, refreshLocks, enterPage, leavePage, saveCurrentPage, heartbeat, release, dispose,
  }
}
