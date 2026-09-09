export function restoreWorkspaceFile(saved) {
  if (saved instanceof Blob) return saved
  if (saved?.storedFileKey && !saved?.blob) return saved
  if (!saved?.blob || !(saved.blob instanceof Blob)) return null
  const file = new File([saved.blob], saved.name || 'file', {
    type: saved.type || saved.blob.type,
    lastModified: Number(saved.lastModified) || Date.now(),
  })
  if (saved.relativePath) Object.defineProperty(file, 'webkitRelativePath', { configurable: true, value: saved.relativePath })
  if (Number.isInteger(saved.referenceIndex)) Object.defineProperty(file, 'weldMarkerReferenceIndex', { configurable: true, value: saved.referenceIndex })
  if (saved.storedFileKey) Object.defineProperty(file, 'weldMarkerStoredFileKey', { configurable: true, value: saved.storedFileKey })
  return file
}

export async function materializeWorkspaceFile(saved, loadStoredFile) {
  // Files selected by the user or downloaded by the tutorial API are already
  // usable Blob instances. They must not be interpreted as draft descriptors.
  if (saved instanceof Blob) return saved
  const restored = restoreWorkspaceFile(saved)
  if (!restored?.storedFileKey || restored instanceof Blob) return restored
  return restoreWorkspaceFile(await loadStoredFile(restored))
}
