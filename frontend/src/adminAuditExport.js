function csvCell(value) {
  const raw = String(value ?? '')
  const text = /^[=+\-@]/.test(raw) ? `'${raw}` : raw
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

export function auditEventsCsv(events, { actionLabel = value => value, sourceLabel = value => value } = {}) {
  const rows = [["时间", "来源", "操作", "用户", "任务编号", "图纸", "页码", "详情"]]
  for (const event of events || []) {
    rows.push([
      event.timestamp,
      sourceLabel(event.source),
      actionLabel(event.action),
      event.username,
      event.jobId,
      event.targetName,
      event.pageNumber ?? '',
      JSON.stringify(event.details || {}),
    ])
  }
  return `\uFEFF${rows.map(row => row.map(csvCell).join(',')).join('\r\n')}\r\n`
}
