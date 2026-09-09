import assert from 'node:assert/strict'
import test from 'node:test'

import { referenceFileSlot, resolveMatchedReference, resolveReferenceFileIndex, storedReferenceIndex } from '../src/referenceIdentity.js'

test('stored upload prefix selects the correct file when base names are duplicated', () => {
  const files = [{ name: 'same.pdf' }, { name: 'same.pdf' }]
  assert.equal(storedReferenceIndex('reference-002__same.pdf'), 1)
  assert.equal(resolveReferenceFileIndex('reference-002__same.pdf', files), 1)
})

test('restored file index survives a missing file in the manifest', () => {
  const files = [{ name: 'first.pdf', weldMarkerReferenceIndex: 0 }, { name: 'third.pdf', weldMarkerReferenceIndex: 2 }]
  assert.equal(resolveReferenceFileIndex('reference-003__third.pdf', files), 1)
  assert.equal(referenceFileSlot(files[1], 1), 2)
})

test('ambiguous legacy names do not silently select the first file', () => {
  const files = [{ name: 'same.pdf' }, { name: 'same.pdf' }]
  assert.equal(resolveReferenceFileIndex('same.pdf', files), -1)
})

test('parsed direct relationship selects its actual page instead of page one', () => {
  const files = [{ name: 'reference.pdf' }]
  const researchedPage = {
    reference: { referenceFile: 'reference.pdf', referencePage: 4 }
  }
  assert.deepEqual(resolveMatchedReference(researchedPage, files), { index: 0, page: 4 })
})

test('parsed multi-page relationship supplies the matching reference page', () => {
  const files = [{ name: 'first.pdf' }, { name: 'multi.pdf' }]
  const researchedPage = {
    reference: { referencePages: [{ file: 'multi.pdf', page: 7 }] }
  }
  assert.deepEqual(resolveMatchedReference(researchedPage, files), { index: 1, page: 7 })
})

test('candidate relationship remains available for legacy parsed results', () => {
  const files = [{ name: 'legacy.pdf' }]
  const researchedPage = {
    candidates: [{ referenceFile: 'legacy.pdf', referencePage: 3 }]
  }
  assert.deepEqual(resolveMatchedReference(researchedPage, files), { index: 0, page: 3 })
})

test('authoritative title pairing remains visible when no object matched', () => {
  const files = [{ name: 'LINE_sheet_001.pdf' }]
  const researchedPage = {
    reference: {
      referencePairingStatus: 'exact-title-1:1',
      eligibleReferencePages: [{ file: 'LINE_sheet_001.pdf', page: 1 }],
      referencePages: [],
    },
    candidates: [],
  }
  assert.deepEqual(resolveMatchedReference(researchedPage, files), { index: 0, page: 1 })
})

test('missing parsed page does not silently fall back to page one', () => {
  const files = [{ name: 'reference.pdf' }]
  const researchedPage = {
    reference: { referenceFile: 'reference.pdf' }
  }
  assert.equal(resolveMatchedReference(researchedPage, files), null)
})
