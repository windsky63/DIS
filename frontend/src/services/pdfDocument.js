let pdfjsModulePromise

export function loadPdfjs() {
  if (!pdfjsModulePromise) {
    pdfjsModulePromise = Promise.all([
      import('pdfjs-dist/legacy/build/pdf.mjs'),
      import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'),
    ]).then(([pdfjs, worker]) => {
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default
      return pdfjs
    })
  }
  return pdfjsModulePromise
}

export function pdfDocumentParameters(source) {
  if (source?.url && typeof source.arrayBuffer !== 'function') {
    return {
      url: source.url,
      withCredentials: true,
      disableRange: false,
      // Keep the initial request range-only. App.vue starts the complete-file
      // archival download after the first workspace render has finished.
      disableStream: true,
      disableAutoFetch: true,
      rangeChunkSize: 64 * 1024,
    }
  }
  return null
}

export async function loadPdfDocument(source) {
  const pdfjs = await loadPdfjs()
  const remoteParameters = pdfDocumentParameters(source)
  if (remoteParameters) return pdfjs.getDocument(remoteParameters).promise
  const data = await source.arrayBuffer()
  return pdfjs.getDocument({ data }).promise
}
