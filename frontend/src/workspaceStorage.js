const DB_NAME = 'weld-marker-workspace'
const DB_VERSION = 1

function openDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onerror = () => reject(request.error)
    request.onupgradeneeded = () => {
      const database = request.result
      if (!database.objectStoreNames.contains('drafts')) database.createObjectStore('drafts', { keyPath: 'key' })
      if (!database.objectStoreNames.contains('snapshots')) database.createObjectStore('snapshots', { keyPath: 'key' })
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
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')
}

export function saveDraft(key, payload) {
  return transaction('drafts', 'readwrite', store => store.put({ key, payload, savedAt: new Date().toISOString() }))
}

export function loadDraft(key) {
  return transaction('drafts', 'readonly', store => store.get(key))
}

export async function listDrafts() {
  const drafts = await transaction('drafts', 'readonly', store => store.getAll())
  return drafts.sort((left, right) => String(right.savedAt || '').localeCompare(String(left.savedAt || '')))
}

export function deleteDraft(key) {
  return transaction('drafts', 'readwrite', store => store.delete(key))
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
