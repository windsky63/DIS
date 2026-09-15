export function calculateAnalysisProgress(state = {}) {
  if (['preparing', 'uploading', 'server-validation'].includes(state.phase)) {
    const transferFraction = Math.max(0, Math.min(1, (Number(state.transferPercent) || 0) / 100))
    return transferFraction * 15
  }
  const percent = Number(state.progressPercent)
  return Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0
}
