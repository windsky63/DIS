<script setup>
const props = defineProps({
  entries: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  selectedKeys: { type: Array, default: () => [] },
  selectionMode: { type: Boolean, default: false },
  allSelected: { type: Boolean, default: false },
  batchDeleting: { type: Boolean, default: false },
  deleteTarget: { type: Object, default: null },
  recoveryOptions: { type: Array, default: () => [] },
  recoveryConflictInfo: { type: Object, default: null },
  currentTargetName: { type: String, default: '' },
})

const recoveryDialog = defineModel('recoveryDialog', { type: Boolean, required: true })
const recoveryConflictDialog = defineModel('recoveryConflictDialog', { type: Boolean, default: false })
const selectedRecoveryKey = defineModel('selectedRecoveryKey', { type: String, default: '' })
const managerDialog = defineModel('managerDialog', { type: Boolean, required: true })
const batchDeleteDialog = defineModel('batchDeleteDialog', { type: Boolean, required: true })

const emit = defineEmits([
  'select-recovery', 'discard-recovery', 'restore-recovery', 'toggle-selection-mode',
  'toggle-all', 'toggle-selection', 'open-draft', 'request-delete', 'delete-selected',
  'clear-delete-target', 'delete-draft', 'resolve-recovery-conflict',
])

function rawOption(item) { return item?.raw && typeof item.raw === 'object' ? item.raw : (item || {}) }
function candidateCount(entry) {
  if (Number.isFinite(Number(entry?.candidateCount))) return Number(entry.candidateCount)
  return (entry?.payload?.result?.pages || []).reduce((total, page) => total + (page.candidates || []).length, 0)
}
function savedAt(entry) {
  const timestamp = Date.parse(entry?.savedAt || '')
  return Number.isFinite(timestamp) ? new Date(timestamp).toLocaleString('zh-CN', { hour12: false }) : '时间未知'
}
function hasStoredTarget(entry) {
  return Boolean(entry?.hasStoredTarget)
}
function canOpen(entry) { return hasStoredTarget(entry) || props.currentTargetName === entry?.payload?.fileName }
</script>

