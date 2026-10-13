import { createDefaultMarkerAppearance } from './defaultMarkerAppearance.js'
import { createManualNumberingSettings } from './manualNumberingSettings.js'
import { normalizeManualLeaderLength } from './manualMarkerPlacement.js'
import { isDesignComponent, isSpecialMarker } from './numbering.js'

const COMPONENT_TYPES = ['valve', 'flange', 'support']

export function firstInheritedMarkerStyle(documentResult, componentType = '') {
  for (const page of documentResult?.pages || []) {
    const item = (page.candidates || []).find(candidate => !candidate.markerStyleOverride && (
      componentType ? candidate.componentType === componentType : !isDesignComponent(candidate) && !isSpecialMarker(candidate)
    ))
    if (item) return item.markerStyle || null
  }
  return null
}

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value))
}

export function createTaskMarkerSettings(value = {}) {
  const fallback = createDefaultMarkerAppearance()
  const appearance = value.appearance || value
  const components = appearance.components || value.componentMarkerStyles || {}
  return {
    appearance: {
      weld: clone({ ...fallback.weld, ...(appearance.weld || value.markerStyle || {}) }),
      components: Object.fromEntries(COMPONENT_TYPES.map(type => [
        type,
        clone({ ...fallback.components[type], ...(components[type] || {}) }),
      ])),
      special: clone({ ...fallback.special, ...(appearance.special || value.specialMarkerAppearance || {}) }),
    },
    numbering: createManualNumberingSettings(value.numbering || value.manualNumberingSettings),
    leaderLength: normalizeManualLeaderLength(value.leaderLength ?? value.manualLeaderLength),
  }
}

export function applyMarkerTypeDefault(taskValue, defaultValue, type) {
  const task = createTaskMarkerSettings(taskValue)
  const defaults = createTaskMarkerSettings(defaultValue)
  if (type === 'weld') task.appearance.weld = clone(defaults.appearance.weld)
  else if (COMPONENT_TYPES.includes(type)) task.appearance.components[type] = clone(defaults.appearance.components[type])
  else if (type === 'other' || type === 'special') task.appearance.special = clone(defaults.appearance.special)
  if (task.numbering[type]) task.numbering[type] = clone(defaults.numbering[type])
  return task
}

export function applyLeaderDefault(taskValue, leaderLength) {
  const task = createTaskMarkerSettings(taskValue)
  task.leaderLength = normalizeManualLeaderLength(leaderLength)
  return task
}

export function restoreTaskCreationSettings(defaultValue, payload = {}) {
  const defaults = createTaskMarkerSettings(defaultValue)
  return {
    numbering: createManualNumberingSettings(payload.manualNumberingSettings ?? payload.numbering ?? defaults.numbering),
    leaderLength: normalizeManualLeaderLength(payload.manualLeaderLength ?? payload.leaderLength ?? defaults.leaderLength),
  }
}
