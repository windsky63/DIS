import { api } from '../api.js'


export async function uploadJobInputs(target, references = [], pcfs = [], onProgress) {
  const entries = [
    { kind: 'target', file: target },
    ...references.map(file => ({ kind: 'reference', file })),
    ...pcfs.map(file => ({ kind: 'pcf', file })),
  ]
  const totalBytes = Math.max(1, entries.reduce((sum, entry) => sum + Math.max(0, Number(entry.file?.size) || 0), 0))
  let completedBytes = 0
  const uploaded = []
  for (let index = 0; index < entries.length; index += 1) {
    const entry = entries[index]
    const record = await api.uploadFile(entry.file, ({ loaded, complete }) => {
      const currentBytes = Math.min(Number(entry.file?.size) || 0, Number(loaded) || 0)
      onProgress?.({
        loaded: completedBytes + currentBytes,
        total: totalBytes,
        fileName: entry.file?.name || '',
        fileIndex: index,
        fileCount: entries.length,
        awaitingServer: Boolean(complete),
      })
    })
    completedBytes += Math.max(0, Number(entry.file?.size) || 0)
    uploaded.push({ kind: entry.kind, record })
  }
  return {
    targetUpload: uploaded.find(entry => entry.kind === 'target')?.record || null,
    referenceUploads: uploaded.filter(entry => entry.kind === 'reference').map(entry => entry.record),
    pcfUploads: uploaded.filter(entry => entry.kind === 'pcf').map(entry => entry.record),
  }
}
