import assert from 'node:assert/strict'
import test from 'node:test'

import {
  FolderFileLimitError,
  enforceFolderFileLimit,
  filesFromFolderDrop,
} from '../src/folderDrop.js'


function fileEntry(file) {
  return {
    isFile: true,
    isDirectory: false,
    file(resolve) { resolve(file) },
  }
}

function directoryEntry(...batches) {
  return {
    isFile: false,
    isDirectory: true,
    createReader() {
      let index = 0
      return {
        readEntries(resolve) { resolve(batches[index++] || []) },
      }
    },
  }
}

test('folder drop reads every browser directory batch after the first 100 entries', async () => {
  const files = Array.from({ length: 150 }, (_, index) => ({ name: `${index + 1}.pdf` }))
  const entry = directoryEntry(
    files.slice(0, 100).map(fileEntry),
    files.slice(100).map(fileEntry),
    [],
  )

  const result = await filesFromFolderDrop({
    dataTransfer: {
      items: [{ kind: 'file', webkitGetAsEntry: () => entry }],
      files: files.slice(0, 100),
    },
  })

  assert.equal(result.length, 150)
  assert.deepEqual(result, files)
})

test('folder drop recursively reads nested directories and falls back to plain files', async () => {
  const nestedFile = { name: 'nested.pdf' }
  const nested = directoryEntry([fileEntry(nestedFile)], [])
  const root = directoryEntry([nested], [])
  assert.deepEqual(await filesFromFolderDrop({
    dataTransfer: { items: [{ kind: 'file', webkitGetAsEntry: () => root }], files: [] },
  }), [nestedFile])

  const plainFiles = [{ name: 'one.pdf' }, { name: 'two.pdf' }]
  assert.deepEqual(await filesFromFolderDrop({
    dataTransfer: { items: [], files: plainFiles },
  }), plainFiles)
})

test('folder drop stops and rejects as soon as a 1001st file is encountered', async () => {
  const files = Array.from({ length: 1100 }, (_, index) => ({ name: `${index + 1}.pdf` }))
  const entry = directoryEntry(
    files.slice(0, 100).map(fileEntry),
    ...Array.from({ length: 10 }, (_, index) => files.slice(100 + index * 100, 200 + index * 100).map(fileEntry)),
    [],
  )

  await assert.rejects(
    () => filesFromFolderDrop({
      dataTransfer: { items: [{ kind: 'file', webkitGetAsEntry: () => entry }], files: [] },
    }, { maxFiles: 1000 }),
    error => error instanceof FolderFileLimitError && error.limit === 1000,
  )
})

test('clicked folder selection enforces the same 1000 file limit', () => {
  const files = Array.from({ length: 1001 }, (_, index) => ({ name: `${index + 1}.pdf` }))
  assert.throws(
    () => enforceFolderFileLimit(files, 1000),
    error => error instanceof FolderFileLimitError && error.limit === 1000,
  )
  assert.equal(enforceFolderFileLimit(files.slice(0, 1000), 1000).length, 1000)
})
