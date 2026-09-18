function readFileEntry(entry) {
  return new Promise((resolve, reject) => entry.file(resolve, reject))
}

function readDirectoryBatch(reader) {
  return new Promise((resolve, reject) => reader.readEntries(resolve, reject))
}

export class FolderFileLimitError extends Error {
  constructor(limit) {
    super(`文件夹中的文件超过 ${limit} 个，请选择较小的文件夹`)
    this.name = 'FolderFileLimitError'
    this.limit = limit
  }
}

export function enforceFolderFileLimit(files, maxFiles = 1000) {
  const normalized = [...(files || [])]
  if (normalized.length > maxFiles) throw new FolderFileLimitError(maxFiles)
  return normalized
}

async function traverseEntry(entry, files, maxFiles) {
  if (entry?.isFile) {
    files.push(await readFileEntry(entry))
    if (files.length > maxFiles) throw new FolderFileLimitError(maxFiles)
    return
  }
  if (!entry?.isDirectory) return

  const reader = entry.createReader()
  // Chromium returns directory entries in batches of at most 100. Keep
  // reading from the same reader until its empty batch marks the end.
  while (true) {
    const entries = await readDirectoryBatch(reader)
    if (!entries.length) break
    for (const child of entries) await traverseEntry(child, files, maxFiles)
  }
}

export async function filesFromFolderDrop(event, { maxFiles = Number.POSITIVE_INFINITY } = {}) {
  const transfer = event?.dataTransfer ?? event?.clipboardData
  if (!transfer) return []

  const entries = [...(transfer.items || [])]
    .filter(item => item.kind === 'file')
    .map(item => item.webkitGetAsEntry?.())
    .filter(Boolean)

  if (!entries.length) return enforceFolderFileLimit(transfer.files, maxFiles)

  const files = []
  for (const entry of entries) await traverseEntry(entry, files, maxFiles)
  return files
}
