import assert from 'node:assert/strict'
import test from 'node:test'

import { encodeCsv, safeCsvValue } from '../src/csv.js'

test('safeCsvValue neutralizes spreadsheet formulas', () => {
  assert.equal(safeCsvValue('=HYPERLINK("https://example.invalid")'), '"\'=HYPERLINK(""https://example.invalid"")"')
  assert.equal(safeCsvValue('  +1+1'), '"\'  +1+1"')
  assert.equal(safeCsvValue('F12'), '"F12"')
})

test('encodeCsv emits a BOM and CRLF rows', () => {
  assert.equal(encodeCsv([['编号'], ['F1']]), '\uFEFF"编号"\r\n"F1"')
})
