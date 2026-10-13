const BEIJING_TIME_ZONE = 'Asia/Shanghai'

export function parseRecordedTime(value) {
  const text = String(value || '').trim()
  if (!text) return null
  const explicitZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)
  const date = new Date(explicitZone ? text : `${text}+08:00`)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatBeijingTime(value, fallback = '—') {
  const date = parseRecordedTime(value)
  if (!date) return fallback
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: BEIJING_TIME_ZONE,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

export function beijingDate(daysFromToday = 0, now = new Date()) {
  const shifted = new Date(now.getTime() + daysFromToday * 86_400_000)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: BEIJING_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(shifted)
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}
