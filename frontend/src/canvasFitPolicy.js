export function canvasFitDecision({ requested, autoFit, initialFitPending, waitingForReference }) {
  const consumeInitialFit = Boolean(requested && initialFitPending && !waitingForReference)
  return {
    fit: Boolean(requested && (autoFit || consumeInitialFit)),
    consumeInitialFit,
  }
}
