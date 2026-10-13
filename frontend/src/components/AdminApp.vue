<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity, AlertTriangle, BarChart3, CheckCircle2, Database, Download, FileCheck2, Files, KeyRound,
  LayoutDashboard, RefreshCw, ScrollText, Server, Settings, ShieldCheck, Users,
} from '@lucide/vue'

import { api } from '../api.js'
import { nextEffectRankingMode } from '../adminEffectRanking.js'
import { beijingDate, formatBeijingTime } from '../beijingTime.js'
import { auditEventsCsv } from '../adminAuditExport.js'
import { businessStatsCsv } from '../adminBusinessExport.js'
import { appHeaderHeightForViewport } from '../appHeaderLayout.js'
import { useAuthSession } from '../composables/useAuthSession.js'
import { useThemeSettings } from '../composables/useThemeSettings.js'
import AdminSettingsDialog from './AdminSettingsDialog.vue'
import AppBrandIcon from './AppBrandIcon.vue'
import ConfirmedLogoutButton from './ConfirmedLogoutButton.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthSession({ client: api })
const { preference: themePreference, options: themeOptions } = useThemeSettings()
const settingsDialog = ref(false)
const adminViewportHeight = ref(window.innerHeight)
const adminHeaderHeight = computed(() => appHeaderHeightForViewport(adminViewportHeight.value))
const EFFECT_RANKING_KEY = 'weld-marker.admin-effect-ranking'
const effectRankingMode = ref(localStorage.getItem(EFFECT_RANKING_KEY) === 'drawing' ? 'drawing' : 'project')
const tooltipsEnabled = ref(localStorage.getItem('weld-marker.tooltips-enabled') === 'true')
const overview = ref(null)
const balance = ref(null)
const business = ref(null)
const users = ref([])
const pagination = ref({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const search = ref('')
const sessions = ref([])
const sessionPagination = ref({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const sessionSearch = ref('')
const sessionOnlineCount = ref(0)
const jobs = ref([])
const jobPagination = ref({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const jobSearch = ref('')
const jobStatus = ref('')
const auditEvents = ref([])
const auditPagination = ref({ page: 1, pageSize: 50, total: 0, totalPages: 1 })
const auditOptions = ref({ users: [], actions: [], sources: [] })
const auditFilters = ref({ from: localDate(-29), to: localDate(), username: '', action: '', source: '', jobId: '', search: '' })
const selectedAudit = ref(null)
const exportingAudit = ref(false)
const loading = ref(false)
const error = ref('')
let sessionRefreshTimer = 0

function localDate(daysFromToday = 0) {
  return beijingDate(daysFromToday)
}

const businessFilters = ref({ from: localDate(-29), to: localDate(), project: '', username: '', action: '' })

const section = computed(() => ({
  'admin-users': 'users',
  'admin-sessions': 'sessions',
  'admin-jobs': 'jobs',
  'admin-audit': 'audit',
  'admin-database': 'database',
}[route.name] || 'dashboard'))
const pageTitle = computed(() => ({
  dashboard: '运行仪表盘', users: '用户表', sessions: '登录会话', jobs: '任务表', audit: '审计日志', database: '数据库信息',
}[section.value]))
const navItems = [
  { title: '仪表盘', to: '/admin', icon: LayoutDashboard },
  { title: '用户表', to: '/admin/users', icon: Users },
  { title: '登录会话', to: '/admin/sessions', icon: Activity },
  { title: '任务表', to: '/admin/jobs', icon: Files },
  { title: '审计日志', to: '/admin/audit', icon: ScrollText },
  { title: '数据库', to: '/admin/database', icon: Database },
]
const jobStatusOptions = [
  { title: '全部状态', value: '' },
  { title: '等待中', value: 'queued' },
  { title: '已领取', value: 'leased' },
  { title: '解析中', value: 'processing' },
  { title: '正在取消', value: 'cancelling' },
  { title: '已完成', value: 'complete' },
  { title: '失败', value: 'failed' },
  { title: '已取消', value: 'cancelled' },
]
const metrics = computed(() => overview.value?.metrics || {})
const businessSummary = computed(() => business.value?.summary || {})
const businessOptions = computed(() => business.value?.filterOptions || { projects: [], users: [], actions: [] })
const businessActionOptions = computed(() => businessOptions.value.actions.map(value => ({ title: actionLabel(value), value })))
const effectRankingRows = computed(() => effectRankingMode.value === 'drawing' ? (business.value?.drawings || []) : (business.value?.projects || []))
const trendMax = computed(() => Math.max(1, ...(business.value?.trends || []).map(item => Number(item.processedPages || 0))))
const balanceLines = computed(() => Array.isArray(balance.value?.balances) ? balance.value.balances : [])
const balanceMessage = computed(() => {
  if (balance.value?.status === 'loading') return '正在读取余额'
  if (balance.value?.status === 'unconfigured') return 'AI 服务尚未配置'
  if (balance.value?.status === 'unsupported') return '当前兼容服务未提供统一余额接口'
  if (balance.value?.reason === 'authentication') return '密钥鉴权失败，无法读取余额'
  if (balance.value?.reason === 'network') return '无法连接服务商余额接口'
  if (balance.value?.reason === 'invalid-response') return '服务商返回了无法识别的余额数据'
  return '暂时无法读取余额'
})

function formatBytes(value) {
  const bytes = Number(value) || 0
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

function displayTime(value) {
  return formatBeijingTime(value)
}

function formatRate(value) {
  return Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(1)}%` : '—'
}

function toggleEffectRanking() {
  effectRankingMode.value = nextEffectRankingMode(effectRankingMode.value)
}

function actionLabel(value) {
  const labels = {
    'auth.registered': '注册账号',
    'auth.login': '登录系统',
    'auth.login_failed': '登录失败',
    'auth.logout': '退出系统',
    'page.saved': '保存核对页面',
    'marker.manual_added': '人工新增标识',
    'marker.content_corrected': '内容修正',
    'marker.layout_adjusted': '布局调整',
    'marker.deleted': '删除标识',
    'pdf.exported': '导出 PDF',
    'tutorial.exported': '导出教程 PDF',
    'job.queued': '任务进入队列',
    'job.claimed': 'Worker 领取任务',
    'job.complete': '任务完成',
    'job.failed': '任务失败',
    'job.cancel-requested': '请求取消任务',
    'job.cancelled': '任务已取消',
    'job.queue-moved': '调整队列顺序',
    'task.created': '创建任务',
    'task.cancel_requested': '用户请求取消任务',
    'task.queue_moved': '用户调整队列',
    'task.archived': '归档任务',
    'task.unarchived': '取消归档任务',
    'task.deleted': '删除任务',
  }
  return labels[value] || value
}

function auditSourceLabel(value) {
  return ({ server: '服务端', client: '客户端', job: '任务队列', review: '页面审核', business: '业务操作' })[value] || value
}

function auditDetails(value) {
  return JSON.stringify(value || {}, null, 2)
}

function exportBusinessStats() {
  const content = businessStatsCsv(business.value?.projects || [], formatRate)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }))
  link.download = `业务统计-${businessFilters.value.from || '全部'}-${businessFilters.value.to || '全部'}.csv`
  link.click()
  URL.revokeObjectURL(link.href)
}

function jobStatusLabel(status) {
  return ({ queued: '等待中', leased: '已领取', processing: '解析中', cancelling: '正在取消', complete: '已完成', failed: '失败', cancelled: '已取消' })[status] || status || '未知'
}

function jobStatusClass(status) {
  if (status === 'complete') return 'state-mark'
  if (status === 'failed' || status === 'cancelled') return 'state-mark state-mark--off'
  return 'state-mark state-mark--active'
}

async function loadOverview({ includeBalance = false } = {}) {
  const overviewRequest = api.getAdminOverview()
  if (includeBalance) {
    balance.value = { status: 'loading', supported: true, balances: [] }
    void api.getAdminAiBalance().then(
      response => { balance.value = response },
      () => { balance.value = { status: 'unavailable', supported: true, balances: [] } },
    )
  }
  overview.value = await overviewRequest
}

async function loadBusinessStats() {
  business.value = await api.getAdminBusinessStats(businessFilters.value)
}

async function loadUsers(page = pagination.value.page) {
  const response = await api.getAdminUsers({ search: search.value.trim(), page, pageSize: pagination.value.pageSize })
  users.value = response.users || []
  pagination.value = response.pagination || pagination.value
}

async function loadSessions(page = sessionPagination.value.page) {
  const response = await api.getAdminSessions({
    search: sessionSearch.value.trim(), page, pageSize: sessionPagination.value.pageSize,
  })
  sessions.value = response.sessions || []
  sessionPagination.value = response.pagination || sessionPagination.value
  sessionOnlineCount.value = Number(response.onlineCount) || 0
}

async function loadJobs(page = jobPagination.value.page) {
  const response = await api.getAdminJobs({
    search: jobSearch.value.trim(), status: jobStatus.value, page, pageSize: jobPagination.value.pageSize,
  })
  jobs.value = response.jobs || []
  jobPagination.value = response.pagination || jobPagination.value
}

async function loadAuditEvents(page = auditPagination.value.page) {
  const response = await api.getAdminAuditEvents({
    ...auditFilters.value, page, pageSize: auditPagination.value.pageSize,
  })
  auditEvents.value = response.events || []
  auditPagination.value = response.pagination || auditPagination.value
  auditOptions.value = response.filterOptions || auditOptions.value
}

async function refresh() {
  if (auth.user.value?.isAdmin !== true) return
  loading.value = true
  error.value = ''
  try {
    if (section.value === 'users') await Promise.all([loadOverview(), loadUsers()])
    else if (section.value === 'sessions') await Promise.all([loadOverview(), loadSessions()])
    else if (section.value === 'jobs') await Promise.all([loadOverview(), loadJobs()])
    else if (section.value === 'audit') await loadAuditEvents()
    else if (section.value === 'dashboard') await Promise.all([loadOverview({ includeBalance: true }), loadBusinessStats()])
    else await loadOverview()
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    loading.value = false
  }
}

async function submitBusinessFilters() {
  loading.value = true
  error.value = ''
  try {
    await loadBusinessStats()
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    loading.value = false
  }
}

async function resetBusinessFilters() {
  businessFilters.value = { from: localDate(-29), to: localDate(), project: '', username: '', action: '' }
  await submitBusinessFilters()
}

async function submitSearch() {
  pagination.value.page = 1
  await loadUsers(1)
}

async function submitSessionSearch() {
  sessionPagination.value.page = 1
  loading.value = true
  error.value = ''
  try {
    await loadSessions(1)
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    loading.value = false
  }
}

async function submitJobSearch() {
  jobPagination.value.page = 1
  loading.value = true
  error.value = ''
  try {
    await loadJobs(1)
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    loading.value = false
  }
}

async function submitAuditSearch() {
  auditPagination.value.page = 1
  loading.value = true
  error.value = ''
  try {
    await loadAuditEvents(1)
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    loading.value = false
  }
}

async function resetAuditFilters() {
  auditFilters.value = { from: localDate(-29), to: localDate(), username: '', action: '', source: '', jobId: '', search: '' }
  await submitAuditSearch()
}

async function exportAuditEvents() {
  exportingAudit.value = true
  error.value = ''
  try {
    const first = await api.getAdminAuditEvents({ ...auditFilters.value, page: 1, pageSize: 200 })
    const events = [...(first.events || [])]
    const totalPages = Number(first.pagination?.totalPages) || 1
    if (totalPages > 100) throw new Error('审计记录超过 20000 条，请缩小筛选范围后再导出')
    for (let page = 2; page <= totalPages; page += 1) {
      const response = await api.getAdminAuditEvents({ ...auditFilters.value, page, pageSize: 200 })
      events.push(...(response.events || []))
    }
    const content = auditEventsCsv(events, { actionLabel, sourceLabel: auditSourceLabel })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }))
    link.download = `审计日志-${auditFilters.value.from || '全部'}-${auditFilters.value.to || '全部'}.csv`
    link.click()
    URL.revokeObjectURL(link.href)
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    exportingAudit.value = false
  }
}

async function logout() {
  await auth.logout()
  await router.replace('/login')
}

function authenticationRequired() {
  auth.user.value = null
  auth.status.value = 'anonymous'
}

function redirectToLogin() {
  return router.replace({ path: '/login', query: { redirect: route.fullPath } })
}

watch([section, () => auth.status.value], ([, status]) => {
  if (status === 'authenticated' && auth.user.value?.isAdmin) void refresh()
  else if (status === 'anonymous') void redirectToLogin()
})
watch(tooltipsEnabled, value => {
  localStorage.setItem('weld-marker.tooltips-enabled', String(value))
  document.body.classList.toggle('tooltips-disabled', !value)
}, { immediate: true })
watch(effectRankingMode, value => localStorage.setItem(EFFECT_RANKING_KEY, value))

function updateAdminViewport() {
  adminViewportHeight.value = window.innerHeight
}

onMounted(async () => {
  window.addEventListener('drawing-marker:authentication-required', authenticationRequired)
  window.addEventListener('resize', updateAdminViewport)
  sessionRefreshTimer = window.setInterval(() => {
    if (section.value === 'sessions' && auth.status.value === 'authenticated' && !loading.value) void loadSessions()
  }, 30_000)
  await auth.restore()
})

onBeforeUnmount(() => {
  window.removeEventListener('drawing-marker:authentication-required', authenticationRequired)
  window.removeEventListener('resize', updateAdminViewport)
  window.clearInterval(sessionRefreshTimer)
})
</script>

<template>
  <v-app class="admin-app">
    <div v-if="auth.status.value === 'loading'" class="admin-centered"><v-progress-circular indeterminate color="primary" /><span>正在检查管理员身份</span></div>
    <div v-else-if="auth.status.value !== 'authenticated'" class="admin-centered"><v-progress-circular indeterminate color="primary" /><span>正在前往登录页</span></div>
    <div v-else-if="!auth.user.value?.isAdmin" class="admin-centered admin-forbidden">
      <ShieldCheck :size="42" />
      <h1>无后台访问权限</h1>
      <p>当前账号不是管理员。</p>
      <v-btn to="/main" color="primary" prepend-icon="">返回工作台</v-btn>
    </div>
    <template v-else>
      <v-app-bar color="header" elevation="0" :height="adminHeaderHeight" class="admin-topbar">
        <div class="admin-brand"><span class="admin-brand__icon" aria-hidden="true"><AppBrandIcon /></span><div><strong>系统后台</strong><small>图纸标识识别系统</small></div></div>
        <v-spacer />
        <v-btn icon size="small" class="admin-settings-button header-icon-button" :class="{ 'admin-topbar-button--active': settingsDialog }" aria-label="系统设置" @click="settingsDialog = true"><Settings :size="19" /><v-tooltip activator="parent" location="bottom">系统设置</v-tooltip></v-btn>
        <v-menu location="bottom end" :close-on-content-click="false">
          <template #activator="{ props }">
            <v-btn v-bind="props" class="admin-account-button" variant="text" aria-label="打开用户菜单">
              <span class="admin-account-button__icon user-menu-button" aria-hidden="true">
                <svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5" /><path d="M5.5 19c.7-3.5 3-5.3 6.5-5.3s5.8 1.8 6.5 5.3" /><circle cx="12" cy="12" r="9" /></svg>
              </span>
              <span class="admin-account-button__name">{{ auth.user.value.username }}</span>
              <v-tooltip activator="parent" location="bottom">用户中心</v-tooltip>
            </v-btn>
          </template>
          <v-card class="user-menu-card" min-width="310">
            <div class="user-menu-card__header">
              <span class="user-menu-avatar" aria-hidden="true">{{ String(auth.user.value.username || 'U').slice(0, 1).toUpperCase() }}</span>
              <div><small>当前登录用户</small><strong>{{ auth.user.value.username }}</strong></div>
            </div>
            <div class="user-menu-details">
              <div><span>用户名</span><strong>{{ auth.user.value.username }}</strong></div>
              <div><span>创建时间</span><strong>{{ displayTime(auth.user.value.createdAt) }}</strong></div>
            </div>
            <v-divider />
            <v-card-actions class="user-menu-actions">
              <v-btn block to="/main" color="primary" variant="tonal">返回工作台</v-btn>
              <ConfirmedLogoutButton @confirm="logout" />
            </v-card-actions>
          </v-card>
        </v-menu>
      </v-app-bar>

      <v-navigation-drawer permanent width="220" class="admin-nav">
        <v-list nav density="compact">
          <v-list-item v-for="item in navItems" :key="item.to" :to="item.to" :title="item.title" color="primary" rounded="0">
            <template #prepend><component :is="item.icon" :size="18" /></template>
          </v-list-item>
        </v-list>
        <template #append><div class="admin-nav__foot"><span>SQLite WAL</span><strong>{{ overview?.database?.journalMode || '—' }}</strong></div></template>
      </v-navigation-drawer>

      <AdminSettingsDialog v-model="settingsDialog" v-model:theme-preference="themePreference" v-model:tooltips="tooltipsEnabled" :theme-options="themeOptions" />

      <v-main class="admin-main">
        <div class="admin-page">
          <header class="admin-page-header">
            <div><span>ADMINISTRATION</span><h1>{{ pageTitle }}</h1></div>
            <v-btn icon variant="text" class="admin-refresh-button" :class="{ 'admin-refresh-button--active': loading }" :loading="loading" aria-label="刷新数据" @click="refresh"><RefreshCw :size="19" /><v-tooltip activator="parent">刷新数据</v-tooltip></v-btn>
          </header>

          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-4">{{ error }}</v-alert>

          <template v-if="section === 'dashboard'">
            <form class="business-filters" @submit.prevent="submitBusinessFilters">
              <v-text-field v-model="businessFilters.from" type="date" label="开始日期" hide-details />
              <v-text-field v-model="businessFilters.to" type="date" label="结束日期" hide-details />
              <v-select v-model="businessFilters.project" :items="businessOptions.projects" label="项目" clearable hide-details />
              <v-select v-model="businessFilters.username" :items="businessOptions.users" label="用户" clearable hide-details />
              <v-select v-model="businessFilters.action" :items="businessActionOptions" label="人工操作类型" clearable hide-details />
              <v-btn type="submit" color="primary" :loading="loading">查询</v-btn>
              <v-btn variant="text" :disabled="loading" @click="resetBusinessFilters">重置</v-btn>
              <v-btn variant="tonal" :disabled="!business?.projects?.length" @click="exportBusinessStats"><Download :size="16" />导出</v-btn>
            </form>

            <section class="metric-grid metric-grid--business" aria-label="业务指标">
              <v-card class="metric-card" variant="outlined"><Files :size="19" /><span>处理图纸</span><strong>{{ businessSummary.processedDocuments ?? '—' }}</strong><small>{{ businessSummary.processedPages ?? 0 }} 个已生成页面</small></v-card>
              <v-card class="metric-card" variant="outlined"><FileCheck2 :size="19" /><span>页面核对率</span><strong>{{ formatRate(businessSummary.reviewCompletionRate) }}</strong><small>{{ businessSummary.reviewedPages ?? 0 }}/{{ businessSummary.processedPages ?? 0 }} 页已核对</small></v-card>
              <v-card class="metric-card" variant="outlined"><BarChart3 :size="19" /><span>自动标识占比</span><strong>{{ formatRate(businessSummary.automaticShare) }}</strong><small>{{ businessSummary.automaticMarkers ?? 0 }} 个自动标识保留在结果中</small></v-card>
              <v-card class="metric-card" variant="outlined"><Activity :size="19" /><span>人工干预</span><strong>{{ businessSummary.manualAdditions ?? 0 }}</strong><small>修正 {{ businessSummary.contentCorrections ?? 0 }} · 删除 {{ businessSummary.deletedMarkers ?? 0 }}</small></v-card>
              <v-card class="metric-card" variant="outlined"><CheckCircle2 :size="19" /><span>任务成功率</span><strong>{{ formatRate(businessSummary.successRate) }}</strong><small>{{ businessSummary.completedJobs ?? 0 }}/{{ businessSummary.totalJobs ?? 0 }} 个任务完成</small></v-card>
              <v-card class="metric-card metric-card--warning" variant="outlined"><AlertTriangle :size="19" /><span>失败任务</span><strong>{{ businessSummary.failedJobs ?? 0 }}</strong><small>{{ businessSummary.activeUsers ?? 0 }} 个活跃用户 · {{ businessSummary.exports ?? 0 }} 次导出</small></v-card>
            </section>

            <section class="admin-panel dashboard-grid">
              <div class="dashboard-card">
                <div class="admin-panel__heading"><div><BarChart3 :size="20" /><h2>处理趋势</h2></div><span>按日</span></div>
                <div v-if="business?.trends?.length" class="trend-chart">
                  <div v-for="item in business.trends" :key="item.date" class="trend-column" :title="`${item.date}：${item.processedPages} 页`">
                    <span>{{ item.processedPages }}</span>
                    <div :style="{ height: item.processedPages ? `${Math.max(3, item.processedPages / trendMax * 100)}%` : '0' }"></div>
                    <small>{{ item.date.slice(5) }}</small>
                  </div>
                </div>
                <div v-else class="admin-empty">当前范围暂无趋势数据</div>
              </div>
              <div class="dashboard-card">
                <div class="admin-panel__heading"><div><Activity :size="20" /><h2>人工操作</h2></div><span>当前筛选范围</span></div>
                <div class="operation-summary">
                  <div><span>人工新增</span><strong>{{ businessSummary.manualAdditions ?? 0 }}</strong></div>
                  <div><span>内容修正</span><strong>{{ businessSummary.contentCorrections ?? 0 }}</strong></div>
                  <div><span>布局调整</span><strong>{{ businessSummary.layoutAdjustments ?? 0 }}</strong></div>
                  <div><span>删除标识</span><strong>{{ businessSummary.deletedMarkers ?? 0 }}</strong></div>
                </div>
              </div>
            </section>

            <section class="admin-panel admin-table-panel">
              <div class="admin-panel__heading"><div><Files :size="20" /><h2>{{ effectRankingMode === 'drawing' ? '图纸效果排行' : '项目效果排行' }}</h2></div><div class="effect-ranking-controls"><v-btn icon size="small" variant="text" class="effect-ranking-switch" :aria-label="effectRankingMode === 'project' ? '切换至图纸效果排行' : '切换至项目效果排行'" @click="toggleEffectRanking"><component :is="effectRankingMode === 'project' ? Files : LayoutDashboard" :size="18" /><v-tooltip activator="parent" location="bottom">{{ effectRankingMode === 'project' ? '切换至图纸效果排行' : '切换至项目效果排行' }}</v-tooltip></v-btn></div></div>
              <v-table density="comfortable" class="admin-table business-table">
                <thead v-if="effectRankingMode === 'project'"><tr><th>项目</th><th>任务</th><th>图纸 / 页</th><th>核对率</th><th>自动 / 人工标识</th><th>人工新增</th><th>修正 / 删除</th><th>成功率</th></tr></thead>
                <thead v-else><tr><th>图纸</th><th>项目</th><th>页数</th><th>核对率</th><th>自动 / 人工标识</th><th>自动标识占比</th><th>人工新增</th><th>修正 / 删除</th></tr></thead>
                <tbody v-if="effectRankingMode === 'project'">
                  <tr v-for="item in effectRankingRows" :key="item.project"><td><strong>{{ item.project }}</strong></td><td>{{ item.jobs }}</td><td>{{ item.documents }} / {{ item.pages }}</td><td>{{ formatRate(item.reviewRate) }}</td><td>{{ item.automaticMarkers }} / {{ item.manualMarkers }}</td><td>{{ item.manualAdditions }}</td><td>{{ item.contentCorrections }} / {{ item.deletions }}</td><td>{{ formatRate(item.successRate) }}</td></tr>
                  <tr v-if="!effectRankingRows.length"><td colspan="8" class="admin-empty">当前范围暂无项目数据</td></tr>
                </tbody>
                <tbody v-else>
                  <tr v-for="item in effectRankingRows" :key="item.jobId"><td><strong>{{ item.targetName }}</strong><small>{{ item.jobId }}</small></td><td>{{ item.project }}</td><td>{{ item.pages }}</td><td>{{ formatRate(item.reviewRate) }}</td><td>{{ item.automaticMarkers }} / {{ item.manualMarkers }}</td><td>{{ formatRate(item.automaticShare) }}</td><td>{{ item.manualAdditions }}</td><td>{{ item.contentCorrections }} / {{ item.deletions }}</td></tr>
                  <tr v-if="!effectRankingRows.length"><td colspan="8" class="admin-empty">当前范围暂无图纸数据</td></tr>
                </tbody>
              </v-table>
            </section>

            <section class="admin-panel admin-table-panel">
              <div class="admin-panel__heading"><div><Users :size="20" /><h2>用户工作量</h2></div><span>新审计数据已绑定登录用户</span></div>
              <v-table density="comfortable" class="admin-table business-table">
                <thead><tr><th>用户</th><th>任务</th><th>处理 / 核对页</th><th>人工新增</th><th>内容修正</th><th>布局调整</th><th>删除</th><th>导出</th></tr></thead>
                <tbody>
                  <tr v-for="item in business?.users || []" :key="item.username"><td><strong>{{ item.username }}</strong></td><td>{{ item.jobs }}</td><td>{{ item.pages }} / {{ item.reviewedPages }}</td><td>{{ item.manualAdditions }}</td><td>{{ item.contentCorrections }}</td><td>{{ item.layoutAdjustments }}</td><td>{{ item.deletions }}</td><td>{{ item.exports }}</td></tr>
                  <tr v-if="!business?.users?.length"><td colspan="8" class="admin-empty">当前范围暂无用户工作量数据</td></tr>
                </tbody>
              </v-table>
            </section>

            <section v-if="business?.failures?.length" class="admin-panel admin-table-panel failure-panel">
              <div class="admin-panel__heading"><div><AlertTriangle :size="20" /><h2>最近失败任务</h2></div><span>最多显示 50 条</span></div>
              <v-table density="comfortable" class="admin-table"><thead><tr><th>更新时间</th><th>项目</th><th>图纸</th><th>创建人</th><th>错误</th></tr></thead><tbody><tr v-for="item in business.failures" :key="item.jobId"><td>{{ displayTime(item.updatedAt) }}</td><td>{{ item.project }}</td><td><strong>{{ item.targetName }}</strong><small>{{ item.jobId }}</small></td><td>{{ item.username }}</td><td class="failure-message">{{ item.error }}</td></tr></tbody></v-table>
            </section>

            <section class="admin-panel">
              <div class="admin-panel__heading"><div><Server :size="20" /><h2>系统运行</h2></div><span>实时快照</span></div>
              <div class="status-strip operational-strip">
                <div><span>活跃会话</span><strong>{{ metrics.activeSessions ?? 0 }}</strong></div>
                <div><span>运行任务</span><strong>{{ metrics.runningJobs ?? 0 }}</strong></div>
                <div><span>等待任务</span><strong>{{ metrics.queuedJobs ?? 0 }}</strong></div>
                <div><span>Worker</span><strong>{{ overview?.workers?.count ?? 0 }}</strong></div>
                <div><span>数据库</span><strong>{{ formatBytes(overview?.database?.sizeBytes) }}</strong></div>
              </div>
            </section>

            <section class="admin-panel balance-panel">
              <div class="admin-panel__heading"><div><KeyRound :size="20" /><h2>AI 密钥余额</h2></div><span>{{ balance?.provider === 'deepseek' ? 'DeepSeek' : '兼容服务' }}</span></div>
              <div v-if="balance?.status === 'available'" class="balance-values">
                <div v-for="item in balanceLines" :key="item.currency"><span>{{ item.currency }} 可用余额</span><strong>{{ item.totalBalance }}</strong><small>充值 {{ item.toppedUpBalance }} · 赠金 {{ item.grantedBalance }}</small></div>
                <div v-if="!balanceLines.length"><span>账户状态</span><strong>{{ balance.isAvailable ? '可用' : '余额不足' }}</strong></div>
              </div>
              <div v-else class="admin-empty">
                {{ balanceMessage }}
              </div>
            </section>

          </template>

          <section v-else-if="section === 'users'" class="admin-panel admin-table-panel">
            <div class="admin-panel__heading"><div><Users :size="20" /><h2>用户记录</h2></div><span>共 {{ pagination.total }} 人</span></div>
            <form class="admin-filter" @submit.prevent="submitSearch"><v-text-field v-model="search" label="搜索用户名" clearable hide-details /><v-btn type="submit" color="primary" :loading="loading">查询</v-btn></form>
            <v-table density="comfortable" class="admin-table">
              <thead><tr><th>用户</th><th>权限</th><th>状态</th><th>有效登录会话</th><th>创建时间</th><th>最后登录</th></tr></thead>
              <tbody><tr v-for="item in users" :key="item.userId"><td><strong>{{ item.username }}</strong><small>{{ item.userId }}</small></td><td>{{ item.isAdmin ? '管理员' : '普通用户' }}</td><td><span :class="['state-mark', item.disabled ? 'state-mark--off' : '']">{{ item.disabled ? '已停用' : '正常' }}</span></td><td>{{ item.activeSessions }}</td><td>{{ displayTime(item.createdAt) }}</td><td>{{ displayTime(item.lastLoginAt) }}</td></tr><tr v-if="!users.length"><td colspan="6" class="admin-empty">没有匹配的用户</td></tr></tbody>
            </v-table>
            <v-pagination v-if="pagination.totalPages > 1" v-model="pagination.page" :length="pagination.totalPages" density="compact" @update:model-value="loadUsers" />
          </section>

          <section v-else-if="section === 'sessions'" class="admin-panel admin-table-panel">
            <div class="admin-panel__heading"><div><Activity :size="20" /><h2>当前登录会话</h2></div><span>在线 {{ sessionOnlineCount }} 人 · 有效会话 {{ sessionPagination.total }} 个</span></div>
            <form class="admin-filter" @submit.prevent="submitSessionSearch"><v-text-field v-model="sessionSearch" label="搜索在线用户名" clearable hide-details /><v-btn type="submit" color="primary" :loading="loading">查询</v-btn></form>
            <v-table density="comfortable" class="admin-table">
              <thead><tr><th>用户</th><th>在线状态</th><th>权限</th><th>登录时间</th><th>最近活动</th><th>会话到期</th></tr></thead>
              <tbody>
                <tr v-for="item in sessions" :key="item.userId">
                  <td><strong>{{ item.username }}</strong><small>{{ item.userId }}</small></td>
                  <td><v-chip size="x-small" :color="item.disabled ? 'error' : item.online ? 'success' : 'secondary'" variant="tonal">{{ item.disabled ? '账号已停用' : item.online ? '在线' : '暂离' }}</v-chip></td>
                  <td>{{ item.isAdmin ? '管理员' : '普通用户' }}</td>
                  <td>{{ displayTime(item.createdAt) }}</td>
                  <td>{{ displayTime(item.lastSeenAt) }}</td>
                  <td>{{ displayTime(item.expiresAt) }}</td>
                </tr>
                <tr v-if="!sessions.length"><td colspan="6" class="admin-empty">当前没有匹配的有效登录会话</td></tr>
              </tbody>
            </v-table>
            <v-pagination v-if="sessionPagination.totalPages > 1" v-model="sessionPagination.page" :length="sessionPagination.totalPages" density="compact" @update:model-value="loadSessions" />
          </section>

          <section v-else-if="section === 'jobs'" class="admin-panel admin-table-panel">
            <div class="admin-panel__heading"><div><Files :size="20" /><h2>任务记录</h2></div><span>共 {{ jobPagination.total }} 个任务</span></div>
            <form class="admin-filter admin-filter--jobs" @submit.prevent="submitJobSearch">
              <v-text-field v-model="jobSearch" label="搜索文件名、任务编号、创建人或批次" clearable hide-details />
              <v-select v-model="jobStatus" :items="jobStatusOptions" label="任务状态" hide-details />
              <v-btn type="submit" color="primary" :loading="loading">查询</v-btn>
            </form>
            <v-table density="comfortable" class="admin-table admin-jobs-table">
              <thead><tr><th>任务 / 文件</th><th>状态</th><th>进度</th><th>页面审核</th><th>创建人</th><th>项目</th><th>创建时间</th><th>更新时间</th></tr></thead>
              <tbody>
                <tr v-for="item in jobs" :key="item.jobId">
                  <td><strong>{{ item.targetName || '未命名图纸' }}</strong><small>{{ item.jobId }}</small></td>
                  <td><span :class="jobStatusClass(item.status)">{{ jobStatusLabel(item.status) }}</span><small v-if="item.archivedAt">已归档</small></td>
                  <td><strong>{{ item.progressPercent }}%</strong><small>{{ item.completedPages }}/{{ item.totalPages }} 页</small></td>
                  <td>{{ item.reviewedPageCount }}/{{ item.pageCount }}</td>
                  <td>{{ item.creatorUsername || '—' }}</td>
                  <td>{{ item.projectName || '—' }}</td>
                  <td>{{ displayTime(item.createdAt) }}</td>
                  <td>{{ displayTime(item.updatedAt) }}</td>
                </tr>
                <tr v-if="!jobs.length"><td colspan="8" class="admin-empty">没有匹配的任务</td></tr>
              </tbody>
            </v-table>
            <v-pagination v-if="jobPagination.totalPages > 1" v-model="jobPagination.page" :length="jobPagination.totalPages" density="compact" @update:model-value="loadJobs" />
          </section>

          <section v-else-if="section === 'audit'" class="admin-panel admin-table-panel">
            <div class="admin-panel__heading">
              <div><ScrollText :size="20" /><h2>统一审计时间线</h2></div>
              <span>共 {{ auditPagination.total }} 条事件</span>
            </div>
            <form class="audit-filters" @submit.prevent="submitAuditSearch">
              <v-text-field v-model="auditFilters.from" type="date" label="开始日期" hide-details />
              <v-text-field v-model="auditFilters.to" type="date" label="结束日期" hide-details />
              <v-select v-model="auditFilters.username" :items="auditOptions.users" label="用户" clearable hide-details />
              <v-select v-model="auditFilters.source" :items="auditOptions.sources.map(value => ({ title: auditSourceLabel(value), value }))" label="来源" clearable hide-details />
              <v-select v-model="auditFilters.action" :items="auditOptions.actions.map(value => ({ title: actionLabel(value), value }))" label="操作类型" clearable hide-details />
              <v-text-field v-model="auditFilters.jobId" label="任务编号" clearable hide-details />
              <v-text-field v-model="auditFilters.search" label="搜索图纸、详情或操作" clearable hide-details class="audit-search" />
              <div class="audit-filter-actions">
                <v-btn type="submit" color="primary" :loading="loading">查询</v-btn>
                <v-btn variant="text" :disabled="loading" @click="resetAuditFilters">重置</v-btn>
                <v-btn variant="tonal" :loading="exportingAudit" :disabled="!auditPagination.total" @click="exportAuditEvents"><Download :size="16" />导出 CSV</v-btn>
              </div>
            </form>
            <v-table density="comfortable" class="admin-table audit-table">
              <thead><tr><th>时间</th><th>来源</th><th>操作</th><th>用户</th><th>任务 / 图纸</th><th>页面</th><th>详情</th></tr></thead>
              <tbody>
                <tr v-for="item in auditEvents" :key="item.id">
                  <td>{{ displayTime(item.timestamp) }}</td>
                  <td><span class="audit-source">{{ auditSourceLabel(item.source) }}</span></td>
                  <td><strong>{{ actionLabel(item.action) }}</strong><small>{{ item.action }}</small></td>
                  <td>{{ item.username || '系统' }}</td>
                  <td><strong>{{ item.targetName || item.jobId || '—' }}</strong><small v-if="item.targetName && item.jobId">{{ item.jobId }}</small></td>
                  <td>{{ item.pageNumber ?? '—' }}</td>
                  <td><v-btn size="small" variant="text" @click="selectedAudit = item">查看</v-btn></td>
                </tr>
                <tr v-if="!auditEvents.length"><td colspan="7" class="admin-empty">当前筛选范围暂无审计事件</td></tr>
              </tbody>
            </v-table>
            <v-pagination v-if="auditPagination.totalPages > 1" v-model="auditPagination.page" :length="auditPagination.totalPages" density="compact" @update:model-value="loadAuditEvents" />
          </section>

          <section v-else class="admin-panel admin-table-panel">
            <div class="admin-panel__heading"><div><Database :size="20" /><h2>SQLite 数据表</h2></div><span>{{ formatBytes(overview?.database?.sizeBytes) }}</span></div>
            <div class="database-meta"><span>存储引擎 <strong>{{ overview?.database?.engine || '—' }}</strong></span><span>日志模式 <strong>{{ overview?.database?.journalMode || '—' }}</strong></span><span>表数量 <strong>{{ overview?.database?.tableCount ?? '—' }}</strong></span></div>
            <v-table density="comfortable" class="admin-table"><thead><tr><th>业务表</th><th>数据库表名</th><th class="text-right">记录数</th></tr></thead><tbody><tr v-for="table in overview?.database?.tables || []" :key="table.name"><td><strong>{{ table.label }}</strong></td><td><code>{{ table.name }}</code></td><td class="text-right">{{ table.rowCount }}</td></tr></tbody></v-table>
          </section>
        </div>
      </v-main>

      <v-dialog :model-value="Boolean(selectedAudit)" max-width="720" @update:model-value="value => { if (!value) selectedAudit = null }">
        <v-card v-if="selectedAudit" class="audit-dialog">
          <v-card-title>{{ actionLabel(selectedAudit.action) }}</v-card-title>
          <v-card-subtitle>{{ displayTime(selectedAudit.timestamp) }} · {{ auditSourceLabel(selectedAudit.source) }}</v-card-subtitle>
          <v-card-text>
            <div class="audit-dialog-meta">
              <div><span>用户</span><strong>{{ selectedAudit.username || '系统' }}</strong></div>
              <div><span>任务</span><code>{{ selectedAudit.jobId || '—' }}</code></div>
              <div><span>图纸</span><strong>{{ selectedAudit.targetName || '—' }}</strong></div>
              <div><span>页面</span><strong>{{ selectedAudit.pageNumber ?? '—' }}</strong></div>
            </div>
            <pre>{{ auditDetails(selectedAudit.details) }}</pre>
          </v-card-text>
          <v-card-actions><v-spacer /><v-btn color="primary" variant="text" @click="selectedAudit = null">关闭</v-btn></v-card-actions>
        </v-card>
      </v-dialog>
    </template>
  </v-app>
</template>

<style scoped>
.admin-app { background: rgb(var(--v-theme-background)); color: rgb(var(--v-theme-on-surface)); }
.admin-centered { width: 100%; height: 100%; display: grid; place-content: center; justify-items: center; gap: 14px; background: rgb(var(--v-theme-background)); }
.admin-forbidden h1 { margin: 2px 0 0; font-size: 22px; }.admin-forbidden p { margin: 0 0 8px; color: rgb(var(--v-theme-on-surface-muted)); }
.admin-topbar { border-bottom: 1px solid rgb(var(--v-theme-outline)) !important; box-shadow: none !important; }
.admin-brand { margin-left: 20px; display: flex; align-items: center; gap: 9px; }.admin-brand__icon { width: 27px; height: 27px; display: grid; place-items: center; color: rgb(var(--v-theme-on-header)); }.admin-brand__icon svg { width: 100%; height: 100%; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }.admin-brand > div { display: grid; line-height: 1.12; }.admin-brand strong { font-size: 18px; letter-spacing: .08em; }.admin-brand small { margin-top: 3px; color: rgba(255,255,255,.72) !important; font-size: 9px; font-weight: 500; letter-spacing: .08em; }
.admin-account-button { min-width: 0 !important; height: 36px !important; margin-right: 14px; padding: 0 10px 0 5px !important; flex: 0 0 auto; box-sizing: border-box; border: 0 !important; border-radius: 18px !important; background: transparent !important; color: rgb(var(--v-theme-on-header)) !important; text-transform: none !important; box-shadow: none !important; }.admin-account-button:hover { background: var(--ui-subtle) !important; color: var(--ui-text) !important; }.admin-account-button :deep(.v-btn__content) { display: flex; align-items: center; gap: 7px; }.admin-account-button__icon { width: 26px; height: 26px; display: grid; place-items: center; flex: 0 0 26px; border-radius: 50%; }.admin-account-button__icon svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }.admin-account-button__name { max-width: 160px; overflow: hidden; font-size: 12px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.admin-settings-button { width: 36px !important; height: 36px !important; flex: 0 0 36px; }
.admin-nav { border-right: 1px solid rgb(var(--v-theme-outline)) !important; background: rgb(var(--v-theme-surface)); }.admin-nav :deep(.v-list) { padding-top: 14px; }.admin-nav :deep(.v-list-item) { min-height: 40px; margin: 2px 8px; font-size: 12px; }.admin-nav__foot { padding: 16px 18px; display: flex; justify-content: space-between; border-top: 1px solid rgb(var(--v-theme-outline)); color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.admin-nav__foot strong { color: rgb(var(--v-theme-success)); }
.admin-main { height: 100vh; overflow: auto; }.admin-page { width: min(1240px, calc(100% - 48px)); margin: 0 auto; padding: 28px 0 48px; }.admin-page-header { min-height: 60px; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgb(var(--v-theme-outline)); }.admin-page-header span { color: rgb(var(--v-theme-secondary)); font-size: 9px; font-weight: 700; }.admin-page-header h1 { margin: 3px 0 14px; font-size: 24px; letter-spacing: 0; }
.business-filters { margin-bottom: 16px; padding: 12px; display: grid; grid-template-columns: 145px 145px minmax(150px, 1fr) minmax(130px, .8fr) minmax(160px, 1fr) auto auto auto; align-items: end; gap: 8px; border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }.metric-card { min-height: 128px; padding: 16px; display: grid; grid-template-columns: 22px 1fr; grid-template-rows: auto 1fr auto; gap: 5px 8px; border-radius: 6px !important; background: rgb(var(--v-theme-surface)); }.metric-card svg { color: rgb(var(--v-theme-secondary)); }.metric-card span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 11px; }.metric-card strong { grid-column: 1 / -1; align-self: end; font-size: 28px; line-height: 1; }.metric-card small { grid-column: 1 / -1; color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.metric-card--warning svg { color: rgb(var(--v-theme-warning)); }
.admin-panel { margin-top: 22px; padding: 0 0 18px; border-bottom: 1px solid rgb(var(--v-theme-outline)); }.admin-panel__heading { min-height: 46px; display: flex; align-items: center; justify-content: space-between; }.admin-panel__heading > div { display: flex; align-items: center; gap: 9px; }.admin-panel__heading h2 { margin: 0; font-size: 15px; }.admin-panel__heading > span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }
.effect-ranking-controls { display: flex; align-items: center; }.effect-ranking-switch { width: 32px !important; height: 32px !important; border-radius: 50% !important; color: rgb(var(--v-theme-secondary)) !important; }.effect-ranking-switch:hover { background: rgb(var(--v-theme-surface-muted)) !important; }
.dashboard-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(280px, .8fr); gap: 18px; }.dashboard-card { min-width: 0; }.trend-chart { height: 210px; padding: 14px 10px 0; display: flex; align-items: flex-end; gap: 8px; overflow-x: auto; border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }.trend-column { min-width: 34px; height: 100%; display: grid; grid-template-rows: 18px 1fr 25px; align-items: end; justify-items: center; color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; }.trend-column > div { width: 68%; min-height: 3px; align-self: end; border-radius: 3px 3px 0 0; background: rgb(var(--v-theme-secondary)); }.trend-column span { color: rgb(var(--v-theme-on-surface)); font-size: 9px; }.trend-column small { padding-top: 5px; white-space: nowrap; font-size: 8px; }.operation-summary { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-outline)); }.operation-summary > div { min-height: 88px; padding: 14px; display: grid; align-content: center; gap: 5px; background: rgb(var(--v-theme-surface)); }.operation-summary span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.operation-summary strong { font-size: 23px; }.business-table { min-width: 920px; }.failure-panel .admin-panel__heading svg, .failure-message { color: rgb(var(--v-theme-error)); }.failure-message { max-width: 360px; white-space: normal; }.operational-strip > div { flex: 1; }
.balance-panel { border-top: 3px solid rgb(var(--v-theme-success)); }.balance-values { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1px; background: rgb(var(--v-theme-outline)); }.balance-values > div { padding: 18px; display: grid; gap: 5px; background: rgb(var(--v-theme-surface)); }.balance-values span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.balance-values strong { font-size: 24px; }.balance-values small { color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; }
.status-strip { display: flex; align-items: stretch; overflow-x: auto; border: 1px solid rgb(var(--v-theme-outline)); }.status-strip > div { min-width: 120px; padding: 14px 18px; display: grid; gap: 5px; border-right: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }.status-strip span { color: rgb(var(--v-theme-on-surface-muted)); font: 10px Consolas, monospace; }.status-strip strong { font-size: 18px; }
.admin-filter { width: min(460px, 100%); margin: 8px 0 16px; display: grid; grid-template-columns: 1fr auto; gap: 8px; }.admin-filter--jobs { width: min(760px, 100%); grid-template-columns: minmax(260px, 1fr) 180px auto; }.admin-table { border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }.admin-table th { color: rgb(var(--v-theme-on-surface-muted)) !important; font-size: 10px; }.admin-table td { font-size: 11px; }.admin-table td:first-child strong, .admin-table td:first-child small, .admin-jobs-table td:nth-child(2) small, .admin-jobs-table td:nth-child(3) small { display: block; }.admin-table td:first-child small { max-width: 260px; overflow: hidden; color: rgb(var(--v-theme-on-surface-muted)); font: 9px Consolas, monospace; text-overflow: ellipsis; }.admin-jobs-table td:nth-child(2) small, .admin-jobs-table td:nth-child(3) small { margin-top: 3px; color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; }.admin-table code { color: rgb(var(--v-theme-secondary)); font-size: 10px; }.state-mark { color: rgb(var(--v-theme-success)); font-weight: 700; }.state-mark--off { color: rgb(var(--v-theme-error)); }.state-mark--active { color: rgb(var(--v-theme-secondary)); }.database-meta { display: flex; flex-wrap: wrap; gap: 24px; margin: 6px 0 16px; color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.database-meta strong { margin-left: 5px; color: rgb(var(--v-theme-on-surface)); }.admin-empty { padding: 22px !important; color: rgb(var(--v-theme-on-surface-muted)); text-align: center; font-size: 11px; }
.audit-filters { margin: 8px 0 16px; display: grid; grid-template-columns: 145px 145px minmax(130px, .7fr) minmax(130px, .7fr) minmax(170px, 1fr) minmax(180px, 1fr); gap: 8px; }.audit-search { grid-column: 1 / 5; }.audit-filter-actions { grid-column: 5 / 7; display: flex; align-items: center; justify-content: flex-end; gap: 6px; }.audit-table { min-width: 1050px; }.audit-table td:nth-child(3) strong, .audit-table td:nth-child(3) small, .audit-table td:nth-child(5) strong, .audit-table td:nth-child(5) small { display: block; }.audit-table td small { margin-top: 3px; max-width: 260px; overflow: hidden; color: rgb(var(--v-theme-on-surface-muted)); font: 9px Consolas, monospace; text-overflow: ellipsis; }.audit-source { padding: 3px 6px; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 3px; white-space: nowrap; }.audit-dialog-meta { margin-bottom: 14px; display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; }.audit-dialog-meta div { min-width: 0; display: grid; gap: 3px; }.audit-dialog-meta span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; }.audit-dialog-meta strong, .audit-dialog-meta code { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; }.audit-dialog pre { max-height: 360px; margin: 0; padding: 14px; overflow: auto; border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-background)); font: 10px/1.6 Consolas, monospace; white-space: pre-wrap; word-break: break-word; }

/* Component polish: preserve the workspace palette while improving hierarchy and rhythm. */
.admin-app { background: linear-gradient(135deg, rgb(var(--v-theme-background)), rgb(var(--v-theme-surface-muted))); }
.admin-topbar { box-shadow: 0 8px 24px rgba(16,42,67,.12) !important; }
.admin-brand strong { letter-spacing: .08em; }.admin-brand small { margin-top: 3px; letter-spacing: .08em; }
.admin-nav { box-shadow: 8px 0 24px rgba(16,42,67,.045) !important; }
.admin-nav :deep(.v-list-item) { margin: 4px 10px; border-radius: 9px !important; transition: background .18s ease, color .18s ease, transform .18s ease; }
.admin-nav :deep(.v-list-item:hover) { background: rgb(var(--v-theme-surface-muted)); transform: translateX(2px); }
.admin-nav :deep(.v-list-item--active) { background: rgb(var(--v-theme-surface-selected)) !important; font-weight: 700; }
.admin-page { width: min(1320px, calc(100% - 56px)); padding-top: 30px; }
.admin-page-header { min-height: 70px; margin-bottom: 22px; padding: 0 4px; }
.admin-page-header h1 { margin-bottom: 16px; font-size: 25px; }.admin-page-header > div > span { letter-spacing: .18em; }
.admin-page-header .v-btn { border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }
.admin-page-header .admin-refresh-button { border: 0 !important; background: transparent !important; box-shadow: none !important; }
.admin-page-header .admin-refresh-button :deep(.v-btn__overlay) { opacity: 0 !important; }
.admin-page-header .admin-refresh-button--active, .admin-page-header .admin-refresh-button:active, .admin-page-header .admin-refresh-button:focus-visible { border: 1px solid color-mix(in srgb, rgb(var(--v-theme-secondary)) 58%, transparent) !important; box-shadow: 0 0 0 3px color-mix(in srgb, rgb(var(--v-theme-secondary)) 16%, transparent), 0 5px 14px rgba(16,42,67,.14) !important; }
.admin-topbar-button--active, .admin-account-button[aria-expanded="true"] { border-color: rgba(113,210,203,.58) !important; box-shadow: 0 0 0 3px rgba(113,210,203,.16), 0 5px 14px rgba(7,26,39,.24) !important; }
.business-filters, .audit-filters, .admin-filter {
  border: 1px solid rgb(var(--v-theme-outline)); border-radius: 12px; background: rgb(var(--v-theme-surface));
  box-shadow: 0 8px 24px rgba(16,42,67,.055);
}
.business-filters, .audit-filters { padding: 14px; }.admin-filter { width: min(520px, 100%); padding: 10px; }
.admin-filter--jobs { width: min(820px, 100%); }
.business-filters :deep(.v-field), .audit-filters :deep(.v-field), .admin-filter :deep(.v-field) { border-radius: 8px; }
.business-filters .v-btn, .audit-filter-actions .v-btn, .admin-filter .v-btn { min-height: 40px; border-radius: 8px; font-weight: 700; letter-spacing: .01em; }
.metric-grid { gap: 14px; }
.metric-card {
  position: relative; min-height: 138px; padding: 18px; overflow: hidden; border-radius: 12px !important;
  box-shadow: 0 8px 24px rgba(16,42,67,.055); transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}
.metric-card::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 3px; background: rgb(var(--v-theme-secondary)); opacity: .78; }
.metric-card:hover { border-color: color-mix(in srgb, rgb(var(--v-theme-secondary)) 48%, rgb(var(--v-theme-outline))); box-shadow: 0 12px 30px rgba(16,42,67,.09); transform: translateY(-2px); }
.metric-card--warning::before { background: rgb(var(--v-theme-warning)); }.metric-card strong { font-size: 30px; letter-spacing: -.02em; }
.admin-panel {
  margin-top: 18px; padding: 18px; overflow: hidden; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 12px;
  background: rgb(var(--v-theme-surface)); box-shadow: 0 8px 24px rgba(16,42,67,.05);
}
.admin-panel__heading { min-height: 38px; margin-bottom: 12px; }.admin-panel__heading h2 { font-size: 14px; }.admin-panel__heading svg { color: rgb(var(--v-theme-secondary)); }
.dashboard-grid { gap: 20px; }.dashboard-card + .dashboard-card { padding-left: 18px; border-left: 1px solid rgb(var(--v-theme-outline)); }
.trend-chart { height: 226px; padding: 18px 14px 0; border-radius: 9px; background: linear-gradient(180deg, rgb(var(--v-theme-surface-muted)), rgb(var(--v-theme-surface))); }
.trend-column > div { width: 72%; border-radius: 5px 5px 0 0; background: linear-gradient(180deg, rgb(var(--v-theme-secondary)), rgb(var(--v-theme-primary))); box-shadow: 0 -3px 10px rgba(45,111,120,.13); }
.operation-summary, .balance-values { overflow: hidden; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 9px; }
.operation-summary > div, .balance-values > div, .status-strip > div { transition: background .18s ease; }
.operation-summary > div:hover, .balance-values > div:hover, .status-strip > div:hover { background: rgb(var(--v-theme-surface-muted)); }
.balance-panel { border-top: 1px solid rgb(var(--v-theme-outline)); }.balance-values { gap: 0; }
.status-strip { border-radius: 9px; }.status-strip > div:last-child { border-right: 0; }
.admin-table { overflow: hidden; border-radius: 9px; }
.admin-table :deep(thead th) { height: 42px !important; background: rgb(var(--v-theme-surface-muted)); font-weight: 700 !important; letter-spacing: .04em; }
.admin-table :deep(tbody tr) { transition: background .15s ease; }.admin-table :deep(tbody tr:hover) { background: rgb(var(--v-theme-surface-muted)); }
.admin-table :deep(td) { height: 50px !important; border-color: rgb(var(--v-theme-outline)) !important; }
.state-mark, .audit-source { display: inline-flex; align-items: center; min-height: 23px; padding: 3px 8px; border: 0; border-radius: 999px; background: color-mix(in srgb, rgb(var(--v-theme-success)) 12%, transparent); }
.state-mark--off { background: color-mix(in srgb, rgb(var(--v-theme-error)) 11%, transparent); }.state-mark--active { background: color-mix(in srgb, rgb(var(--v-theme-secondary)) 11%, transparent); }
.audit-source { background: rgb(var(--v-theme-surface-muted)); color: rgb(var(--v-theme-on-surface-muted)); font-weight: 700; }
.admin-table + .v-pagination { margin-top: 14px; }.admin-empty { min-height: 76px; vertical-align: middle; }
.database-meta { padding: 12px 14px; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 9px; background: rgb(var(--v-theme-surface-muted)); }
.audit-dialog { overflow: hidden; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 13px !important; box-shadow: 0 20px 56px rgba(16,42,67,.24) !important; }
.audit-dialog .v-card-title { padding: 20px 22px 4px; font-size: 17px; font-weight: 800; }.audit-dialog .v-card-subtitle { padding: 2px 22px 15px; }.audit-dialog .v-card-text { padding: 18px 22px; }
.audit-dialog-meta { padding: 13px; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 9px; background: rgb(var(--v-theme-surface-muted)); }
.audit-dialog pre { border-radius: 9px; }
@media (max-width: 1100px) { .business-filters { grid-template-columns: repeat(3, minmax(0, 1fr)); }.dashboard-grid { grid-template-columns: 1fr; } }
@media (max-width: 1100px) { .dashboard-card + .dashboard-card { padding-top: 18px; padding-left: 0; border-top: 1px solid rgb(var(--v-theme-outline)); border-left: 0; } }
@media (max-width: 900px) { .admin-nav { width: 184px !important; }.metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.admin-page { width: min(100% - 28px, 1240px); }.admin-panel { padding: 15px; } }
@media (max-width: 640px) { .admin-nav { display: none; }.admin-main { padding-left: 0 !important; }.metric-grid { grid-template-columns: 1fr; }.business-filters, .audit-filters { grid-template-columns: 1fr; }.audit-search, .audit-filter-actions { grid-column: 1; }.audit-filter-actions { justify-content: flex-start; flex-wrap: wrap; }.admin-account-button__name { display: none; }.admin-account-button { width: 40px !important; padding: 0 5px !important; }.admin-page-header h1 { font-size: 20px; }.admin-filter--jobs { grid-template-columns: 1fr; }.admin-table-panel { overflow-x: auto; }.admin-table { min-width: 720px; }.admin-jobs-table { min-width: 1080px; } }
</style>
