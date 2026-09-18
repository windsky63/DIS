import { createDefaultMarkerAppearance } from './defaultMarkerAppearance.js'
import { createManualNumberingSettings } from './manualNumberingSettings.js'
import { normalizeManualLeaderLength } from './manualMarkerPlacement.js'

const COMPONENT_TYPES = ['valve', 'flange', 'support']

function clone(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value))
}

export function createTaskMarkerSettings(value = {}) {
  const fallback = createDefaultMarkerAppearance()
  const appearance = value.appearance || value
  const components = appearance.components || value.componentMarkerStyles || {}
  return {
    appearance: {
      weld: clone(appearance.weld || value.markerStyle || fallback.weld),
      components: Object.fromEntries(COMPONENT_TYPES.map(type => [
        type,
        clone(components[type] || fallback.components[type]),
      ])),
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
