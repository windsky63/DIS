const MIB = 1024 * 1024

export const CANVAS_PERFORMANCE_PROFILES = Object.freeze({
  low: Object.freeze({ key: 'low', label: '节省内存', maxDimension: 4096, maxPixels: 8_000_000, cacheBytes: 96 * MIB, recommendedDeviceMemoryGb: 4, recommendedAvailableMemoryGb: 1 }),
  balanced: Object.freeze({ key: 'balanced', label: '均衡模式', maxDimension: 8192, maxPixels: 16_000_000, cacheBytes: 256 * MIB, recommendedDeviceMemoryGb: 8, recommendedAvailableMemoryGb: 2 }),
  high: Object.freeze({ key: 'high', label: '高清模式', maxDimension: 12288, maxPixels: 32_000_000, cacheBytes: 512 * MIB, recommendedDeviceMemoryGb: 16, recommendedAvailableMemoryGb: 4 }),
})

export function selectCanvasPerformanceProfile(environment = {}) {
  const memory = Number(environment.deviceMemory) || 0
  const cores = Number(environment.hardwareConcurrency) || 0
  const touchPoints = Number(environment.maxTouchPoints) || 0
  const viewportWidth = Number(environment.viewportWidth) || 0
  const compactTouchDevice = touchPoints > 0 && viewportWidth > 0 && viewportWidth <= 900

  if ((memory > 0 && memory <= 4) || (cores > 0 && cores <= 4) || compactTouchDevice) {
    return CANVAS_PERFORMANCE_PROFILES.low
  }
  if (memory >= 16 && cores >= 8 && !compactTouchDevice) {
    return CANVAS_PERFORMANCE_PROFILES.high
  }
  return CANVAS_PERFORMANCE_PROFILES.balanced
}

export function boundedCanvasPixelFactor(width, height, desiredFactor, profile) {
  const dimensionLimit = profile.maxDimension / Math.max(width, height, 1)
  const areaLimit = Math.sqrt(profile.maxPixels / Math.max(width * height, 1))
  return Math.max(Number.EPSILON, Math.min(desiredFactor, dimensionLimit, areaLimit))
}

export function trimCanvasCache(cache, byteBudget) {
  let bytes = [...cache.values()].reduce((total, canvas) => total + (canvas?.width || 0) * (canvas?.height || 0) * 4, 0)
  while (bytes > byteBudget && cache.size > 1) {
    const oldestKey = cache.keys().next().value
    const oldest = cache.get(oldestKey)
    bytes -= (oldest?.width || 0) * (oldest?.height || 0) * 4
    cache.delete(oldestKey)
  }
  return bytes
}
