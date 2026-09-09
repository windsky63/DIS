export function safeCsvValue(value) {
  let text = String(value ?? '')
  if (/^[\t\r\n ]*[=+\-@]/.test(text)) text = `'${text}`
  return `"${text.replaceAll('"', '""')}"`
}

export function encodeCsv(rows) {
  return '\uFEFF' + rows.map(row => row.map(safeCsvValue).join(',')).join('\r\n')
}
