export function storedReferenceIndex(referenceName) {
  const match = String(referenceName || '').match(/^reference-(\d{3})__/i)
  return match ? Math.max(0, Number(match[1]) - 1) : -1
}

export function resolveReferenceFileIndex(referenceName, files) {
  const values = Array.isArray(files) ? files : []
  const storedIndex = storedReferenceIndex(referenceName)
  if (storedIndex >= 0) {
    const restoredIndex = values.findIndex(file => Number(file?.weldMarkerReferenceIndex) === storedIndex)
    if (restoredIndex >= 0) return restoredIndex
    if (!values.some(file => Number.isInteger(file?.weldMarkerReferenceIndex)) && storedIndex < values.length) return storedIndex
  }

  const identity = String(referenceName || '').replaceAll('\\', '/')
  const matches = values.map((file, index) => ({ file, index })).filter(({ file }) => {
    const relativePath = String(file?.webkitRelativePath || '').replaceAll('\\', '/')
    return identity === relativePath || identity === file?.name || identity.endsWith(`__${file?.name}`)
  })
  return matches.length === 1 ? matches[0].index : -1
}

export function referenceFileSlot(file, fallbackIndex) {
  const restoredIndex = Number(file?.weldMarkerReferenceIndex)
  return Number.isInteger(restoredIndex) ? restoredIndex : Number(fallbackIndex) || 0
}

function positivePageNumber(value) {
  if (value === null || value === undefined || value === '') return null
  const page = Number(value)
  return Number.isFinite(page) && page >= 1 ? Math.floor(page) : null
}

export function resolveMatchedReference(researchedPage, files) {
  const reference = researchedPage?.reference || {}
  const referencePages = Array.isArray(reference.referencePages) ? reference.referencePages : []
  const eligiblePages = Array.isArray(reference.eligibleReferencePages) ? reference.eligibleReferencePages : []
  const candidates = Array.isArray(researchedPage?.candidates) ? researchedPage.candidates : []
  const firstCandidate = candidates.find(item => item?.referenceFile)
  const primaryFile = reference.referenceFile || referencePages.find(item => item?.file)?.file
    || firstCandidate?.referenceFile || eligiblePages.find(item => item?.file)?.file
  if (!primaryFile) return null

  const index = resolveReferenceFileIndex(primaryFile, files)
  if (index < 0) return null

  const relatedPage = referencePages.find(item => item?.file === primaryFile)
  const eligiblePage = eligiblePages.find(item => item?.file === primaryFile)
  const relatedCandidate = candidates.find(item => item?.referenceFile === primaryFile)
  const page = [reference.referencePage, relatedPage?.page, relatedCandidate?.referencePage, eligiblePage?.page]
    .map(positivePageNumber)
    .find(value => value !== null)

  // Missing parsed correspondence must not silently point the tutorial at page 1.
  if (page === undefined) return null
  return { index, page }
}
