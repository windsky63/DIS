import { createDefaultMarkerAppearance } from './defaultMarkerAppearance.js'
import { createManualNumberingSettings } from './manualNumberingSettings.js'
import { DEFAULT_MANUAL_LEADER_LENGTH } from './manualMarkerPlacement.js'
import { createHeaderButtonVisibility } from './headerButtonVisibility.js'

export function createSystemSettingsDefaults() {
  return {
    themePreference: 'light',
    leftDrawerOpen: true,
    reviewDrawerOpen: true,
    leftDrawerSize: 'standard',
    reviewDrawerSize: 'standard',
    tooltipsEnabled: false,
    operationMessagesEnabled: true,
    errorMessagesEnabled: true,
    messageLocation: 'bottom',
    headerButtons: createHeaderButtonVisibility(),
    pageButtonsPerGroup: 5,
    clearReferencesOnDesignUpload: true,
    autoReferenceWindow: false,
    referenceHintLocation: 'reference',
    autoFitPdf: false,
    canvasPerformanceMode: 'auto',
    markerAppearance: createDefaultMarkerAppearance(),
    manualNumbering: createManualNumberingSettings(),
    manualLeaderLength: DEFAULT_MANUAL_LEADER_LENGTH,
    manualAddInteraction: 'keyboard',
  }
}
