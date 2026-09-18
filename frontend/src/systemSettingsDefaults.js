import { createDefaultMarkerAppearance } from './defaultMarkerAppearance.js'
import { createManualNumberingSettings } from './manualNumberingSettings.js'
import { DEFAULT_MANUAL_LEADER_LENGTH } from './manualMarkerPlacement.js'

export function createSystemSettingsDefaults() {
  return {
    themePreference: 'light',
    leftDrawerOpen: true,
    reviewDrawerOpen: true,
    tooltipsEnabled: false,
    operationMessagesEnabled: false,
    errorMessagesEnabled: true,
    pageButtonsPerGroup: 5,
    clearReferencesOnDesignUpload: true,
    autoReferenceWindow: false,
    referenceHintLocation: 'reference',
    canvasPerformanceMode: 'auto',
    markerAppearance: createDefaultMarkerAppearance(),
    manualNumbering: createManualNumberingSettings(),
    manualLeaderLength: DEFAULT_MANUAL_LEADER_LENGTH,
  }
}
