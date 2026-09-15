const DB_NAME = 'weld-marker-workspace'
const DB_VERSION = 2
const storedFileKeyCache = new WeakMap()
const knownStoredFileKeys = new Set()

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onerror = () => reject(request.error)
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains('snapshots')) database.createObjectStore('snapshots', { keyPath: 'key' })
      if (!database.objectStoreNames.contains('draftMetadata')) database.createObjectStore('draftMetadata', { keyPath: 'key' })
      if (!database.objectStoreNames.contains('workspaceManifests')) database.createObjectStore('workspaceManifests', { keyPath: 'key' })
      if (!database.objectStoreNames.contains('workspacePages')) database.createObjectStore('workspacePages', { keyPath: 'key' })
      if (!database.objectStoreNames.contains('workspaceFiles')) database.createObjectStore('workspaceFiles', { keyPath: 'key' })
    }
    request.onsuccess = () => resolve(request.result)
  })
}

async function transaction(storeName, mode, operation) {
  const database = await openDatabase()
  return new Promise((resolve, reject) => {
    const tx = database.transaction(storeName, mode)
    const store = tx.objectStore(storeName)
    const request = operation(store)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
    tx.oncomplete = () => database.close()
  })
}

export async function fileFingerprint(file) {
  if (!file) return ''
  const sliceSize = 64 * 1024
  const first = await file.slice(0, sliceSize).arrayBuffer()
  const last = await file.slice(Math.max(0, file.size - sliceSize), file.size).arrayBuffer()
  const metadata = new TextEncoder().encode(`${file.name}|${file.size}|${file.lastModified}`)
  const bytes = new Uint8Array(metadata.length + first.byteLength + last.byteLength)
  bytes.set(metadata)
  bytes.set(new Uint8Array(first), metadata.length)
  bytes.set(new Uint8Array(last), metadata.length + first.byteLength)
  return sha256Hex(bytes)
}

// Web Crypto is only exposed in secure browser contexts. The application is
// also commonly deployed on an internal server over plain HTTP, so keep a
// small standards-compatible fallback instead of making draft fingerprinting
// abort an otherwise successful PDF load.
export async function sha256Hex(bytes, cryptoProvider = globalThis.crypto) {
  if (cryptoProvider?.subtle?.digest) {
    try {
      const digest = await cryptoProvider.subtle.digest('SHA-256', bytes)
      return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
    } catch { /* use the local implementation below */ }
  }

  const input = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes)
  const paddedLength = Math.ceil((input.length + 9) / 64) * 64
  const padded = new Uint8Array(paddedLength)
  padded.set(input)
  padded[input.length] = 0x80
  const view = new DataView(padded.buffer)
  const bitLength = input.length * 8
  view.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000), false)
  view.setUint32(paddedLength - 4, bitLength >>> 0, false)

  const constants = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ])
  const hash = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19])
  const words = new Uint32Array(64)
  const rotateRight = (value, amount) => (value >>> amount) | (value << (32 - amount))

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(offset + index * 4, false)
    for (let index = 16; index < 64; index += 1) {
      const left = words[index - 15]
      const right = words[index - 2]
      const sigma0 = rotateRight(left, 7) ^ rotateRight(left, 18) ^ (left >>> 3)
      const sigma1 = rotateRight(right, 17) ^ rotateRight(right, 19) ^ (right >>> 10)
      words[index] = (words[index - 16] + sigma0 + words[index - 7] + sigma1) >>> 0
    }
    let [a, b, c, d, e, f, g, h] = hash
    for (let index = 0; index < 64; index += 1) {
      const sum1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25)
      const choice = (e & f) ^ (~e & g)
      const temp1 = (h + sum1 + choice + constants[index] + words[index]) >>> 0
      const sum0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22)
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const temp2 = (sum0 + majority) >>> 0
      h = g; g = f; f = e; e = (d + temp1) >>> 0
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0
    }
    hash[0] = (hash[0] + a) >>> 0; hash[1] = (hash[1] + b) >>> 0
    hash[2] = (hash[2] + c) >>> 0; hash[3] = (hash[3] + d) >>> 0
    hash[4] = (hash[4] + e) >>> 0; hash[5] = (hash[5] + f) >>> 0
    hash[6] = (hash[6] + g) >>> 0; hash[7] = (hash[7] + h) >>> 0
  }
  return [...hash].map(value => value.toString(16).padStart(8, '0')).join('')
}

