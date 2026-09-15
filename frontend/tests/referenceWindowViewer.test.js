import assert from 'node:assert/strict'
import test from 'node:test'

import { createReferenceViewerState } from '../src/composables/useReferenceWindowViewer.js'


test('reference viewer follows the newest matched page by default', () => {
  const viewer = createReferenceViewerState()
  viewer.accept({ fileKey: 'a.pdf:10', fileName: 'a.pdf', page: 7 })
  assert.equal(viewer.currentPage.value, 7)
  assert.equal(viewer.active.value.fileName, 'a.pdf')
})

test('reference viewer keeps manual page while following is disabled', () => {
  const viewer = createReferenceViewerState()
  viewer.accept({ fileKey: 'a.pdf:10', fileName: 'a.pdf', page: 7 })
  viewer.setFollowing(false)
  viewer.goToPage(3, 12)
  viewer.accept({ fileKey: 'a.pdf:10', fileName: 'a.pdf', page: 9 })
  assert.equal(viewer.currentPage.value, 3)
  assert.equal(viewer.latest.value.page, 9)
})

test('reference viewer applies the latest state when following resumes', () => {
  const viewer = createReferenceViewerState()
  viewer.accept({ fileKey: 'a.pdf:10', fileName: 'a.pdf', page: 7 })
  viewer.setFollowing(false)
  viewer.goToPage(3, 12)
  viewer.accept({ fileKey: 'b.pdf:20', fileName: 'b.pdf', page: 11 })
  viewer.setFollowing(true)
  assert.equal(viewer.currentPage.value, 11)
  assert.equal(viewer.active.value.fileName, 'b.pdf')
})

test('reference viewer clamps manual navigation to the loaded document', () => {
  const viewer = createReferenceViewerState()
  viewer.goToPage(0, 12)
  assert.equal(viewer.currentPage.value, 1)
  viewer.goToPage(99, 12)
  assert.equal(viewer.currentPage.value, 12)
})

test('reference viewer clears stale content when the main window has no match', () => {
  const viewer = createReferenceViewerState()
  viewer.accept({ fileKey: 'a.pdf:10', fileName: 'a.pdf', page: 7 })
  viewer.accept(null)
  assert.equal(viewer.active.value, null)
  assert.equal(viewer.currentPage.value, 1)
})

test('manual page navigation pauses following and accepts a typed page number', () => {
  const viewer = createReferenceViewerState()
  viewer.accept({ fileKey: 'a.pdf:10', fileName: 'a.pdf', page: 2 })
  viewer.navigate('8', 12)
  assert.equal(viewer.following.value, false)
  assert.equal(viewer.currentPage.value, 8)
})