<template>
  <v-dialog v-model="recoveryDialog" persistent max-width="520">
    <v-card>
      <v-card-title>发现可恢复的标识工作区</v-card-title>
      <v-card-text>
        <div class="mb-3">系统已按优先级列出全部可用版本。默认选择优先级最高的版本，也可以自由切换。</div>
        <v-select v-model="selectedRecoveryKey" :items="recoveryOptions" item-title="title" item-value="key" label="选择要恢复的版本" @update:model-value="$emit('select-recovery', $event)">
          <template #item="{ props: itemProps, item }"><v-list-item v-bind="itemProps" :subtitle="`优先级 ${rawOption(item).priority || '-'} · ${rawOption(item).savedAt || ''}`" /></template>
        </v-select>
      </v-card-text>
      <v-card-actions><v-btn variant="text" @click="$emit('discard-recovery')">忽略</v-btn><v-spacer /><v-btn color="primary" @click="$emit('restore-recovery')">恢复并继续编辑</v-btn></v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="recoveryConflictDialog" persistent max-width="560">
    <v-card>
      <v-card-title>草稿与服务端结果存在冲突</v-card-title>
      <v-card-text>
        <div v-if="recoveryConflictInfo?.conflictPages?.length">服务端已更新的冲突页：第 {{ recoveryConflictInfo.conflictPages.join('、') }} 页。</div>
        <div v-if="recoveryConflictInfo?.unavailablePages?.length" class="mt-2">无法读取服务端结果的页面：第 {{ recoveryConflictInfo.unavailablePages.join('、') }} 页。这些页面只能先恢复为本地待保存内容。</div>
        <div class="mt-3 text-medium-emphasis">选择草稿将逐页获取编辑锁并覆盖当前可用的服务端结果；任一页失败都会保留恢复内容并提示重试。</div>
      </v-card-text>
      <v-card-actions><v-btn variant="text" @click="$emit('resolve-recovery-conflict', 'cancel')">取消恢复</v-btn><v-spacer /><v-btn variant="tonal" color="secondary" @click="$emit('resolve-recovery-conflict', 'server')">保留服务端冲突页</v-btn><v-btn color="error" @click="$emit('resolve-recovery-conflict', 'draft')">使用草稿并覆盖</v-btn></v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="managerDialog" max-width="720">
    <v-card class="draft-manager-card">
      <div class="draft-manager-header">
        <span class="draft-manager-header__icon" aria-hidden="true">▤</span>
        <div><v-card-title>浏览器草稿</v-card-title><v-card-subtitle>草稿保存在当前浏览器中，按最近保存时间排序。</v-card-subtitle></div>
        <v-spacer />
        <v-btn v-if="entries.length" size="small" color="secondary" :variant="selectionMode ? 'flat' : 'tonal'" @click="$emit('toggle-selection-mode')">{{ selectionMode ? '退出多选' : '多选' }}</v-btn>
      </div>
      <v-progress-linear v-if="loading" indeterminate color="secondary" />
      <v-card-text>
        <v-alert v-if="!loading && !entries.length" type="info" variant="tonal" density="compact">当前浏览器中没有可用草稿。</v-alert>
        <template v-else>
          <div v-if="selectionMode" class="draft-manager-toolbar">
            <v-checkbox-btn :model-value="allSelected" :indeterminate="selectedKeys.length > 0 && !allSelected" density="compact" color="secondary" aria-label="选择全部草稿" @update:model-value="$emit('toggle-all', $event)" />
            <v-btn size="small" variant="text" color="secondary" @click="$emit('toggle-all', !allSelected)">{{ allSelected ? '取消全选' : '全选' }}</v-btn>
            <span>{{ selectedKeys.length ? `已选择 ${selectedKeys.length} 项` : `共 ${entries.length} 个草稿` }}</span>
            <v-spacer />
            <v-btn size="small" color="error" variant="tonal" :disabled="!selectedKeys.length" @click="batchDeleteDialog = true">删除所选</v-btn>
          </div>
          <v-list class="draft-manager-list" lines="three">
            <v-list-item v-for="entry in entries" :key="entry.key" :class="{ 'draft-manager-list__selected': selectedKeys.includes(entry.key) }" @click="selectionMode && $emit('toggle-selection', entry.key)">
              <template #prepend><div class="draft-manager-prepend"><v-checkbox-btn v-if="selectionMode" :model-value="selectedKeys" :value="entry.key" density="compact" color="secondary" :aria-label="`选择草稿 ${entry.payload?.fileName || '未命名图纸'}`" @click.stop @update:model-value="$emit('toggle-selection', entry.key)" /><span class="draft-manager-file-icon" aria-hidden="true">PDF</span></div></template>
              <v-list-item-title>{{ entry.payload?.fileName || '未命名图纸' }}</v-list-item-title>
              <v-list-item-subtitle>{{ savedAt(entry) }} · {{ candidateCount(entry) }} 个标识 · 对照 PDF {{ entry.payload?.referenceFiles?.length || 0 }} 份</v-list-item-subtitle>
              <template #append><div class="draft-manager-actions"><v-chip v-if="entry.archiveStatus === 'partial'" size="x-small" color="warning" variant="tonal">容灾归档未完整</v-chip><template v-if="!selectionMode"><v-btn size="small" color="secondary" variant="tonal" :disabled="!canOpen(entry)" @click.stop="$emit('open-draft', entry)">打开</v-btn><v-btn size="small" color="error" variant="text" @click.stop="$emit('request-delete', entry)">删除</v-btn></template></div></template>
            </v-list-item>
          </v-list>
        </template>
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn color="primary" @click="managerDialog = false">关闭</v-btn></v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="batchDeleteDialog" persistent max-width="460">
    <v-card><v-card-title>确认批量删除草稿</v-card-title><v-card-text>确定删除选中的 {{ selectedKeys.length }} 个浏览器草稿吗？删除后无法恢复。</v-card-text><v-card-actions><v-btn variant="text" :disabled="batchDeleting" @click="batchDeleteDialog = false">取消</v-btn><v-spacer /><v-btn color="error" :loading="batchDeleting" @click="$emit('delete-selected')">删除所选</v-btn></v-card-actions></v-card>
  </v-dialog>

  <v-dialog :model-value="Boolean(deleteTarget)" persistent max-width="460" @update:model-value="value => { if (!value) emit('clear-delete-target') }">
    <v-card><v-card-title>确认删除草稿</v-card-title><v-card-text>确定删除“{{ deleteTarget?.payload?.fileName || '未命名图纸' }}”吗？删除后无法从浏览器草稿中恢复。</v-card-text><v-card-actions><v-btn variant="text" @click="$emit('clear-delete-target')">取消</v-btn><v-spacer /><v-btn color="error" @click="$emit('delete-draft')">删除草稿</v-btn></v-card-actions></v-card>
  </v-dialog>
</template>
