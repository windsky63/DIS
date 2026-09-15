<script setup>
import { onBeforeUnmount, ref } from 'vue'
import { calculateAnalysisProgress } from '../analysisProgress.js'
import { archiveActionLabel, queueSummaryChips } from '../analysisQueuePresentation.js'

const pendingArchiveId = ref('')
const expandedJobId = ref('')
let archiveConfirmTimer

function toggleExpanded(job) {
  expandedJobId.value = expandedJobId.value === job.jobId ? '' : job.jobId
}

function reviewerNames(job) {
  const names = (job.reviewers || []).map(item => item.username).filter(Boolean)
  if (!names.length) return '尚无人保存审核'
  const visible = names.slice(0, 3).join('、')
  return names.length > 3 ? `${visible} 等 ${names.length} 人` : visible
}

function statusLabel(job) {
  if (job.queueState === 'running') return job.status === 'cancelling' ? '取消中' : '解析中'
  if (job.queueState === 'queued') return `等待中 · 第 ${job.queuePosition || '-'} 位`
  return { complete: '已完成', failed: '失败', cancelled: '已取消' }[job.status] || job.status || '未知'
}

function statusColor(job) {
  return { running: 'accent', queued: 'warning', complete: 'success', failed: 'error', cancelled: 'grey' }[job.queueState] || 'grey'
}

function progress(job) {
  return calculateAnalysisProgress(job)
}

