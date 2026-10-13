import { computed, ref, watch } from 'vue'

export const SHORTCUT_DEFAULTS = Object.freeze({
  toggleHelp: 'F1',
  analyze: 'Ctrl+Enter',
  save: 'Ctrl+S',
  undo: 'Ctrl+Z',
  redo: 'Ctrl+Y',
  addAtPointer: 'A',
  copyMarker: 'Ctrl+C',
  pasteMarker: 'Ctrl+V',
  addWeld: 'W',
  addValve: 'V',
  addFlange: 'F',
  addSupport: 'S',
  modifyAll: 'M',
  swapWeld: 'X',
  shiftFollowing: 'Ctrl+F',
  deleteWeld: 'Backspace',
  deleteRegion: 'D',
  openReferenceWindow: 'Ctrl+M',
  fitCanvas: '0',
  previousPage: 'ArrowLeft',
  nextPage: 'ArrowRight',
})

export const SHORTCUT_SETTING_ROWS = Object.freeze([
  { group: '常用操作', action: 'toggleHelp', label: '打开或关闭快捷键面板' },
  { group: '常用操作', action: 'analyze', label: '开始智能编号' },
  { group: '常用操作', action: 'save', label: '保存校对结果' },
  { group: '常用操作', action: 'undo', label: '撤销最近编辑' },
  { group: '常用操作', action: 'redo', label: '重做最近编辑' },
  { group: '标识编辑', action: 'addAtPointer', label: '在鼠标位置新增标识' },
  { group: '标识编辑', action: 'copyMarker', label: '复制选中标识' },
  { group: '标识编辑', action: 'pasteMarker', label: '粘贴已复制标识' },
  { group: '标识编辑', action: 'addWeld', label: '进入焊口修改模式' },
  { group: '标识编辑', action: 'addValve', label: '进入阀门修改模式' },
  { group: '标识编辑', action: 'addFlange', label: '进入法兰修改模式' },
  { group: '标识编辑', action: 'addSupport', label: '进入支架修改模式' },
  { group: '标识编辑', action: 'modifyAll', label: '进入其他标识修改模式' },
  { group: '标识编辑', action: 'swapWeld', label: '交换焊口编号' },
  { group: '标识编辑', action: 'shiftFollowing', label: '顺延后续标识编号（必须含 Ctrl）' },
  { group: '标识编辑', action: 'deleteRegion', label: '进入区域删除模式' },
  { group: '标识编辑', action: 'deleteWeld', label: '删除选中焊口' },
  { group: '页面与画布', action: 'openReferenceWindow', label: '打开对照图分屏窗口' },
  { group: '页面与画布', action: 'fitCanvas', label: '画布适合窗口' },
  { group: '页面与画布', action: 'previousPage', label: '上一页' },
  { group: '页面与画布', action: 'nextPage', label: '下一页' },
])

export const SHORTCUT_MODIFIER_OPTIONS = Object.freeze([
  { title: '无', value: '' },
  { title: 'Ctrl', value: 'Ctrl' },
  { title: 'Alt', value: 'Alt' },
  { title: 'Shift', value: 'Shift' },
  { title: 'Meta', value: 'Meta' },
  { title: 'Ctrl + Shift', value: 'Ctrl+Shift' },
  { title: 'Ctrl + Alt', value: 'Ctrl+Alt' },
  { title: 'Alt + Shift', value: 'Alt+Shift' },
  { title: 'Ctrl + Alt + Shift', value: 'Ctrl+Alt+Shift' },
])

export function normalizeShortcut(value) {
  const parts = String(value || '').split('+').map(part => part.trim()).filter(Boolean)
  if (!parts.length) return ''
  const key = parts.pop()
  const modifiers = new Set(parts.map(part => part.toLowerCase()))
  return [
    modifiers.has('ctrl') ? 'Ctrl' : '',
    modifiers.has('alt') ? 'Alt' : '',
    modifiers.has('shift') ? 'Shift' : '',
    modifiers.has('meta') ? 'Meta' : '',
    key.length === 1 ? key.toUpperCase() : key,
  ].filter(Boolean).join('+')
}

