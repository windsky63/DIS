export const HEADER_BUTTON_STORAGE_KEY = 'weld-marker.header-buttons'

export const HEADER_BUTTON_OPTIONS = Object.freeze([
  { key: 'analysisQueue', label: '任务队列', description: '显示图元引擎状态并打开分类任务队列。' },
  { key: 'mineruStatus', label: 'MinerU 状态', description: '显示 MinerU 服务连接状态。' },
  { key: 'projectSwitch', label: '项目切换', description: '显示当前识别项目与项目规则入口。' },
  { key: 'assistant', label: 'AI 助手', description: '显示 AI 助手入口。' },
  { key: 'tutorial', label: '操作教程', description: '显示分阶段操作教程入口。' },
  { key: 'shortcuts', label: '快捷键', description: '显示快捷键速查入口。' },
  { key: 'userMenu', label: '用户中心', description: '显示账号信息、后台管理与退出入口。' },
])

export function createHeaderButtonVisibility(value = {}) {
  return Object.fromEntries(HEADER_BUTTON_OPTIONS.map(option => [
    option.key,
    typeof value?.[option.key] === 'boolean' ? value[option.key] : true,
  ]))
}

export function loadHeaderButtonVisibility(storage = globalThis.localStorage) {
  try {
    return createHeaderButtonVisibility(JSON.parse(storage?.getItem(HEADER_BUTTON_STORAGE_KEY) || 'null'))
  } catch {
    return createHeaderButtonVisibility()
  }
}

export function saveHeaderButtonVisibility(storage, value) {
  storage?.setItem(HEADER_BUTTON_STORAGE_KEY, JSON.stringify(createHeaderButtonVisibility(value)))
}
