export function queueSummaryChips(queue = {}) {
  return [
    { key: 'running', label: `解析中 ${Number(queue.runningCount) || 0}`, color: 'accent' },
    { key: 'queued', label: `等待 ${Number(queue.queuedCount) || 0}`, color: 'warning' },
    { key: 'concurrency', label: `并发上限 ${Number(queue.maxConcurrent) || 0}`, color: 'secondary' },
  ]
}

export function archiveActionLabel(job = {}) {
  return job.isArchived ? '移回解析队列' : '归档'
}

export function isCancellationPending(job = {}, cancellingJobIds = []) {
  return job.status === 'cancelling' || cancellingJobIds.includes(job.jobId)
}
