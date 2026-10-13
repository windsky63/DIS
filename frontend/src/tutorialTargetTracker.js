// Track only while a tour is visible. Dialog transforms can change without a
// resize event, so publishing the first non-empty rectangle is insufficient.
export function createTutorialTargetTracker({
  findTarget, isReady = () => true, onTarget,
  requestFrame = callback => window.requestAnimationFrame(callback),
  cancelFrame = handle => window.cancelAnimationFrame(handle),
  stableMs = 120,
}) {
  let active = false, frame, generation = 0
  let previousTarget, previousRect, stableSince, published

  const sameRect = (a, b) => a && b && ['left', 'top', 'width', 'height'].every(key => Math.abs(a[key] - b[key]) < .25)
  function isAnimating(target) {
    for (let node = target; node; node = node.parentElement) {
      for (const animation of node.getAnimations?.() || []) {
        const timing = animation.effect?.getComputedTiming?.()
        if (timing?.iterations === Infinity) continue
        if (animation.pending || animation.playState === 'running') return true
      }
    }
    return false
  }
  function publish(value) {
    if (value === null && published === null) return
    if (value && published?.target === value.target && sameRect(published.rect, value.rect)) return
    published = value
    onTarget(value)
  }
  function tick(time, token) {
    if (!active || token !== generation) return
    const target = isReady() ? findTarget() : null
    const measured = target?.getBoundingClientRect()
    const visible = measured && measured.width > 0 && measured.height > 0
      && target.isConnected !== false && !target.closest?.('[aria-hidden="true"], [inert]')
    const rect = visible ? { left: measured.left, top: measured.top, width: measured.width, height: measured.height,
      right: measured.left + measured.width, bottom: measured.top + measured.height } : null
    if (!visible || isAnimating(target)) {
      previousTarget = null; previousRect = null; stableSince = undefined
      publish(null)
    } else {
      if (target !== previousTarget || !sameRect(rect, previousRect)) {
        stableSince = time
        previousTarget = target; previousRect = rect
        // Hide the old focus until the new bounds have settled.
        publish(null)
      }
      if (time - stableSince >= stableMs) publish({ target, rect })
    }
    // Keep waiting for lazy targets on weak networks while the centered card
    // remains usable. Stop immediately when the tour closes or changes steps.
    frame = requestFrame(nextTime => tick(nextTime, token))
  }
  function stop() {
    active = false; generation += 1
    if (frame !== undefined) cancelFrame(frame)
    frame = undefined
  }
  function start() {
    stop()
    active = true; previousTarget = null; previousRect = null
    stableSince = undefined; published = undefined
    publish(null)
    const token = generation
    frame = requestFrame(time => tick(time, token))
  }
  return { start, stop }
}