function formatStartTime(value) {
  if (!value) return '时间未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '时间未知'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

function clearArchiveConfirmation() {
  pendingArchiveId.value = ''
  clearTimeout(archiveConfirmTimer)
  archiveConfirmTimer = undefined
}

function handleArchive(job) {
  if (job.isArchived) {
    clearArchiveConfirmation()
    emit('archive', job, false)
    return
  }
  if (pendingArchiveId.value === job.jobId) {
    clearArchiveConfirmation()
    emit('archive', job, true)
    return
  }
  clearArchiveConfirmation()
  pendingArchiveId.value = job.jobId
  archiveConfirmTimer = setTimeout(clearArchiveConfirmation, 4000)
}

defineProps({
  modelValue: { type: Boolean, required: true },
  deleteDialog: { type: Boolean, required: true },
  loading: { type: Boolean, default: false },
  tab: { type: String, default: 'current' },
  queue: { type: Object, required: true },
  jobs: { type: Array, default: () => [] },
  actionId: { type: String, default: '' },
  deleteTarget: { type: Object, default: null },
  currentJobId: { type: String, default: '' },
})

const emit = defineEmits([
  'update:modelValue', 'update:deleteDialog', 'update:tab', 'refresh', 'cancel',
  'move', 'archive', 'restore', 'delete-request', 'delete-confirm',
])

onBeforeUnmount(clearArchiveConfirmation)
</script>

<template>
  <v-dialog :model-value="modelValue" max-width="820" @update:model-value="$emit('update:modelValue', $event)">
    <v-card class="analysis-queue-card">
      <div class="draft-manager-header analysis-queue-header">
        <span class="draft-manager-header__icon analysis-queue-header__icon" aria-hidden="true">⇅</span>
        <div class="analysis-queue-header__copy"><v-card-title>解析队列</v-card-title><v-card-subtitle>服务器保留图纸与解析结果，完成后无需再次上传即可恢复核对。</v-card-subtitle></div>
        <v-spacer />
        <v-btn icon size="small" variant="text" :loading="loading" aria-label="刷新解析队列" @click="$emit('refresh')"><span aria-hidden="true">↻</span></v-btn>
      </div>
      <v-progress-linear v-if="loading" indeterminate color="secondary" />
      <v-tabs :model-value="tab" color="secondary" grow density="compact" @update:model-value="$emit('update:tab', $event)">
        <v-tab value="current">解析任务</v-tab>
        <v-tab value="archived">已归档（{{ queue.jobs.filter(job => job.isArchived).length }}）</v-tab>
      </v-tabs>
      <v-card-text class="analysis-queue-content">
        <div data-tour="analysis-queue-summary" class="analysis-queue-summary">
          <v-chip v-for="item in queueSummaryChips(queue)" :key="item.key" size="small" :color="item.color" variant="tonal">{{ item.label }}</v-chip>
        </div>
        <v-alert v-if="!loading && !jobs.length" type="info" variant="tonal" density="compact">{{ tab === 'archived' ? '当前没有已归档任务。' : '当前没有解析任务。' }}</v-alert>
        <v-list v-else data-tour="analysis-queue-list" class="analysis-queue-list" lines="three">
          <div v-for="job in jobs" :key="job.jobId" class="analysis-queue-item-shell" :class="{ 'analysis-queue-item-shell--expanded': expandedJobId === job.jobId }">
          <v-list-item class="analysis-queue-item" role="button" :aria-expanded="expandedJobId === job.jobId" @click="toggleExpanded(job)">
            <template #prepend><span class="analysis-queue-file-icon" aria-hidden="true">PDF</span></template>
            <v-list-item-title>{{ job.fileName }}</v-list-item-title>
            <v-list-item-subtitle>
              <div class="analysis-queue-item__meta"><span class="analysis-queue-start-time">{{ formatStartTime(job.createdAt) }}</span><span v-if="job.createdBy?.username" class="analysis-queue-start-time">创建人：{{ job.createdBy.username }}</span></div>
              <v-progress-linear v-if="job.queueState === 'running'" :model-value="progress(job)" color="accent" height="4" rounded class="mt-2" />
              <small v-if="job.queueState === 'running'" class="analysis-queue-progress-detail">总进度 {{ Math.round(progress(job)) }}% · 对照 {{ job.completedReferenceFiles || 0 }}/{{ job.totalReferenceFiles || 0 }} · 设计页 {{ job.completedPages || 0 }}/{{ job.totalPages || 0 }}</small>
            </v-list-item-subtitle>
            <template #append>
              <div class="analysis-queue-actions">
                <v-chip size="x-small" :color="statusColor(job)" variant="tonal">{{ statusLabel(job) }}</v-chip>
                <div v-if="job.canReorder" class="analysis-queue-order">
                  <v-btn icon size="x-small" variant="text" :disabled="job.queuePosition <= 1 || actionId === job.jobId" aria-label="提高优先级" @click.stop="$emit('move', job, 'up')">↑</v-btn>
                  <v-btn icon size="x-small" variant="text" :disabled="job.queuePosition >= queue.queuedCount || actionId === job.jobId" aria-label="降低优先级" @click.stop="$emit('move', job, 'down')">↓</v-btn>
                </div>
                <v-btn v-if="job.canCancel" size="small" color="error" variant="text" :loading="actionId === job.jobId" @click.stop="$emit('cancel', job)">取消</v-btn>
                <v-btn v-if="job.canRestore" icon size="small" color="secondary" variant="text" class="analysis-queue-icon-button" :loading="actionId === job.jobId" aria-label="恢复核对" @click.stop="$emit('restore', job)"><svg class="analysis-queue-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7H5v-4M5.4 7.1A8 8 0 1 1 4 14"/><path d="M5 7l3.2-3.2"/></svg><v-tooltip activator="parent" location="top">恢复核对</v-tooltip></v-btn>
                <v-btn v-if="job.canArchive && job.isArchived" icon size="small" color="secondary" variant="text" class="analysis-queue-icon-button" :loading="actionId === job.jobId" :aria-label="archiveActionLabel(job)" @click.stop="handleArchive(job)"><svg class="analysis-queue-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v13H4zM3 3h18v4H3zM9 11h6"/><path d="M12 17v-5m0 0-2 2m2-2 2 2"/></svg><v-tooltip activator="parent" location="top">{{ archiveActionLabel(job) }}</v-tooltip></v-btn>
                <v-menu v-else-if="job.canArchive" :model-value="pendingArchiveId === job.jobId" :open-on-click="false" :close-on-content-click="false" location="top" :offset="8">
                  <template #activator="{ props: archiveActivatorProps }">
                    <v-btn v-bind="archiveActivatorProps" icon size="small" variant="text" class="analysis-queue-icon-button" :class="{ 'analysis-queue-archive-button--confirming': pendingArchiveId === job.jobId }" :loading="actionId === job.jobId" :aria-label="pendingArchiveId === job.jobId ? '确认归档' : archiveActionLabel(job)" @click.stop="handleArchive(job)"><svg class="analysis-queue-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v13H4zM3 3h18v4H3zM9 11h6"/></svg><v-tooltip v-if="pendingArchiveId !== job.jobId" activator="parent" location="top">{{ archiveActionLabel(job) }}</v-tooltip></v-btn>
                  </template>
                  <div class="analysis-queue-archive-confirm" role="status">确定归档</div>
                </v-menu>
                <v-btn v-if="job.canDelete" icon size="small" color="error" variant="text" class="analysis-queue-icon-button" :disabled="currentJobId === job.jobId" :loading="actionId === job.jobId" aria-label="永久删除解析任务" @click.stop="$emit('delete-request', job)"><svg class="analysis-queue-action-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg><v-tooltip activator="parent" location="top">{{ currentJobId === job.jobId ? '当前正在核对此任务，请先切换或关闭当前工作区' : '永久删除任务文件与解析结果' }}</v-tooltip></v-btn>
              </div>
            </template>
          </v-list-item>
          <v-expand-transition>
            <div v-if="expandedJobId === job.jobId" class="analysis-queue-detail">
              <div class="analysis-queue-detail-grid">
                <div><span>所属项目</span><strong>{{ job.project?.name || '未指定' }}</strong></div>
                <div><span>图纸数</span><strong>{{ job.pageCount || job.totalPages || 0 }} 页</strong></div>
                <div><span>审核人员</span><strong>{{ (job.reviewers || []).length }} 人参与</strong><small>{{ reviewerNames(job) }} · 已审核 {{ job.reviewedPageCount || 0 }} 页</small></div>
              </div>
              <div class="analysis-queue-page-review-title">最近修改记录</div>
              <div v-if="job.recentReviewEvents?.length" class="analysis-queue-page-reviews">
                <div v-for="event in job.recentReviewEvents" :key="`${event.page}-${event.revision}-${event.savedAt}-${event.userId}`" class="analysis-queue-page-review">
                  <strong>P{{ event.page }}</strong>
                  <div class="analysis-queue-page-review__users"><span>{{ event.username }}</span></div>
                  <time>{{ formatStartTime(event.savedAt) }}</time>
                </div>
              </div>
              <div v-else class="analysis-queue-page-review-empty">暂无修改记录。</div>
            </div>
          </v-expand-transition>
          </div>
        </v-list>
      </v-card-text>
      <v-card-actions class="settings-actions"><v-spacer /><v-btn color="primary" @click="$emit('update:modelValue', false)">关闭</v-btn></v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog :model-value="deleteDialog" max-width="460" @update:model-value="$emit('update:deleteDialog', $event)">
    <v-card>
      <v-card-title>确认删除解析任务</v-card-title>
      <v-card-text>将永久删除“{{ deleteTarget?.fileName || '未命名图纸' }}”的原始文件、对照文件和解析结果，此操作无法撤销。若只是希望减少任务列表冗余，请使用“归档”。</v-card-text>
      <v-card-actions><v-spacer /><v-btn variant="text" @click="$emit('update:deleteDialog', false)">取消</v-btn><v-btn color="error" :loading="actionId === deleteTarget?.jobId" @click="$emit('delete-confirm')">永久删除</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
