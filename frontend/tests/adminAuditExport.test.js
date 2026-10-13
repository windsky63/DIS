import assert from 'node:assert/strict'
import test from 'node:test'

import { auditEventsCsv } from '../src/adminAuditExport.js'


test('audit CSV escapes details and includes a UTF-8 BOM', () => {
  const csv = auditEventsCsv([{
    timestamp: '2026-09-20T12:00:00', source: 'review', action: 'page.saved',
    username: '审核员', jobId: 'job-1', targetName: 'A,图.pdf', pageNumber: 2,
    details: { revision: 3, note: '已“核对”' },
  }], { actionLabel: () => '保存页面', sourceLabel: () => '页面审核' })

  assert.ok(csv.startsWith('\uFEFF时间,来源,操作'))
  assert.match(csv, /页面审核,保存页面,审核员/)
  assert.match(csv, /"A,图.pdf"/)
  assert.match(csv, /"\{""revision"":3/)
})

test('audit CSV neutralizes spreadsheet formulas', () => {
  const csv = auditEventsCsv([{ username: '=HYPERLINK("bad")', details: {} }])
  assert.match(csv, /"'=HYPERLINK\(""bad""\)"/)
})
