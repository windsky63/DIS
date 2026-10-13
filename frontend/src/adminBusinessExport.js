const HEADERS = [
  '项目', '任务数', '已完成', '失败', '图纸数', '处理页数', '已核对页数',
  '自动标识', '人工标识', '人工新增', '内容修正', '布局调整', '删除', '核对率', '成功率',
]

function safeSpreadsheetValue(value) {
  const text = String(value ?? '')
  return /^[=+\-@]/.test(text) ? `'${text}` : text
}

function csvCell(value) {
  return `"${safeSpreadsheetValue(value).replaceAll('"', '""')}"`
}

export function businessStatsCsv(projects = [], formatRate = value => value ?? '') {
  const rows = [HEADERS]
  for (const item of projects) {
    rows.push([
      item.project, item.jobs, item.completedJobs, item.failedJobs, item.documents, item.pages,
      item.reviewedPages, item.automaticMarkers, item.manualMarkers, item.manualAdditions,
      item.contentCorrections, item.layoutAdjustments, item.deletions,
      formatRate(item.reviewRate), formatRate(item.successRate),
    ])
  }
  return `\ufeff${rows.map(row => row.map(csvCell).join(',')).join('\r\n')}`
}
