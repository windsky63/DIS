export function isDesignComponent(item) {
  return item?.componentKind === 'design-component' && ['valve', 'flange', 'support'].includes(item?.componentType)
}

export function isSpecialMarker(item) {
  return item?.componentKind === 'special-marker' || item?.componentType === 'special' || item?.specialMarker === true
}
