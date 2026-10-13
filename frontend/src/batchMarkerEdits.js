import { isSpecialMarker } from './numbering.js'

export const BATCH_MARKER_TYPES = [
  { title: '焊口', value: 'weld' }, { title: '阀门', value: 'valve' },
  { title: '法兰', value: 'flange' }, { title: '支架', value: 'support' }, { title: '其他', value: 'other' },
]
export const BATCH_APPEARANCE_FIELDS = ['shape', 'color', 'frameSize', 'fontSize', 'lineWidth', 'fillOpacity']
const limits = { frameSize: [18, 64], fontSize: [7, 24], lineWidth: [.5, 4], fillOpacity: [0, 1] }
const fields = ['number', 'secondaryText', 'referenceLabel', 'included', ...BATCH_APPEARANCE_FIELDS]

export function createBatchMarkerRows(page, styleFor) {
  return (page.candidates || []).map(item => {
    const style = styleFor(item)
    const values = {
      number: String(item.number || ''), secondaryText: String(item.secondaryText || ''),
      referenceLabel: String(item.referenceLabel || ''), included: item.included !== false,
      shape: style.shape || 'circle', color: style.color || '#d4143c',
      frameSize: Number(style.frameSize) || 28,
      fontSize: Number(style.fontSize) || 15,
      lineWidth: Number(style.lineWidth) || 1.35, fillOpacity: Number(style.fillOpacity) || 0,
    }
    const type = isSpecialMarker(item) ? 'other' : (item.componentType || 'weld')
    return {
      key: JSON.stringify([page.page, item.id]), page: Number(page.page), id: item.id,
      type, typeTitle: BATCH_MARKER_TYPES.find(option => option.value === type)?.title || type,
      revision: Number(page.reviewRevision) || 0, values, original: { ...values }, changed: false,
    }
  })
}

export function updateBatchMarkerRow(row, field, value) {
  if (!fields.includes(field)) throw new Error('不支持修改该字段')
  row.values[field] = value
  row.changed = fields.some(key => row.values[key] !== row.original[key])
}

export function batchMarkerRowPatch(row) {
  const values = row.values
  for (const key of ['number', 'secondaryText', 'referenceLabel']) {
    if (String(values[key]).length > 200) throw new Error(`第 ${row.page} 页 ${row.id}：文本不能超过 200 个字符`)
  }
  if (values.included && !String(values.number).trim()) throw new Error(`第 ${row.page} 页：参与标识的对象必须填写编号`)
  if (!['circle', 'rectangle', 'diamond', 'split-circle'].includes(values.shape)) throw new Error('标识外形无效')
  if (!/^#[0-9a-f]{6}$/i.test(values.color)) throw new Error('颜色必须为六位十六进制颜色')
  for (const [key, [minimum, maximum]] of Object.entries(limits)) {
    if (values[key] === '' || !Number.isFinite(Number(values[key])) || Number(values[key]) < minimum || Number(values[key]) > maximum) {
      throw new Error(`第 ${row.page} 页：${({ frameSize: '框尺寸', fontSize: '字号', lineWidth: '线宽', fillOpacity: '填充透明度' })[key]}应在 ${minimum}–${maximum} 之间`)
    }
  }
  const patch = {}
  for (const key of ['number', 'secondaryText', 'referenceLabel', 'included']) {
    if (values[key] !== row.original[key]) patch[key] = typeof values[key] === 'string' ? values[key].trim() : values[key]
  }
  if (Object.hasOwn(patch, 'referenceLabel')) patch.referenceMatched = Boolean(patch.referenceLabel)
  if (BATCH_APPEARANCE_FIELDS.some(key => values[key] !== row.original[key])) {
    patch.markerStyle = Object.fromEntries(BATCH_APPEARANCE_FIELDS.map(key => [key, key in limits ? Number(values[key]) : values[key]]))
    patch.markerStyleOverride = true
  }
  return { page: row.page, id: row.id, revision: row.revision, patch }
}

export function applyBatchMarkerPatches(page, changes) {
  if (changes.some(change => change.page !== Number(page.page) || change.revision !== Number(page.reviewRevision || 0))) {
    throw new Error(`第 ${page.page} 页已被更新，未覆盖该页；请重新加载表格后修改。`)
  }
  const patches = new Map(changes.map(change => [change.id, change.patch]))
  const candidates = (page.candidates || []).map(item => {
    const patch = patches.get(item.id)
    if (!patch) return item
    patches.delete(item.id)
    return { ...item, ...patch }
  })
  if (patches.size) throw new Error(`第 ${page.page} 页的部分标识已不存在，请重新加载表格。`)
  return { ...page, candidates }
}
