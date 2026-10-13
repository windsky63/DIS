import { ref } from 'vue'
import { applyBatchMarkerPatches } from '../batchMarkerEdits.js'

function editableContent(page) {
  if (!page) return ''
  const { layoutObstacles, reviewRevision, reviewedBy, reviewedAt, reviewSaveRequestId, detailsLoaded, ...content } = page
  return JSON.stringify(content)
}


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
  savePageDraftBoundary = null,
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
  let lockRefreshGeneration = 0
  let disposed = false

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
    const generation = ++lockRefreshGeneration
    const jobId = getJobId()
    if (!jobId || jobId === 'tutorial-000207') {
      if (generation === lockRefreshGeneration) lockSummaries.value = []
      return []
    }
    const response = await client.getPageLocks(jobId)
    if (generation !== lockRefreshGeneration || getJobId() !== jobId) return lockSummaries.value
    lockSummaries.value = response.locks || []
    return lockSummaries.value
  }

  function startHeartbeat() {
    clearInterval(heartbeatTimer)
    if (!activeLock.value || heartbeatIntervalMs <= 0) return
    heartbeatTimer = setInterval(() => { void heartbeat().catch(() => {}) }, heartbeatIntervalMs)
    heartbeatTimer?.unref?.()
  }

  async function acquireLock(page, { overwriteLocal = false } = {}) {
    const jobId = getJobId()
    if (!jobId || jobId === 'tutorial-000207') return null
    const response = await client.acquirePageLock(jobId, page, clientInstanceId)
    const acquiredLock = { ...response.lock, jobId, page: Number(page) }
    try {
      if (disposed || getJobId() !== jobId) throw new Error('任务已切换，页面锁请求已取消。')
      // Recheck the server even for a cached page, under the newly acquired lock.
      if (typeof client.getJobPage === 'function') {
        const fetched = await client.getJobPage(jobId, page)
        if (disposed || getJobId() !== jobId) throw new Error('任务已切换，页面同步请求已取消。')
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
          else if (changed || local.detailsLoaded === false) Object.assign(local, latest)
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

  function isActiveLease(lock) {
    const active = activeLock.value
    return Boolean(lock && active && lock.jobId === active.jobId
      && lock.page === active.page && lock.lockToken === active.lockToken)
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
    if (!lock || disposed) return null
    try {
      const response = await client.heartbeatPageLock(lock.jobId, lock.page, clientInstanceId, lock.lockToken)
      if (disposed || !isActiveLease(lock)) return null
      activeLock.value = { ...lock, ...response.lock, lockToken: lock.lockToken }
      leaseUncertain.value = false
      void refreshLocks().catch(() => {})
      return activeLock.value
    } catch (cause) {
      if (disposed || !isActiveLease(lock)) return null
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
    let changedDuringSave = false

    if (jobId !== 'tutorial-000207') {
      try {
        if (!lock || lock.page !== page || readOnly.value) throw new Error('当前页面锁已失效，不能保存')
        const submittedContent = editableContent(pageData)
        const saved = await client.savePage(
          jobId, page, pageData, Number(pageData?.reviewRevision || 0), clientInstanceId, lock.lockToken,
        )
        changedDuringSave = editableContent(pageData) !== submittedContent
        if (pageData) Object.assign(pageData, {
          reviewRevision: saved.reviewRevision,
          reviewedBy: saved.reviewedBy,
          reviewedAt: saved.reviewedAt,
          ...(saved.reviewSaveRequestId === undefined ? {} : { reviewSaveRequestId: saved.reviewSaveRequestId }),
        })
      } catch (cause) {
        if (cause?.confirmedSave && pageData) Object.assign(pageData, {
          reviewRevision: cause.confirmedSave.reviewRevision,
          reviewedBy: cause.confirmedSave.reviewedBy, reviewedAt: cause.confirmedSave.reviewedAt,
          reviewSaveRequestId: cause.confirmedSave.reviewSaveRequestId,
        })
        serverFailure = cause
      }
    }

    if (saveDraft) {
      try {
        if (savePageDraftBoundary) await savePageDraftBoundary(page)
        else await saveDraftBoundary()
      } catch (cause) {
        draftFailure = cause
      }
    }

    const serverSaved = jobId === 'tutorial-000207' || !serverFailure
    const authoritativeCopySaved = jobId === 'tutorial-000207' ? saveDraft && !draftFailure : serverSaved
    if (authoritativeCopySaved && !changedDuringSave) {
      replaceDirty(next => next.delete(page))
      saveState.value = 'saved'
    } else {
      saveState.value = 'error'
    }
    if (serverFailure && draftFailure) {
      const failure = new AggregateError(
        [serverFailure, draftFailure],
        `服务器页面和浏览器草稿均保存失败：${serverFailure.message || serverFailure}；${draftFailure.message || draftFailure}`,
      )
      failure.outcomeUnknown = Boolean(serverFailure.outcomeUnknown)
      throw failure
    }
    if (serverFailure) {
      const failure = new Error(`${serverFailure.outcomeUnknown ? '服务器保存结果待确认' : '服务器页面保存失败'}：${serverFailure.message || serverFailure}`, { cause: serverFailure })
      failure.outcomeUnknown = Boolean(serverFailure.outcomeUnknown)
      throw failure
    }
    if (changedDuringSave) throw new Error('服务器已保存提交时的内容，但等待期间又有新修改。新修改已保留，请再次保存。')
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

  function applyMarkerEdits(jobId, changes, options = {}) {
    const pending = navigationPromise || batchSavePromise || pageSaveOperation?.promise
    if (pending) return pending.then(() => applyMarkerEdits(jobId, changes, options))
    const promise = performMarkerEdits(jobId, changes, options).finally(() => {
      if (batchSavePromise === promise) batchSavePromise = null
    })
    batchSavePromise = promise
    return promise
  }

  async function performMarkerEdits(jobId, changes, { onPageSaved = () => {}, onPageSettled = () => {}, onProgress = () => {} } = {}) {
    if (disposed || getJobId() !== jobId) throw new Error('任务已切换，请重新打开批量修改表格。')
    if (readOnly.value) throw new Error('当前核对锁已失效，请重新打开该页后再进行批量修改。')
    if (dirtyPages.value.size) await performSaveAllDirtyPages()
    const groups = new Map()
    for (const change of changes) {
      if (!groups.has(change.page)) groups.set(change.page, [])
      groups.get(change.page).push(change)
    }
    const saved = [], errors = [], warnings = []
    saveState.value = 'saving'
    let completed = 0
    for (const [page, pageChanges] of [...groups].sort(([left], [right]) => left - right)) {
      let lock = null, temporaryLock = false
      try {
        if (disposed || getJobId() !== jobId) throw new Error('任务已切换，剩余修改未保存。')
        if (readOnly.value) throw new Error('核对锁已失效，未修改该页。')
        if (jobId !== 'tutorial-000207') {
          lock = activeLock.value?.jobId === jobId && activeLock.value.page === page ? activeLock.value : null
          if (!lock) {
            lock = await acquireLock(page)
            temporaryLock = true
          } else {
            const response = await client.getJobPage(jobId, page)
            if (disposed || getJobId() !== jobId) throw new Error('任务已切换，未修改该页。')
            const latest = response.page || response
            if (Number(latest.page) !== page) throw new Error('服务器返回的页码不一致。')
            const local = getPageData(page)
            if (Number(local.reviewRevision || 0) !== Number(latest.reviewRevision || 0)) onPageReloaded(page)
            Object.assign(local, latest, { detailsLoaded: true })
          }
        } else if (getPageData(page)?.detailsLoaded === false) {
          const response = await client.getJobPage(jobId, page)
          if (disposed || getJobId() !== jobId) throw new Error('任务已切换，未修改该页。')
          const latest = response.page || response
          if (Number(latest.page) !== page) throw new Error('服务器返回的教程页码不一致。')
          Object.assign(getPageData(page), latest, { detailsLoaded: true })
        }
        if (disposed || getJobId() !== jobId) throw new Error('任务已切换，未修改该页。')
        const local = getPageData(page)
        if (!local || local.detailsLoaded === false) throw new Error('该页标识数据尚未加载。')
        const before = { ...local }
        const updated = applyBatchMarkerPatches(local, pageChanges)
        let metadata = {}
        if (jobId !== 'tutorial-000207') {
          if (readOnly.value) throw new Error('核对锁已失效，未修改该页。')
          metadata = await client.savePage(jobId, page, updated, Number(local.reviewRevision || 0), clientInstanceId, lock.lockToken)
        }
        if (disposed || getJobId() !== jobId) {
          saved.push({ page, revision: Number(metadata.reviewRevision) || 0, ids: pageChanges.map(change => change.id) })
          warnings.push(`第 ${page} 页已保存，任务已关闭或切换，未更改新工作区。`)
          continue
        }
        Object.assign(local, updated, {
          reviewRevision: metadata.reviewRevision ?? local.reviewRevision ?? 0,
          reviewedBy: metadata.reviewedBy ?? local.reviewedBy, reviewedAt: metadata.reviewedAt ?? local.reviewedAt,
          ...(metadata.reviewSaveRequestId === undefined ? {} : { reviewSaveRequestId: metadata.reviewSaveRequestId }),
        })
        saved.push({ page, revision: Number(local.reviewRevision) || 0, ids: pageChanges.map(change => change.id) })
        try { onPageSaved(before, local) } catch (cause) { warnings.push(`第 ${page} 页已保存，但撤销记录或操作记录更新失败：${cause.message || cause}`) }
        replaceDirty(next => next.delete(page))
        try {
          if (savePageDraftBoundary) await savePageDraftBoundary(page)
          else await saveDraftBoundary()
        } catch (cause) {
          if (jobId === 'tutorial-000207') replaceDirty(next => next.add(page))
          warnings.push(`第 ${page} 页浏览器草稿保存失败：${cause.message || cause}`)
        }
      } catch (cause) {
        if (cause?.confirmedSave) {
          const local = getPageData(page)
          if (local && cause.confirmedPage && !dirtyPages.value.has(page)) {
            const before = { ...local }
            Object.assign(local, cause.confirmedPage)
            try {
              onPageSaved(before, local)
              if (savePageDraftBoundary) await savePageDraftBoundary(page)
              else await saveDraftBoundary()
            } catch (failure) { warnings.push(`第 ${page} 页上次保存已确认，但本地记录更新失败：${failure.message || failure}`) }
          }
          if (local) Object.assign(local, {
            reviewRevision: cause.confirmedSave.reviewRevision,
            reviewedBy: cause.confirmedSave.reviewedBy, reviewedAt: cause.confirmedSave.reviewedAt,
            reviewSaveRequestId: cause.confirmedSave.reviewSaveRequestId,
          })
        }
        const owner = cause?.payload?.lock?.owner?.username
        errors.push({ page, message: owner ? `第 ${page} 页正在由 ${owner} 的其他窗口核对，未修改该页。` : (cause.message || String(cause)) })
      } finally {
        if (temporaryLock && lock) {
          try { await releaseLock(lock) } catch (cause) { warnings.push(`第 ${page} 页核对锁释放失败：${cause.message || cause}`) }
        }
        if (!disposed && getJobId() === jobId) onPageSettled(page)
        onProgress(++completed, groups.size)
      }
    }
    saveState.value = errors.length || warnings.length ? 'error' : 'saved'
    return { saved, errors, warnings }
  }

  async function releaseLock(lock, { keepalive = false } = {}) {
    if (!lock) return false
    if (isActiveLease(lock)) clearInterval(heartbeatTimer)
    try {
      await client.releasePageLock(lock.jobId, lock.page, clientInstanceId, lock.lockToken, keepalive)
      if (isActiveLease(lock)) leaseUncertain.value = false
    } catch (cause) {
      if (isActiveLease(lock)) {
        readOnly.value = true
        leaseUncertain.value = true
      }
      throw cause
    } finally {
      if (isActiveLease(lock)) activeLock.value = null
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

  async function acquireNavigationTarget(request, origin) {
    const candidates = [...new Set([
      request.page,
      ...request.fallbackPages.map(Number).filter(Number.isFinite),
    ])]
    let firstLockConflict = null
    for (let index = 0; index < candidates.length; index += 1) {
      const enteredPage = candidates[index]
      // Returning to the still-held origin must not rotate its token: older
      // cleanup or heartbeat requests may still refer to that lease.
      if (origin.lock?.jobId === getJobId() && origin.lock.page === enteredPage && isActiveLease(origin.lock) && !readOnly.value) {
        return { page: enteredPage, lock: origin.lock }
      }
      try {
        return { page: enteredPage, lock: await acquireLock(enteredPage) }
      } catch (cause) {
        const lockConflict = cause?.status === 423 && cause?.payload?.lock
        if (request.generation !== navigationGeneration) throw cause
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
      if (disposed) return null
      try {
        if (dirtyPages.value.has(origin.page)) await savePage(origin.page, origin.lock)
        else if (hasPendingDraftChanges()) await saveDraftBoundary()
      } catch (cause) {
        await switchPage(origin.page, { preserveLocal: true })
        throw new Error(`保存失败，请重试：${cause?.message || cause}`, { cause })
      }

      while (true) {
        if (disposed) return null
        const request = pendingNavigation
        await request.previewPromise
        if (disposed) return null
        if (request.generation !== navigationGeneration) continue
        let acquired
        try {
          acquired = await acquireNavigationTarget(request, origin)
        } catch (cause) {
          if (request.generation !== navigationGeneration) continue
          throw cause
        }
        if (disposed || request.generation !== navigationGeneration) {
          if (acquired.lock !== origin.lock) await releaseLock(acquired.lock).catch(() => {})
          if (disposed) return null
          continue
        }
        if (Number(getCurrentPage()) !== acquired.page) await switchPage(acquired.page)
        if (disposed || request.generation !== navigationGeneration) {
          if (acquired.lock !== origin.lock) await releaseLock(acquired.lock).catch(() => {})
          if (disposed) return null
          continue
        }
        if (origin.lock && origin.lock !== acquired.lock) {
          try {
            await releaseLock(origin.lock)
          } catch (cause) {
            await releaseLock(acquired.lock).catch(() => {})
            throw cause
          }
        }
        if (disposed) {
          await releaseLock(acquired.lock).catch(() => {})
          return null
        }
        if (acquired.lock) activateLock(acquired.lock)
        origin = { page: acquired.page, lock: activeLock.value }
        // A new destination can arrive even while the old lease is released.
        if (request.generation !== navigationGeneration) continue
        return acquired.page
      }
    } catch (cause) {
      if (disposed) return null
      if (Number(getCurrentPage()) !== origin.page) await switchPage(origin.page, { preserveLocal: true })
      if (origin.lock && !leaseUncertain.value && !readOnly.value) activateLock(origin.lock)
      else readOnly.value = true
      throw cause
    } finally {
      synchronizing.value = false
    }
  }

  function enterPage(page, fallbackPages = []) {
    if (disposed) return Promise.resolve(null)
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
    // Older rapid-navigation callers must not clean up page details or report a
    // lock fallback after a newer target has won the navigation race.
    return navigationPromise.then(
      enteredPage => generation === navigationGeneration ? enteredPage : null,
      cause => { if (generation !== navigationGeneration) return null; throw cause },
    )
  }

  async function leavePage() {
    if (dirtyPages.value.size) await saveAllDirtyPages()
    else if (hasPendingDraftChanges()) await saveDraftBoundary()
    await release()
  }

  function dispose() {
    disposed = true
    navigationGeneration += 1
    lockRefreshGeneration += 1
    clearInterval(heartbeatTimer)
    if (activeLock.value) void release({ keepalive: true }).catch(() => {})
  }

  return {
    clientInstanceId, dirtyPages, activeLock, lockSummaries, saveState, readOnly, leaseUncertain,
    synchronizing, markDirty, refreshLocks, enterPage, leavePage, saveCurrentPage, saveAllDirtyPages, acquireCurrentPage,
    heartbeat, release, dispose, applyMarkerEdits,
  }
}