async function storedFileReference(saved) {
  if (!saved) return null
  if (saved.storedFileKey) return { ...saved, blob: undefined }
  if (!saved.blob || !(saved.blob instanceof Blob)) return null
  const sliceSize = 64 * 1024
  const first = await saved.blob.slice(0, sliceSize).arrayBuffer()
  const last = await saved.blob.slice(Math.max(0, saved.blob.size - sliceSize), saved.blob.size).arrayBuffer()
  const metadata = new TextEncoder().encode(`${saved.blob.size}|${saved.type || saved.blob.type || ''}`)
  const bytes = new Uint8Array(metadata.length + first.byteLength + last.byteLength)
  bytes.set(metadata)
  bytes.set(new Uint8Array(first), metadata.length)
  bytes.set(new Uint8Array(last), metadata.length + first.byteLength)
  const storedFileKey = storedFileKeyCache.get(saved.blob) || await sha256Hex(bytes)
  storedFileKeyCache.set(saved.blob, storedFileKey)
  return {
    storedFileKey,
    name: saved.name || 'file',
    type: saved.type || saved.blob.type || '',
    size: saved.blob.size,
    lastModified: Number(saved.lastModified) || Date.now(),
    relativePath: saved.relativePath || '',
    referenceIndex: Number.isInteger(saved.referenceIndex) ? saved.referenceIndex : null,
    blob: knownStoredFileKeys.has(storedFileKey) ? undefined : saved.blob,
  }
}

function lightPage(page) {
  if (!page) return page
  const item = { ...page, detailsLoaded: false }
  delete item.layoutObstacles
  return item
}

function serializableClone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value))
}

export async function prepareDraftV2(key, payload) {
  const activeProjectIndex = Math.max(0, Number(payload.activeProjectIndex) || 0)
  const sourceResults = Array.isArray(payload.projectResults) && payload.projectResults.length
    ? [...payload.projectResults]
    : [payload.result]
  if (payload.result) sourceResults[activeProjectIndex] = payload.result
  const resultEntries = sourceResults.map((result, projectIndex) => {
    if (!result) return null
    const header = serializableClone({ ...result, pages: undefined })
    delete header.pages
    const plainPages = (result.pages || []).map(serializableClone)
    const pages = plainPages.filter(page => page.layoutObstacles || page.detailsLoaded !== false).map(page => ({
        key: `${key}:${projectIndex}:${Number(page.page)}`,
        workspaceKey: key,
        projectIndex,
        page: Number(page.page),
        value: page,
      }))
    return { header, lightPages: plainPages.map(lightPage), pages }
  })
  const fileGroups = {}
  for (const field of ['targetFile', 'projectFiles', 'referenceFiles', 'pcfFiles']) {
    const values = field === 'targetFile' ? [payload[field]] : (payload[field] || [])
    fileGroups[field] = (await Promise.all(values.map(storedFileReference))).filter(Boolean)
  }
  const basePayload = { ...payload }
  for (const field of ['result', 'projectResults', 'targetFile', 'projectFiles', 'referenceFiles', 'pcfFiles']) delete basePayload[field]
  const serializedBasePayload = serializableClone(basePayload)
  const savedAt = new Date().toISOString()
  const candidateCount = sourceResults.reduce((total, result) => total + (result?.pages || [])
    .reduce((pageTotal, page) => pageTotal + (page.candidates || []).length, 0), 0)
  const expectedReferenceCount = Math.max(
    fileGroups.referenceFiles.length,
    Number(payload.result?.referenceFiles?.length) || 0,
  )
  return {
    manifest: {
      key,
      schema: 'weld-marker.draft.v2',
      savedAt,
      basePayload: serializedBasePayload,
      activeProjectIndex,
      results: resultEntries?.map(entry => entry && ({ header: entry.header, lightPages: entry.lightPages })),
      fileGroups: Object.fromEntries(Object.entries(fileGroups).map(([field, values]) => [
        field,
        values.map(({ blob, ...reference }) => reference),
      ])),
    },
    metadata: {
      key,
      schema: 'weld-marker.draft.v2',
      savedAt,
      fileName: payload.fileName || '',
      currentPage: Number(payload.currentPage) || 1,
      pageCount: payload.result?.pages?.length || 0,
      candidateCount,
      referenceCount: expectedReferenceCount,
      storedReferenceCount: fileGroups.referenceFiles.length,
      hasStoredTarget: fileGroups.targetFile.length > 0 || fileGroups.projectFiles.length > 0,
      archiveStatus: fileGroups.referenceFiles.length >= expectedReferenceCount ? 'complete' : 'partial',
    },
    resultEntries,
    fileGroups,
  }
}

