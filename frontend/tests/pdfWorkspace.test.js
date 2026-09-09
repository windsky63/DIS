import assert from 'node:assert/strict'
import test from 'node:test'
import { readEditablePdfAttachments } from '../src/pdfWorkspace.js'

test('editable workspace is read from the PDF.js 6 Map and lazy attachment API', async () => {
  const payload = { schema: 'weld-marker.editable.v1', pages: [{ page: 1, candidates: [] }], sourceFileName: 'source.pdf' }
  const contents = new Map([
    ['weld-marker-result.json', new TextEncoder().encode(JSON.stringify(payload))],
    ['weld-marker-source.pdf', new Uint8Array([37, 80, 68, 70])],
  ])
  const document = {
    getAttachments: async () => new Map([
      ['weld-marker-result.json', { filename: '焊口标识可编辑数据.json' }],
      ['weld-marker-source.pdf', { filename: '图纸标识识别系统原始底图.pdf' }],
    ]),
    getAttachmentContent: async name => contents.get(name),
  }

  const restored = await readEditablePdfAttachments(document)
  assert.deepEqual(restored.embedded, payload)
  assert.deepEqual([...restored.sourceContent], [37, 80, 68, 70])
})
