import assert from 'node:assert/strict'
import test from 'node:test'

import { businessStatsCsv } from '../src/adminBusinessExport.js'


test('business CSV neutralizes spreadsheet formulas and quotes text', () => {
  const csv = businessStatsCsv([{ project: '=HYPERLINK("bad")', jobs: 1 }])

  assert.match(csv, /^\ufeff/)
  assert.match(csv, /"'=HYPERLINK\(""bad""\)"/)
})