export async function saveDraft(key, payload) {
  const prepared = await prepareDraftV2(key, payload)
  const database = await openDatabase()
  return new Promise((resolve, reject) => {
    const tx = database.transaction(
      ['draftMetadata', 'workspaceManifests', 'workspacePages', 'workspaceFiles'],
      'readwrite'
    )
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error || new Error('草稿事务已中止'))
    tx.oncomplete = () => {
      Object.values(prepared.fileGroups).flat().forEach(file => knownStoredFileKeys.add(file.storedFileKey))
      database.close()
      resolve({ key, savedAt: prepared.metadata.savedAt })
    }
    tx.objectStore('workspaceManifests').put(prepared.manifest)
    tx.objectStore('draftMetadata').put(prepared.metadata)
    prepared.resultEntries.filter(Boolean).flatMap(entry => entry.pages).forEach(page => tx.objectStore('workspacePages').put(page))
    Object.values(prepared.fileGroups).flat().forEach(file => {
      if (file.blob) tx.objectStore('workspaceFiles').put({ key: file.storedFileKey, blob: file.blob, size: file.size })
    })
  })
}

async function loadV2Draft(key) {
  const manifest = await transaction('workspaceManifests', 'readonly', store => store.get(key))
  if (!manifest) return null
  const activeIndex = Math.max(0, Number(manifest.activeProjectIndex) || 0)
  const results = (manifest.results || []).map(entry => entry && ({
    ...entry.header,
    pages: entry.lightPages || [],
    workspaceDraftSchema: manifest.schema,
  }))
  const activeResult = results[activeIndex] || results.find(Boolean) || null
  if (activeResult) {
    const currentPage = Number(manifest.basePayload?.currentPage) || Number(activeResult.pages?.[0]?.page) || 1
    const fullPage = await loadDraftPage(key, activeIndex, currentPage)
    const target = activeResult.pages.find(page => Number(page.page) === currentPage)
    if (target && fullPage) Object.assign(target, fullPage, { detailsLoaded: true })
  }
  const groups = manifest.fileGroups || {}
  const payload = {
    ...manifest.basePayload,
    workspaceDraftSchema: manifest.schema,
    result: activeResult,
    projectResults: results,
    activeProjectIndex: activeIndex,
    targetFile: groups.targetFile?.[0] || null,
    projectFiles: groups.projectFiles || [],
    referenceFiles: groups.referenceFiles || [],
    pcfFiles: groups.pcfFiles || [],
  }
  return { key, payload, savedAt: manifest.savedAt, schema: manifest.schema }
}

export async function loadDraft(key) {
  return loadV2Draft(key)
}

export function loadDraftPage(workspaceKey, projectIndex, page) {
  return transaction('workspacePages', 'readonly', store => store.get(`${workspaceKey}:${projectIndex}:${Number(page)}`))
    .then(record => record?.value || null)
}

export function loadStoredFile(reference) {
  if (!reference?.storedFileKey) return Promise.resolve(null)
  return transaction('workspaceFiles', 'readonly', store => store.get(reference.storedFileKey))
    .then(record => {
      if (!record?.blob) return null
      knownStoredFileKeys.add(reference.storedFileKey)
      return { ...reference, blob: record.blob }
    })
}

export async function listDrafts() {
  const metadata = await transaction('draftMetadata', 'readonly', store => store.getAll())
  const modern = metadata.map(item => ({
    key: item.key,
    savedAt: item.savedAt,
    schema: item.schema,
    candidateCount: item.candidateCount,
    hasStoredTarget: item.hasStoredTarget,
    archiveStatus: item.archiveStatus,
    payload: {
      fileName: item.fileName,
      currentPage: item.currentPage,
      referenceFiles: Array.from({ length: item.referenceCount || 0 }),
    },
  }))
  return modern.sort((left, right) => String(right.savedAt || '').localeCompare(String(left.savedAt || '')))
}

export async function deleteDraft(key) {
  const database = await openDatabase()
  return new Promise((resolve, reject) => {
    const tx = database.transaction(['draftMetadata', 'workspaceManifests', 'workspacePages'], 'readwrite')
    tx.onerror = () => reject(tx.error)
    tx.oncomplete = () => { database.close(); resolve() }
    tx.objectStore('draftMetadata').delete(key)
    tx.objectStore('workspaceManifests').delete(key)
    const cursor = tx.objectStore('workspacePages').openCursor()
    cursor.onsuccess = () => {
      const value = cursor.result
      if (!value) return
      if (value.value?.workspaceKey === key || value.value?.key?.startsWith(`${key}:`)) value.delete()
      value.continue()
    }
  })
}

export function loadSnapshot(key) {
  return transaction('snapshots', 'readonly', store => store.get(key))
}

export async function saveSnapshot(key, blob, width, height) {
  await transaction('snapshots', 'readwrite', store => store.put({ key, blob, width, height, savedAt: Date.now() }))
  const all = await transaction('snapshots', 'readonly', store => store.getAll())
  if (all.length > 48) {
    const oldest = all.sort((a, b) => a.savedAt - b.savedAt).slice(0, all.length - 48)
    await Promise.all(oldest.map(item => transaction('snapshots', 'readwrite', store => store.delete(item.key))))
  }
}
