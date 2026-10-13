import { computed, ref } from 'vue'
import { batchMarkerRowPatch, createBatchMarkerRows, updateBatchMarkerRow } from '../batchMarkerEdits.js'

export function useBatchMarkerEditor({ getJobId, getPages, getPage, styleFor, prepare, apply, pageLockOwner = () => '' }) {
  const open = ref(false), loading = ref(false), saving = ref(false)
  const rows = ref([]), selected = ref(new Set()), messages = ref([])
  const progress = ref({ completed: 0, total: 0 }), success = ref('')
  const search = ref(''), typeFilter = ref('all'), pageFilter = ref('all'), tablePage = ref(1)
  const pageRangeStart = ref(1), pageRangeEnd = ref(1)
  const pageBounds = computed(() => {
    const numbers = getPages().map(page => Number(page.page)).filter(Number.isSafeInteger)
    return { min: numbers.length ? Math.min(...numbers) : 1, max: numbers.length ? Math.max(...numbers) : 1 }
  })
  const pageRangeError = computed(() => {
    if (pageFilter.value !== 'range') return ''
    const start = Number(pageRangeStart.value), end = Number(pageRangeEnd.value)
    if (pageRangeStart.value === '' || pageRangeStart.value == null || pageRangeEnd.value === '' || pageRangeEnd.value == null
      || !Number.isSafeInteger(start) || !Number.isSafeInteger(end)) return '请输入完整的整数页码范围。'
    if (start < pageBounds.value.min || end > pageBounds.value.max) return `页码范围应在 ${pageBounds.value.min}–${pageBounds.value.max} 页之间。`
    if (start > end) return '起始页不能大于结束页。'
    return ''
  })
  const pageSize = 15
  let generation = 0, jobId = ''
  const changedRows = computed(() => rows.value.filter(row => row.changed))
  const selectedCount = computed(() => selected.value.size)
  const filteredRows = computed(() => {
    const needle = search.value.trim().toLocaleLowerCase()
    return rows.value.filter(row => (typeFilter.value === 'all' || row.type === typeFilter.value)
      && (pageFilter.value === 'all' || (pageFilter.value === 'range'
        ? !pageRangeError.value && row.page >= Number(pageRangeStart.value) && row.page <= Number(pageRangeEnd.value)
        : row.page === pageFilter.value))
      && (!needle || `${row.page} ${row.typeTitle} ${row.values.number} ${row.values.secondaryText} ${row.values.referenceLabel}`.toLocaleLowerCase().includes(needle)))
  })
  const pageCount = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / pageSize)))
  const visibleRows = computed(() => {
    const index = Math.min(tablePage.value, pageCount.value) - 1
    return filteredRows.value.slice(index * pageSize, (index + 1) * pageSize)
  })
  const pageOptions = computed(() => [{ title: '全部页面', value: 'all' }, { title: '页码范围', value: 'range' }, ...getPages().map(page => ({ title: `第 ${page.page} 页`, value: Number(page.page) }))])
  const busy = computed(() => loading.value || saving.value)

  function updateRow(row, field, value) {
    if (busy.value) return
    updateBatchMarkerRow(row, field, value)
    row.error = ''
    success.value = ''
  }
  function toggleRow(row, checked) {
    if (checked) selected.value.add(row.key)
    else selected.value.delete(row.key)
  }
  function toggleFiltered(checked) {
    if (busy.value) return
    filteredRows.value.forEach(row => toggleRow(row, checked))
  }
  function restoreChanges() {
    rows.value.forEach(row => { row.values = { ...row.original }; row.changed = false; row.error = '' })
    messages.value = []
    success.value = ''
  }
  function numberSelected({ prefix = '', suffix = '', start = 1, step = 1 }) {
    const first = Number(start), increment = Number(step)
    if (start === '' || step === '' || !Number.isSafeInteger(first) || first < 0 || !Number.isSafeInteger(increment) || increment < 1) {
      messages.value = ['起始数字应为非负整数，步长应为正整数。']; return
    }
    let index = 0
    rows.value.forEach(row => {
      if (selected.value.has(row.key)) updateRow(row, 'number', `${prefix}${first + index++ * increment}${suffix}`)
    })
  }
  function setSelectedFields(values) {
    rows.value.forEach(row => {
      if (selected.value.has(row.key)) {
        for (const [field, value] of Object.entries(values)) {
          if (value !== null && value !== undefined && value !== '') updateRow(row, field, value)
        }
      }
    })
  }
  async function load() {
    const request = ++generation
    jobId = getJobId()
    loading.value = true
    rows.value = []; selected.value.clear(); messages.value = []; success.value = ''; tablePage.value = 1
    search.value = ''; typeFilter.value = 'all'; pageFilter.value = 'all'
    pageRangeStart.value = pageBounds.value.min; pageRangeEnd.value = pageBounds.value.max
    try {
      await prepare()
      if (request !== generation) return
      const pages = [...getPages()].sort((left, right) => left.page - right.page)
      progress.value = { completed: 0, total: pages.length }
      let cursor = 0
      const worker = async () => {
        while (cursor < pages.length && request === generation && getJobId() === jobId) {
          const number = Number(pages[cursor++].page)
          try {
            const response = await getPage(jobId, number)
            if (request !== generation || getJobId() !== jobId) return
            const page = response.page || response
            if (Number(page.page) !== number) throw new Error('返回的页码不一致')
            const compact = createBatchMarkerRows(page, styleFor)
            compact.forEach(row => { row.lockOwner = pageLockOwner(row.page); row.error = '' })
            for (const row of compact) rows.value.push(row)
          } catch (cause) {
            if (request === generation) messages.value.push(`第 ${number} 页加载失败：${cause.message || cause}`)
          }
          if (request === generation) progress.value.completed += 1
        }
      }
      await Promise.all([worker(), worker()])
      if (request === generation) rows.value.sort((left, right) => left.page - right.page)
    } catch (cause) {
      if (request === generation) messages.value = [cause.message || String(cause)]
    } finally { if (request === generation) loading.value = false }
  }
  async function show() { open.value = true; await load() }
  function close() {
    if (saving.value) return
    generation += 1
    open.value = false; loading.value = false; rows.value = []; selected.value.clear()
  }
  async function save() {
    if (busy.value || !changedRows.value.length) return
    let changes
    try { changes = changedRows.value.map(batchMarkerRowPatch) } catch (cause) { messages.value = [cause.message]; return }
    saving.value = true; messages.value = []; success.value = ''
    progress.value = { completed: 0, total: new Set(changes.map(change => change.page)).size }
    try {
      const response = await apply(jobId, changes, (completed, total) => { progress.value = { completed, total } })
      const changesByKey = new Map(changes.map(change => [JSON.stringify([change.page, change.id]), change.patch]))
      let savedCount = 0
      for (const saved of response.saved) {
        const ids = new Set(saved.ids)
        for (const row of rows.value.filter(row => row.page === saved.page)) {
          row.revision = saved.revision
          row.lockOwner = ''; row.error = ''
          if (!ids.has(row.id)) continue
          const patch = changesByKey.get(row.key)
          for (const key of ['number', 'secondaryText', 'referenceLabel', 'included']) {
            if (Object.hasOwn(patch, key)) row.values[key] = patch[key]
          }
          if (patch.markerStyle) Object.assign(row.values, patch.markerStyle)
          row.original = { ...row.values }; row.changed = false; row.error = ''; savedCount += 1
        }
      }
      for (const failure of response.errors) rows.value.filter(row => row.page === failure.page).forEach(row => { row.error = failure.message })
      messages.value = [...response.errors.map(item => item.message), ...response.warnings]
      if (savedCount) success.value = `已保存 ${savedCount} 个标识，涉及 ${response.saved.length} 页。${changedRows.value.length ? '未保存的修改仍保留在表格中。' : ''}`
    } catch (cause) { messages.value = [cause.message || String(cause)] }
    finally { saving.value = false }
  }
  return {
    open, loading, saving, rows, selected, messages, progress, success, search, typeFilter, pageFilter,
    tablePage, pageSize, pageCount, pageOptions, pageRangeStart, pageRangeEnd, pageRangeError, pageBounds, visibleRows, filteredRows, changedRows, selectedCount, busy,
    show, load, close, save, updateRow, toggleRow, toggleFiltered, restoreChanges, numberSelected, setSelectedFields,
  }
}
