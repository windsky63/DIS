<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity, ArrowLeft, CircleUserRound, Database, Files, KeyRound,
  LayoutDashboard, LogOut, RefreshCw, Server, ShieldCheck, Users,
} from '@lucide/vue'

import { api } from '../api.js'
import { useAuthSession } from '../composables/useAuthSession.js'

const route = useRoute()
const router = useRouter()
const auth = useAuthSession({ client: api })
const overview = ref(null)
const balance = ref(null)
const users = ref([])
const pagination = ref({ page: 1, pageSize: 20, total: 0, totalPages: 1 })
const search = ref('')
const loading = ref(false)
const error = ref('')

const section = computed(() => ({
  'admin-users': 'users',
  'admin-database': 'database',
}[route.name] || 'dashboard'))
const pageTitle = computed(() => ({
  dashboard: '运行仪表盘', users: '用户表', database: '数据库信息',
}[section.value]))
const navItems = [
  { title: '仪表盘', to: '/admin', icon: LayoutDashboard },
  { title: '用户表', to: '/admin/users', icon: Users },
  { title: '数据库', to: '/admin/database', icon: Database },
]
const metrics = computed(() => overview.value?.metrics || {})
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
  return value ? String(value).replace('T', ' ') : '—'
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

async function loadUsers(page = pagination.value.page) {
  const response = await api.getAdminUsers({ search: search.value.trim(), page, pageSize: pagination.value.pageSize })
  users.value = response.users || []
  pagination.value = response.pagination || pagination.value
}

async function refresh() {
  if (auth.user.value?.isAdmin !== true) return
  loading.value = true
  error.value = ''
  try {
    if (section.value === 'users') await Promise.all([loadOverview(), loadUsers()])
    else await loadOverview({ includeBalance: section.value === 'dashboard' })
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    loading.value = false
  }
}

