export function visibleAssistantError(configured, error) {
  return configured === false ? '' : String(error || '')
}

export function assistantConfigurationAlert(configured) {
  return configured === false
    ? { level: 'error', message: 'AI API 尚未配置，请由管理员在服务端设置 API 地址、密钥和模型后重启服务。' }
    : null
}

export function selectQuickStarts(pool, count = 4, random = Math.random) {
  const choices = [...new Set(Array.isArray(pool) ? pool : [])]
  for (let index = choices.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1))
    ;[choices[index], choices[swapIndex]] = [choices[swapIndex], choices[index]]
  }
  return choices.slice(0, Math.max(0, count))
}

export function replaceQuickStart(visible, pool, index, random = Math.random) {
  if (!Array.isArray(visible) || index < 0 || index >= visible.length) return [...(visible || [])]
  const available = [...new Set(Array.isArray(pool) ? pool : [])].filter(item => !visible.includes(item))
  if (!available.length) return [...visible]
  const replacement = available[Math.floor(random() * available.length)]
  return visible.map((item, itemIndex) => itemIndex === index ? replacement : item)
}
