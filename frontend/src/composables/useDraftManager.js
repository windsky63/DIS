import { computed, ref } from 'vue'

export function useDraftManager({ storage, error, showNotice, beforeOpen } = {}) {
  const dialog = ref(false)
  const loading = ref(false)
  const entries = ref([])
  const deleteTarget = ref(null)
  const selectedKeys = ref([])
  const batchDeleteDialog = ref(false)
  const batchDeleting = ref(false)
  const selectionMode = ref(false)
  const allSelected = computed(() => entries.value.length > 0 && selectedKeys.value.length === entries.value.length)

  async function open() {
    beforeOpen?.()
    dialog.value = true
    loading.value = true
    selectedKeys.value = []
    selectionMode.value = false
    try {
      entries.value = await storage.list()
    } catch (cause) {
      error.value = `读取浏览器草稿失败：${cause.message || cause}`
    } finally {
      loading.value = false
    }
  }

  async function load(entry) {
    loading.value = true
    try {
      return await storage.load(entry?.key).catch(() => null)
    } finally {
      loading.value = false
    }
  }

  async function removeTarget() {
    const entry = deleteTarget.value
    if (!entry) return
    try {
      await storage.remove(entry.key)
      entries.value = entries.value.filter(item => item.key !== entry.key)
      selectedKeys.value = selectedKeys.value.filter(key => key !== entry.key)
      deleteTarget.value = null
      showNotice(`已删除草稿“${entry.payload?.fileName || '未命名图纸'}”。`)
    } catch (cause) {
      error.value = `删除草稿失败：${cause.message || cause}`
    }
  }

  function toggleAll(selected) {
    selectedKeys.value = selected ? entries.value.map(entry => entry.key) : []
  }

  function toggleSelectionMode() {
    selectionMode.value = !selectionMode.value
    if (!selectionMode.value) selectedKeys.value = []
  }

  function toggleSelection(key) {
    selectedKeys.value = selectedKeys.value.includes(key)
      ? selectedKeys.value.filter(item => item !== key)
      : [...selectedKeys.value, key]
  }

  async function removeSelected() {
    const keys = [...selectedKeys.value]
    if (!keys.length || batchDeleting.value) return
    batchDeleting.value = true
    try {
      await Promise.all(keys.map(key => storage.remove(key)))
      const deleted = new Set(keys)
      entries.value = entries.value.filter(entry => !deleted.has(entry.key))
      selectedKeys.value = []
      selectionMode.value = false
      batchDeleteDialog.value = false
      showNotice(`已删除 ${keys.length} 个浏览器草稿。`)
    } catch (cause) {
      error.value = `批量删除草稿失败：${cause.message || cause}`
    } finally {
      batchDeleting.value = false
    }
  }

  return {
    dialog,
    loading,
    entries,
    deleteTarget,
    selectedKeys,
    batchDeleteDialog,
    batchDeleting,
    selectionMode,
    allSelected,
    open,
    load,
    removeTarget,
    toggleAll,
    toggleSelectionMode,
    toggleSelection,
    removeSelected,
  }
}