async function submitSearch() {
  pagination.value.page = 1
  await loadUsers(1)
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

onMounted(async () => {
  window.addEventListener('drawing-marker:authentication-required', authenticationRequired)
  await auth.restore()
})

onBeforeUnmount(() => window.removeEventListener('drawing-marker:authentication-required', authenticationRequired))
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
      <v-app-bar color="header" elevation="0" height="58" class="admin-topbar">
        <v-btn to="/main" icon variant="text" aria-label="返回工作台"><ArrowLeft :size="20" /><v-tooltip activator="parent">返回工作台</v-tooltip></v-btn>
        <div class="admin-brand"><ShieldCheck :size="23" /><div><strong>系统后台</strong><small>图纸标识识别系统</small></div></div>
        <v-spacer />
        <div class="admin-account"><CircleUserRound :size="18" /><span>{{ auth.user.value.username }}</span></div>
        <v-btn icon variant="text" aria-label="退出登录" @click="logout"><LogOut :size="19" /><v-tooltip activator="parent">退出登录</v-tooltip></v-btn>
      </v-app-bar>

      <v-navigation-drawer permanent width="220" class="admin-nav">
        <div class="admin-nav__label">管理视图</div>
        <v-list nav density="compact">
          <v-list-item v-for="item in navItems" :key="item.to" :to="item.to" :title="item.title" color="primary" rounded="0">
            <template #prepend><component :is="item.icon" :size="18" /></template>
          </v-list-item>
        </v-list>
        <template #append><div class="admin-nav__foot"><span>SQLite WAL</span><strong>{{ overview?.database?.journalMode || '—' }}</strong></div></template>
      </v-navigation-drawer>

      <v-main class="admin-main">
        <div class="admin-page">
          <header class="admin-page-header">
            <div><span>ADMINISTRATION</span><h1>{{ pageTitle }}</h1></div>
            <v-btn icon variant="text" :loading="loading" aria-label="刷新数据" @click="refresh"><RefreshCw :size="19" /><v-tooltip activator="parent">刷新数据</v-tooltip></v-btn>
          </header>

          <v-alert v-if="error" type="error" variant="tonal" density="compact" class="mb-4">{{ error }}</v-alert>

          <template v-if="section === 'dashboard'">
            <section class="metric-grid" aria-label="系统指标">
              <v-card class="metric-card" variant="outlined"><Users :size="19" /><span>用户总数</span><strong>{{ metrics.totalUsers ?? '—' }}</strong><small>{{ metrics.activeSessions ?? 0 }} 个有效登录会话</small></v-card>
              <v-card class="metric-card" variant="outlined"><Activity :size="19" /><span>运行中任务</span><strong>{{ metrics.runningJobs ?? '—' }}</strong><small>{{ metrics.queuedJobs ?? 0 }} 个等待任务</small></v-card>
              <v-card class="metric-card" variant="outlined"><Files :size="19" /><span>任务总数</span><strong>{{ metrics.totalJobs ?? '—' }}</strong><small>{{ metrics.completedJobs ?? 0 }} 个已完成</small></v-card>
              <v-card class="metric-card" variant="outlined"><Server :size="19" /><span>解析 Worker</span><strong>{{ overview?.workers?.count ?? '—' }}</strong><small>{{ overview?.workers?.status === 'ready' ? '服务可用' : '当前离线' }}</small></v-card>
              <v-card class="metric-card" variant="outlined"><Database :size="19" /><span>数据库大小</span><strong>{{ formatBytes(overview?.database?.sizeBytes) }}</strong><small>{{ overview?.database?.tableCount ?? 0 }} 张业务表</small></v-card>
              <v-card class="metric-card metric-card--warning" variant="outlined"><Activity :size="19" /><span>失败任务</span><strong>{{ metrics.failedJobs ?? '—' }}</strong><small>{{ metrics.reviewedPages ?? 0 }} 次页面审核</small></v-card>
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

            <section class="admin-panel">
              <div class="admin-panel__heading"><div><Activity :size="20" /><h2>任务状态</h2></div><span>实时快照</span></div>
              <div class="status-strip"><div v-for="(count, statusName) in overview?.jobStatuses" :key="statusName"><span>{{ statusName }}</span><strong>{{ count }}</strong></div><div v-if="!Object.keys(overview?.jobStatuses || {}).length" class="admin-empty">暂无任务记录</div></div>
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

          <section v-else class="admin-panel admin-table-panel">
            <div class="admin-panel__heading"><div><Database :size="20" /><h2>SQLite 数据表</h2></div><span>{{ formatBytes(overview?.database?.sizeBytes) }}</span></div>
            <div class="database-meta"><span>存储引擎 <strong>{{ overview?.database?.engine || '—' }}</strong></span><span>日志模式 <strong>{{ overview?.database?.journalMode || '—' }}</strong></span><span>表数量 <strong>{{ overview?.database?.tableCount ?? '—' }}</strong></span></div>
            <v-table density="comfortable" class="admin-table"><thead><tr><th>业务表</th><th>数据库表名</th><th class="text-right">记录数</th></tr></thead><tbody><tr v-for="table in overview?.database?.tables || []" :key="table.name"><td><strong>{{ table.label }}</strong></td><td><code>{{ table.name }}</code></td><td class="text-right">{{ table.rowCount }}</td></tr></tbody></v-table>
          </section>
        </div>
      </v-main>
    </template>
  </v-app>
</template>

<style scoped>
.admin-app { background: rgb(var(--v-theme-background)); color: rgb(var(--v-theme-on-surface)); }
.admin-centered { width: 100%; height: 100%; display: grid; place-content: center; justify-items: center; gap: 14px; background: rgb(var(--v-theme-background)); }
.admin-forbidden h1 { margin: 2px 0 0; font-size: 22px; }.admin-forbidden p { margin: 0 0 8px; color: rgb(var(--v-theme-on-surface-muted)); }
.admin-topbar { border-bottom: 1px solid rgb(var(--v-theme-outline)) !important; box-shadow: none !important; }
.admin-brand { display: flex; align-items: center; gap: 10px; }.admin-brand > div { display: grid; }.admin-brand strong { font-size: 14px; }.admin-brand small { color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; }
.admin-account { display: flex; align-items: center; gap: 7px; margin-right: 8px; color: rgb(var(--v-theme-on-surface-muted)); font-size: 12px; }
.admin-nav { border-right: 1px solid rgb(var(--v-theme-outline)) !important; background: rgb(var(--v-theme-surface)); }.admin-nav__label { padding: 22px 18px 8px; color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; font-weight: 700; }.admin-nav :deep(.v-list-item) { min-height: 40px; margin: 2px 8px; font-size: 12px; }.admin-nav__foot { padding: 16px 18px; display: flex; justify-content: space-between; border-top: 1px solid rgb(var(--v-theme-outline)); color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.admin-nav__foot strong { color: rgb(var(--v-theme-success)); }
.admin-main { height: 100vh; overflow: auto; }.admin-page { width: min(1240px, calc(100% - 48px)); margin: 0 auto; padding: 28px 0 48px; }.admin-page-header { min-height: 60px; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid rgb(var(--v-theme-outline)); }.admin-page-header span { color: rgb(var(--v-theme-secondary)); font-size: 9px; font-weight: 700; }.admin-page-header h1 { margin: 3px 0 14px; font-size: 24px; letter-spacing: 0; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }.metric-card { min-height: 128px; padding: 16px; display: grid; grid-template-columns: 22px 1fr; grid-template-rows: auto 1fr auto; gap: 5px 8px; border-radius: 6px !important; background: rgb(var(--v-theme-surface)); }.metric-card svg { color: rgb(var(--v-theme-secondary)); }.metric-card span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 11px; }.metric-card strong { grid-column: 1 / -1; align-self: end; font-size: 28px; line-height: 1; }.metric-card small { grid-column: 1 / -1; color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.metric-card--warning svg { color: rgb(var(--v-theme-warning)); }
.admin-panel { margin-top: 22px; padding: 0 0 18px; border-bottom: 1px solid rgb(var(--v-theme-outline)); }.admin-panel__heading { min-height: 46px; display: flex; align-items: center; justify-content: space-between; }.admin-panel__heading > div { display: flex; align-items: center; gap: 9px; }.admin-panel__heading h2 { margin: 0; font-size: 15px; }.admin-panel__heading > span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }
.balance-panel { border-top: 3px solid rgb(var(--v-theme-success)); }.balance-values { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 1px; background: rgb(var(--v-theme-outline)); }.balance-values > div { padding: 18px; display: grid; gap: 5px; background: rgb(var(--v-theme-surface)); }.balance-values span { color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.balance-values strong { font-size: 24px; }.balance-values small { color: rgb(var(--v-theme-on-surface-muted)); font-size: 9px; }
.status-strip { display: flex; align-items: stretch; overflow-x: auto; border: 1px solid rgb(var(--v-theme-outline)); }.status-strip > div { min-width: 120px; padding: 14px 18px; display: grid; gap: 5px; border-right: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }.status-strip span { color: rgb(var(--v-theme-on-surface-muted)); font: 10px Consolas, monospace; }.status-strip strong { font-size: 18px; }
.admin-filter { width: min(460px, 100%); margin: 8px 0 16px; display: grid; grid-template-columns: 1fr auto; gap: 8px; }.admin-table { border: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }.admin-table th { color: rgb(var(--v-theme-on-surface-muted)) !important; font-size: 10px; }.admin-table td { font-size: 11px; }.admin-table td:first-child strong, .admin-table td:first-child small { display: block; }.admin-table td:first-child small { max-width: 260px; overflow: hidden; color: rgb(var(--v-theme-on-surface-muted)); font: 9px Consolas, monospace; text-overflow: ellipsis; }.admin-table code { color: rgb(var(--v-theme-secondary)); font-size: 10px; }.state-mark { color: rgb(var(--v-theme-success)); font-weight: 700; }.state-mark--off { color: rgb(var(--v-theme-error)); }.database-meta { display: flex; flex-wrap: wrap; gap: 24px; margin: 6px 0 16px; color: rgb(var(--v-theme-on-surface-muted)); font-size: 10px; }.database-meta strong { margin-left: 5px; color: rgb(var(--v-theme-on-surface)); }.admin-empty { padding: 22px !important; color: rgb(var(--v-theme-on-surface-muted)); text-align: center; font-size: 11px; }
@media (max-width: 900px) { .admin-nav { width: 184px !important; }.metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.admin-page { width: min(100% - 28px, 1240px); } }
@media (max-width: 640px) { .admin-nav { display: none; }.admin-main { padding-left: 0 !important; }.metric-grid { grid-template-columns: 1fr; }.admin-account span { display: none; }.admin-page-header h1 { font-size: 20px; }.admin-table-panel { overflow-x: auto; }.admin-table { min-width: 720px; } }
</style>
