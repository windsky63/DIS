export function createLatestMessageChannel(onDisplay, options = {}) {
  const interval = Math.max(0, Number(options.interval) || 0)
  const now = options.now || Date.now
  const schedule = options.schedule || setTimeout
  const cancel = options.cancel || clearTimeout
  let timer = null
  let pending = ''
  let displayed = ''
  let lastDisplayedAt = Number.NEGATIVE_INFINITY

  function flush() {
    timer = null
    if (!pending || pending === displayed) return
    displayed = pending
    pending = ''
    lastDisplayedAt = now()
    onDisplay(displayed)
  }

  function publish(message) {
    const value = String(message || '')
    if (!value) {
      clear()
      return
    }
    pending = value
    if (value === displayed || timer !== null) return
    const delay = Math.max(0, interval - (now() - lastDisplayedAt))
    if (!delay) flush()
    else timer = schedule(flush, delay)
  }

  function clear() {
    if (timer !== null) cancel(timer)
    timer = null
    pending = ''
    displayed = ''
    onDisplay('')
  }

  return { publish, clear, dispose: clear }
}
