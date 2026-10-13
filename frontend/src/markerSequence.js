import { isDesignComponent, isSpecialMarker } from './numbering.js'

function markerType(item) {
  if (isSpecialMarker(item)) return 'special'
  if (isDesignComponent(item) || ['valve', 'flange', 'support'].includes(item?.componentType)) {
    return item.componentType
  }
  return 'weld'
}

export function parseMarkerSequenceNumber(value) {
  const text = String(value ?? '').trim()
  const match = /^(.*\D|^)(\d+)(\D*)$/.exec(text)
  if (!match) return null
  return {
    prefix: match[1],
    value: Number(match[2]),
    width: match[2].length,
    suffix: match[3],
  }
}

export function shiftMarkerNumbersAfterSelection(page, selected) {
  if (!page || !selected || selected.included === false) return { changed: [], reason: 'missing-selection' }
  const anchor = parseMarkerSequenceNumber(selected.number)
  if (!anchor) return { changed: [], reason: 'invalid-number' }

  const selectedType = markerType(selected)
  const changed = []
  for (const item of page.candidates || []) {
    if (item === selected || item.id === selected.id || item.included === false || markerType(item) !== selectedType) continue
    const current = parseMarkerSequenceNumber(item.number)
    if (!current || current.prefix !== anchor.prefix || current.suffix !== anchor.suffix || current.value < anchor.value) continue
    const nextValue = current.value + 1
    const nextDigits = String(nextValue).padStart(current.width, '0')
    const previousNumber = item.number
    item.number = `${current.prefix}${nextDigits}${current.suffix}`
    changed.push({ id: item.id, previousNumber, number: item.number })
  }
  return { changed, reason: changed.length ? '' : 'no-following-markers' }
}
