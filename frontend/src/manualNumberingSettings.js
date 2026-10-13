const TYPES = ['weld', 'valve', 'flange', 'support', 'other']

export const MANUAL_NUMBERING_DEFAULTS = Object.freeze({
  weld: Object.freeze({ prefix: '', suffix: '', start: 1, secondaryPrefix: '', secondarySuffix: '', secondaryStart: 1 }),
  valve: Object.freeze({ prefix: 'V', suffix: '', start: 1, secondaryPrefix: '', secondarySuffix: '', secondaryStart: 1 }),
  flange: Object.freeze({ prefix: 'FL', suffix: '', start: 1, secondaryPrefix: '', secondarySuffix: '', secondaryStart: 1 }),
  support: Object.freeze({ prefix: 'SP', suffix: '', start: 1, secondaryPrefix: '', secondarySuffix: '', secondaryStart: 1 }),
  other: Object.freeze({ prefix: 'M', suffix: '', start: 1, secondaryPrefix: '', secondarySuffix: '', secondaryStart: 1 }),
})

function text(value, fallback) {
  return String(value ?? fallback).trim().slice(0, 20)
}

function startingNumber(value, fallback) {
  if (value === '' || value === null) return null
  const source = value === undefined ? fallback : value
  return Math.max(1, Number.parseInt(source, 10) || 1)
}

export function createManualNumberingSettings(value = {}) {
  return Object.fromEntries(TYPES.map(type => {
    const fallback = MANUAL_NUMBERING_DEFAULTS[type]
    const supplied = value?.[type] || {}
    return [type, {
      prefix: text(supplied.prefix, fallback.prefix),
      suffix: text(supplied.suffix, fallback.suffix),
      start: startingNumber(supplied.start, fallback.start),
      secondaryPrefix: text(supplied.secondaryPrefix, fallback.secondaryPrefix),
      secondarySuffix: text(supplied.secondarySuffix, fallback.secondarySuffix),
      secondaryStart: startingNumber(supplied.secondaryStart, fallback.secondaryStart),
    }]
  }))
}

function storageKey(userId) {
  return `weld-marker.manual-numbering.${String(userId || '').trim()}`
}

export function loadManualNumberingSettings(storage, userId) {
  if (!storage || !userId) return createManualNumberingSettings()
  try { return createManualNumberingSettings(JSON.parse(storage.getItem(storageKey(userId)) || 'null')) }
  catch { return createManualNumberingSettings() }
}

export function saveManualNumberingSettings(storage, userId, value) {
  const normalized = createManualNumberingSettings(value)
  if (storage && userId) storage.setItem(storageKey(userId), JSON.stringify(normalized))
  return normalized
}

function ruleFor(value, type) {
  const settings = createManualNumberingSettings(value)
  return settings[type] || settings.other
}

export function manualNumberingSequenceKey(value, type, splitCircle = false) {
  const rule = ruleFor(value, type)
  const fields = [type, rule.prefix, rule.suffix, rule.start]
  if (splitCircle) fields.push(rule.secondaryPrefix, rule.secondarySuffix, rule.secondaryStart)
  return JSON.stringify(fields)
}

export function manualMarkerNumber(value, type, existingCount = 0, splitCircle = false) {
  const rule = ruleFor(value, type)
  const offset = Math.max(0, Number.parseInt(existingCount, 10) || 0)
  const number = rule.start == null ? '' : rule.start + offset
  return {
    number: `${rule.prefix}${number}${rule.suffix}`,
    prefix: rule.prefix,
    secondaryText: splitCircle
      ? `${rule.secondaryPrefix}${rule.secondaryStart == null ? '' : rule.secondaryStart + offset}${rule.secondarySuffix}`
      : undefined,
  }
}
