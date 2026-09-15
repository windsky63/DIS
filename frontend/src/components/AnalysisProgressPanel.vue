<script setup>
defineProps({
  state: { type: Object, required: true },
  percent: { type: Number, required: true },
  open: { type: Boolean, required: true },
  activeJobId: { type: String, default: '' },
  cancelling: { type: Boolean, default: false },
  projectProgress: { type: Number, default: 0 },
  projectCount: { type: Number, default: 1 },
})

defineEmits(['toggle', 'cancel'])

function formatBytes(value) {
  const bytes = Math.max(0, Number(value) || 0)
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(bytes >= 100 * 1024 * 1024 ? 0 : 1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}
</script>

<template>
  <div class="analysis-progress-shell" :class="{ 'analysis-progress-shell--collapsed': !open }" role="status" aria-live="polite">
    <div v-if="open" class="analysis-progress-panel">
      <div class="analysis-progress-panel__header">
        <span>{{ state.message }}</span>
        <div class="analysis-progress-panel__actions">
          <strong>{{ Math.round(percent) }}%</strong>
          <button v-if="activeJobId" type="button" class="analysis-progress-cancel" :disabled="cancelling" @click.stop="$emit('cancel')">{{ cancelling ? '取消中' : '取消解析' }}</button>
        </div>
      </div>
      <v-progress-linear :model-value="percent" color="accent" height="8" rounded striped />
      <div class="analysis-progress-panel__meta">
        <span>{{ state.fileName }}</span>
        <span v-if="['preparing', 'uploading', 'server-validation'].includes(state.phase)">上传 {{ formatBytes(state.transferLoaded) }} / {{ formatBytes(state.transferTotal) }} · 工程 {{ state.fileIndex + 1 }} / {{ state.fileCount }}</span>
        <span v-else>对照 {{ state.completedReferenceFiles || 0 }} / {{ state.totalReferenceFiles || 0 }} · 设计页 {{ state.completedPages }} / {{ state.totalPages }} · 工程 {{ projectProgress }} / {{ projectCount || 1 }}</span>
      </div>
    </div>
    <button type="button" class="analysis-progress-spine" :aria-label="open ? '收起解析进度' : '展开解析进度'" :aria-expanded="open" @click="$emit('toggle')">
      <svg class="analysis-progress-spine__icon" :class="{ 'analysis-progress-spine__icon--expanded': open }" viewBox="0 0 20 20" aria-hidden="true"><path d="m5.5 7.5 4.5 4.5 4.5-4.5" /></svg>
    </button>
  </div>
</template>