export function shortcutFromEvent(event) {
  const key = event.key === ' '
    ? 'Space'
    : event.key === '+'
      ? 'Plus'
      : event.key === '-'
        ? 'Minus'
        : event.key.length === 1 ? event.key.toUpperCase() : event.key
  const implicitShift = event.key === '+'
  return [
    event.ctrlKey ? 'Ctrl' : '',
    event.altKey ? 'Alt' : '',
    event.shiftKey && !implicitShift ? 'Shift' : '',
    event.metaKey ? 'Meta' : '',
    key,
  ].filter(Boolean).join('+')
}

export function matchesShortcut(event, configured) {
  return normalizeShortcut(shortcutFromEvent(event)) === normalizeShortcut(configured)
}

export function shortcutParts(value) {
  const parts = normalizeShortcut(value).split('+').filter(Boolean)
  const key = parts.pop() || ''
  return { modifier: parts.join('+'), key }
}

function enforceRequiredModifiers(action, value) {
  const normalized = normalizeShortcut(value)
  if (action !== 'shiftFollowing' || !normalized) return normalized
  const parts = shortcutParts(normalized)
  const modifiers = parts.modifier.split('+').filter(Boolean)
  if (!modifiers.includes('Ctrl')) modifiers.unshift('Ctrl')
  return normalizeShortcut([...modifiers, parts.key].filter(Boolean).join('+'))
}

function loadShortcutSettings(storage) {
  try {
    const stored = JSON.parse(storage?.getItem('weld-marker.shortcuts') || '{}')
    return Object.fromEntries(Object.entries(SHORTCUT_DEFAULTS).map(([action, fallback]) => [
      action,
      enforceRequiredModifiers(action, stored[action] ?? fallback),
    ]))
  } catch {
    return { ...SHORTCUT_DEFAULTS }
  }
}

function createEditors(settings) {
  return Object.fromEntries(SHORTCUT_SETTING_ROWS.map(({ action }) => [action, shortcutParts(settings[action])]))
}

export function useShortcutSettings({ storage = globalThis.localStorage, onDefaultsRestored } = {}) {
  const settings = ref(loadShortcutSettings(storage))
  const editors = ref(createEditors(settings.value))
  const conflict = computed(() => {
    const entries = Object.entries(settings.value)
      .map(([action, value]) => [action, normalizeShortcut(value)])
      .filter(([, value]) => value)
    const duplicate = entries.find(([, value], index) => entries.findIndex(([, other]) => other === value) !== index)
    return duplicate ? `快捷键 ${duplicate[1]} 被重复使用，请修改后再操作。` : ''
  })

  function commitEditor(action) {
    const editor = editors.value[action] || { modifier: '', key: '' }
    settings.value[action] = enforceRequiredModifiers(action, [editor.modifier, editor.key].filter(Boolean).join('+'))
    editors.value[action] = shortcutParts(settings.value[action])
  }

  function updateModifier(action, modifier) {
    editors.value[action].modifier = modifier || ''
    commitEditor(action)
  }

  function restoreDefaults() {
    settings.value = { ...SHORTCUT_DEFAULTS }
    editors.value = createEditors(SHORTCUT_DEFAULTS)
    onDefaultsRestored?.()
  }

  function captureKey(event, action) {
    if (event.key === 'Tab') return
    event.preventDefault()
    if (event.key === 'Backspace') {
      editors.value[action].key = ''
      commitEditor(action)
      return
    }
    if (['Control', 'Alt', 'Shift', 'Meta'].includes(event.key)) return
    editors.value[action].key = shortcutParts(shortcutFromEvent(event)).key
    commitEditor(action)
  }

  watch(settings, value => storage?.setItem('weld-marker.shortcuts', JSON.stringify(value)), { deep: true })

  return {
    settings,
    editors,
    conflict,
    rows: SHORTCUT_SETTING_ROWS,
    modifierOptions: SHORTCUT_MODIFIER_OPTIONS,
    commitEditor,
    updateModifier,
    restoreDefaults,
    captureKey,
  }
}
