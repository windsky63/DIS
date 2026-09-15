const COMPONENT_PREFIXES = { valve: 'V', flange: 'FL', support: 'SP' }


export function isDesignComponent(item) {
  return item?.componentKind === 'design-component' && ['valve', 'flange', 'support'].includes(item?.componentType)
}

export function isSpecialMarker(item) {
  return item?.componentKind === 'special-marker' || item?.componentType === 'special' || item?.specialMarker === true
}

export function sameNumberingGroup(left, right) {
  if (isSpecialMarker(left) || isSpecialMarker(right)) return isSpecialMarker(left) && isSpecialMarker(right)
  if (isDesignComponent(left) || isDesignComponent(right)) {
    return isDesignComponent(left) && isDesignComponent(right) && left.componentType === right.componentType
  }
  return true
}

export function componentNumber(item, number) {
  return `${item?.autoNumberPrefix || COMPONENT_PREFIXES[item?.componentType] || 'C'}${number}`
}

export function numberDocumentResult(documentResult, startingNumber, options = {}) {
  const useReferenceNumber = options.useReferenceNumber !== false
  const formatWeldNumber = options.formatWeldNumber || (number => String(number))
  ;(documentResult?.pages || []).slice().sort((a, b) => a.page - b.page).forEach(page => {
    let weldNumber = startingNumber
    const componentNumbers = { valve: 1, flange: 1, support: 1 }
    const reserved = { valve: new Set(), flange: new Set(), support: new Set() }
    page.candidates.filter(isDesignComponent).forEach(item => {
      if (!useReferenceNumber || !item.referenceLabel) return
      const match = String(item.referenceLabel).match(/(\d+)$/)
      if (match) reserved[item.componentType].add(Number(match[1]))
    })
    page.candidates.filter(item => item.included !== false).sort((a, b) => a.y - b.y || a.x - b.x).forEach(item => {
      if (isSpecialMarker(item)) return
      if (isDesignComponent(item)) {
        if (useReferenceNumber && item.referenceLabel) item.number = item.referenceLabel
        else {
          while (reserved[item.componentType].has(componentNumbers[item.componentType])) componentNumbers[item.componentType] += 1
          item.number = componentNumber(item, componentNumbers[item.componentType])
          componentNumbers[item.componentType] += 1
        }
      } else {
        item.number = useReferenceNumber && item.referenceLabel ? item.referenceLabel : formatWeldNumber(weldNumber)
        weldNumber += 1
      }
    })
  })
}

export function resultHasMissingNumbers(documentResult) {
  return (documentResult?.pages || []).some(page => (
    (page.candidates || []).some(item => !String(item.number || '').trim())
  ))
}
