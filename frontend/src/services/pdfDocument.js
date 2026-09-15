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

export async function loadPdfDocument(file) {
  const [pdfjs, data] = await Promise.all([loadPdfjs(), file.arrayBuffer()])
  return pdfjs.getDocument({ data }).promise
}
