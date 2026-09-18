const TYPES = ['weld', 'valve', 'flange', 'support']

export const MANUAL_NUMBERING_DEFAULTS = Object.freeze({
  weld: Object.freeze({ prefix: '', suffix: '', start: 1 }),
  valve: Object.freeze({ prefix: 'V', suffix: '', start: 1 }),
  flange: Object.freeze({ prefix: 'FL', suffix: '', start: 1 }),
  support: Object.freeze({ prefix: 'SP', suffix: '', start: 1 }),
})

function text(value, fallback) {
  return String(value ?? fallback).trim().slice(0, 20)
}

function startingNumber(value, fallback) {
  return Math.max(1, Number.parseInt(value ?? fallback, 10) || 1)
}

export function createManualNumberingSettings(value = {}) {
  return Object.fromEntries(TYPES.map(type => {
    const fallback = MANUAL_NUMBERING_DEFAULTS[type]
    const supplied = value?.[type] || {}
    return [type, {
      prefix: text(supplied.prefix, fallback.prefix),
      suffix: text(supplied.suffix, fallback.suffix),
      start: startingNumber(supplied.start, fallback.start),
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
  return settings[type] || { prefix: 'M', suffix: '', start: 1 }
}

export function manualNumberingSequenceKey(value, type) {
  const rule = ruleFor(value, type)
  return JSON.stringify([type, rule.prefix, rule.suffix, rule.start])
}

export function manualMarkerNumber(value, type, existingCount = 0) {
  const rule = ruleFor(value, type)
  const number = rule.start + Math.max(0, Number.parseInt(existingCount, 10) || 0)
  return {
    number: `${rule.prefix}${number}${rule.suffix}`,
    prefix: rule.prefix,
  }
}
