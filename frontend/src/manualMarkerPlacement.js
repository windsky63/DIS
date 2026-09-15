export const DEFAULT_MANUAL_LEADER_LENGTH = 72

export function normalizeManualLeaderLength(value) {
  return Math.max(20, Math.min(240, Number(value) || DEFAULT_MANUAL_LEADER_LENGTH))
}

export function manualLabelPosition(anchor, pageWidth, pageHeight, configuredLength = DEFAULT_MANUAL_LEADER_LENGTH) {
  const length = normalizeManualLeaderLength(configuredLength)
  return {
    x: Math.max(0, Math.min(pageWidth, anchor.x + length * .8)),
    y: Math.max(0, Math.min(pageHeight, anchor.y - length * .6)),
  }
}
