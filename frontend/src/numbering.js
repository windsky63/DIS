const COMPONENT_PREFIXES = { valve: 'V', flange: 'FL', support: 'SP' }


export function isDesignComponent(item) {
  return item?.componentKind === 'design-component' && ['valve', 'flange', 'support'].includes(item?.componentType)
}

export function isSpecialMarker(item) {
  return item?.componentKind === 'special-marker' || item?.componentType === 'special' || item?.specialMarker === true
}

export function componentNumber(item, number) {
  return `${item?.autoNumberPrefix || COMPONENT_PREFIXES[item?.componentType] || 'C'}${number}`
}

export function numberDocumentResult(documentResult, startingNumber, options = {}) {
  const useReferenceNumber = options.useReferenceNumber !== false
  const preserveExistingNumbers = options.preserveExistingNumbers === true
  const formatWeldNumber = options.formatWeldNumber || (number => String(number))
  ;(documentResult?.pages || []).slice().sort((a, b) => a.page - b.page).forEach(page => {
    let weldNumber = startingNumber
    const componentNumbers = { valve: 1, flange: 1, support: 1 }
    const reserved = { valve: new Set(), flange: new Set(), support: new Set() }
    page.candidates.filter(isDesignComponent).forEach(item => {
      const reservedNumber = useReferenceNumber && item.referenceLabel
        ? item.referenceLabel
        : preserveExistingNumbers ? item.number : ''
      const match = String(reservedNumber || '').match(/(\d+)$/)
      if (match) reserved[item.componentType].add(Number(match[1]))
    })
    page.candidates.filter(item => item.included !== false).sort((a, b) => a.y - b.y || a.x - b.x).forEach(item => {
      if (isSpecialMarker(item)) return
      if (isDesignComponent(item)) {
        if (preserveExistingNumbers && String(item.number || '').trim()) return
        if (useReferenceNumber && item.referenceLabel) item.number = item.referenceLabel
        else {
          while (reserved[item.componentType].has(componentNumbers[item.componentType])) componentNumbers[item.componentType] += 1
          item.number = componentNumber(item, componentNumbers[item.componentType])
          componentNumbers[item.componentType] += 1
        }
      } else {
        if (!(preserveExistingNumbers && String(item.number || '').trim())) {
          item.number = useReferenceNumber && item.referenceLabel ? item.referenceLabel : formatWeldNumber(weldNumber)
        }
        weldNumber += 1
      }
    })
  })
}
