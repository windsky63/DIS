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
  onPageReloaded = () => {},
  heartbeatIntervalMs = 30_000,
}) {
  const dirtyPages = ref(new Set())
  const activeLock = ref(null)
  const lockSummaries = ref([])
  const saveState = ref('idle')
  const readOnly = ref(false)
  const leaseUncertain = ref(false)
  const synchronizing = ref(false)
  let heartbeatTimer = null
  let navigationGeneration = 0
  let navigationPromise = null
  let pendingNavigation = null
  let pageSaveOperation = null
  let batchSavePromise = null

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

  async function acquireLock(page, { overwriteLocal = false } = {}) {
    const jobId = getJobId()
    if (!jobId || jobId === 'tutorial-000207') return null
    const response = await client.acquirePageLock(jobId, page, clientInstanceId)
    const acquiredLock = { ...response.lock, jobId, page: Number(page) }
    try {
      // Recheck the server even for a cached page, under the newly acquired lock.
      if (typeof client.getJobPage === 'function') {
        const fetched = await client.getJobPage(jobId, page)
        const latest = fetched.page || fetched
        if (Number(latest?.page) !== Number(page)) throw new Error('服务器返回的页面不一致，无法同步核对版本')
        const local = getPageData(Number(page))
        const changed = Number(local?.reviewRevision || 0) !== Number(latest.reviewRevision || 0)
        if (changed && dirtyPages.value.has(Number(page)) && !overwriteLocal) {
          throw new Error('该页服务器版本已更新，本地未保存修改已保留。请重新载入该页后再处理。')
        }
        if (local) {
          if (changed && overwriteLocal) Object.assign(local, {
            reviewRevision: latest.reviewRevision,
            reviewedBy: latest.reviewedBy,
            reviewedAt: latest.reviewedAt,
          })
          else if (changed) Object.assign(local, latest)
          else if (!local.layoutObstacles && latest.layoutObstacles) local.layoutObstacles = latest.layoutObstacles
          if (overwriteLocal && !local.layoutObstacles && latest.layoutObstacles) local.layoutObstacles = latest.layoutObstacles
          local.detailsLoaded = true
        }
        if (changed && !overwriteLocal) onPageReloaded(Number(page))
      }
    } catch (cause) {
      await releaseLock(acquiredLock).catch(() => {})
      throw cause
    }
    return acquiredLock
  }

  function activateLock(lock) {
    activeLock.value = lock
    readOnly.value = false
    leaseUncertain.value = false
    startHeartbeat()
    void refreshLocks().catch(() => {})
    return lock
  }

  async function acquire(page) {
    const lock = await acquireLock(page)
    return lock ? activateLock(lock) : null
  }

  async function acquireCurrentPage() {
    const page = Number(getCurrentPage())
    const jobId = getJobId()
    if (activeLock.value?.jobId === jobId && activeLock.value?.page === page && !readOnly.value) return activeLock.value
    if (activeLock.value) await release()
    return acquire(page)
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

  function saveCurrentPage() {
    return savePage(Number(getCurrentPage()), activeLock.value)
  }

  function savePage(page, lock) {
    if (batchSavePromise) return batchSavePromise.then(() => savePage(page, lock))
    if (pageSaveOperation?.page === page) return pageSaveOperation.promise
    if (pageSaveOperation) return pageSaveOperation.promise.then(() => savePage(page, lock))
    const promise = performSavePage(page, lock).finally(() => {
      if (pageSaveOperation?.promise === promise) pageSaveOperation = null
    })
    pageSaveOperation = { page, promise }
    return promise
  }

  async function performSavePage(page, lock, { saveDraft = true } = {}) {
    if (!dirtyPages.value.has(page)) return null
    const jobId = getJobId()
    const pageData = getPageData(page)
    saveState.value = 'saving'
    let serverFailure = null
    let draftFailure = null

    if (jobId !== 'tutorial-000207') {
      try {
        if (!lock || lock.page !== page || readOnly.value) throw new Error('当前页面锁已失效，不能保存')
        const saved = await client.savePage(
          jobId, page, pageData, Number(pageData?.reviewRevision || 0), clientInstanceId, lock.lockToken,
        )
        if (pageData) Object.assign(pageData, {
          reviewRevision: saved.reviewRevision,
          reviewedBy: saved.reviewedBy,
          reviewedAt: saved.reviewedAt,
        })
      } catch (cause) {
        serverFailure = cause
      }
    }

    if (saveDraft) {
      try {
        await saveDraftBoundary()
      } catch (cause) {
        draftFailure = cause
      }
    }

    const serverSaved = jobId === 'tutorial-000207' || !serverFailure
    const authoritativeCopySaved = jobId === 'tutorial-000207' ? saveDraft && !draftFailure : serverSaved
    if (authoritativeCopySaved) {
      replaceDirty(next => next.delete(page))
      saveState.value = 'saved'
    } else {
      saveState.value = 'error'
    }
    if (serverFailure && draftFailure) {
      throw new AggregateError(
        [serverFailure, draftFailure],
        `服务器页面和浏览器草稿均保存失败：${serverFailure.message || serverFailure}；${draftFailure.message || draftFailure}`,
      )
    }
    if (serverFailure) throw new Error(`服务器页面保存失败：${serverFailure.message || serverFailure}`, { cause: serverFailure })
    if (draftFailure) throw new Error(`浏览器草稿保存失败：${draftFailure.message || draftFailure}`, { cause: draftFailure })
    return pageData
  }

  function saveAllDirtyPages(options = {}) {
    if (batchSavePromise) return batchSavePromise
    const promise = performSaveAllDirtyPages(options).finally(() => {
      if (batchSavePromise === promise) batchSavePromise = null
    })
    batchSavePromise = promise
    return promise
  }

  async function performSaveAllDirtyPages({ overwritePages = [], skipPages = [] } = {}) {
    if (pageSaveOperation) await pageSaveOperation.promise
    const skipped = new Set(skipPages.map(Number))
    const pages = [...dirtyPages.value].filter(page => !skipped.has(page)).sort((left, right) => left - right)
    if (!pages.length) return []
    const overwrite = new Set(overwritePages.map(Number))
    const jobId = getJobId()
    const savedPages = []
    let saveFailure = null
    let draftFailure = null
    saveState.value = 'saving'
    try {
      for (const page of pages) {
        let lock = activeLock.value?.jobId === jobId && activeLock.value?.page === page ? activeLock.value : null
        let temporaryLock = false
        try {
          if (jobId !== 'tutorial-000207' && !lock) {
            lock = await acquireLock(page, { overwriteLocal: overwrite.has(page) })
            temporaryLock = true
          }
          await performSavePage(page, lock, { saveDraft: false })
          savedPages.push(page)
        } finally {
          if (temporaryLock && lock) await releaseLock(lock)
        }
      }
    } catch (cause) {
      saveFailure = cause
    }
    try {
      await saveDraftBoundary()
      if (jobId === 'tutorial-000207') replaceDirty(next => pages.forEach(page => next.delete(page)))
    } catch (cause) {
      draftFailure = cause
    }
    if (!saveFailure && !draftFailure) saveState.value = 'saved'
    else saveState.value = 'error'
    if (saveFailure && draftFailure) throw new AggregateError([saveFailure, draftFailure], `${saveFailure.message || saveFailure}；${draftFailure.message || draftFailure}`)
    if (saveFailure) throw saveFailure
    if (draftFailure) throw new Error(`浏览器草稿保存失败：${draftFailure.message || draftFailure}`, { cause: draftFailure })
    return savedPages
  }

  async function releaseLock(lock, { keepalive = false } = {}) {
    if (!lock) return false
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

  async function release(options = {}) {
    const lock = activeLock.value
    if (!lock) return false
    clearInterval(heartbeatTimer)
    return releaseLock(lock, options)
  }

  async function acquireNavigationTarget(request) {
    const candidates = [...new Set([
      request.page,
      ...request.fallbackPages.map(Number).filter(Number.isFinite),
    ])]
    let firstLockConflict = null
    for (let index = 0; index < candidates.length; index += 1) {
      const enteredPage = candidates[index]
      try {
        return { page: enteredPage, lock: await acquireLock(enteredPage) }
      } catch (cause) {
        const lockConflict = cause?.status === 423 && cause?.payload?.lock
        if (!lockConflict) throw cause
        firstLockConflict ||= cause
        if (index === candidates.length - 1) throw firstLockConflict
      }
    }
    return { page: request.page, lock: null }
  }

  async function performNavigation(origin) {
    try {
      await pendingNavigation.previewPromise
      try {
        if (dirtyPages.value.has(origin.page)) await savePage(origin.page, origin.lock)
        else if (hasPendingDraftChanges()) await saveDraftBoundary()
      } catch (cause) {
        await switchPage(origin.page, { preserveLocal: true })
        throw new Error(`保存失败，请重试：${cause?.message || cause}`, { cause })
      }

      while (true) {
        const request = pendingNavigation
        await request.previewPromise
        const acquired = await acquireNavigationTarget(request)
        if (request.generation !== navigationGeneration) {
          await releaseLock(acquired.lock).catch(() => {})
          continue
        }
        if (Number(getCurrentPage()) !== acquired.page) await switchPage(acquired.page)
        if (request.generation !== navigationGeneration) {
          await releaseLock(acquired.lock).catch(() => {})
          continue
        }
        if (acquired.lock) activateLock(acquired.lock)
        if (origin.lock && origin.lock !== acquired.lock) await releaseLock(origin.lock)
        return acquired.page
      }
    } catch (cause) {
      if (Number(getCurrentPage()) !== origin.page) await switchPage(origin.page, { preserveLocal: true })
      if (origin.lock) activateLock(origin.lock)
      else readOnly.value = true
      throw cause
    } finally {
      synchronizing.value = false
    }
  }

  function enterPage(page, fallbackPages = []) {
    const targetPage = Number(page)
    const oldPage = Number(getCurrentPage())
    const oldLock = activeLock.value
    if (!navigationPromise && oldLock?.page === targetPage && !readOnly.value) return Promise.resolve(targetPage)
    const generation = ++navigationGeneration
    synchronizing.value = true
    const request = {
      generation,
      page: targetPage,
      fallbackPages,
      previewPromise: Promise.resolve(switchPage(targetPage)),
    }
    pendingNavigation = request
    if (!navigationPromise) {
      navigationPromise = performNavigation({ page: oldPage, lock: oldLock }).finally(() => {
        navigationPromise = null
        pendingNavigation = null
      })
    }
    return navigationPromise.then(enteredPage => generation === navigationGeneration ? enteredPage : targetPage)
  }

  async function leavePage() {
    if (dirtyPages.value.size) await saveAllDirtyPages()
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
    synchronizing, markDirty, refreshLocks, enterPage, leavePage, saveCurrentPage, saveAllDirtyPages, acquireCurrentPage,
    heartbeat, release, dispose,
  }
}
