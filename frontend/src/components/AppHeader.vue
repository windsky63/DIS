<script setup>
import { formatBeijingTime } from '../beijingTime.js'
import AppBrandIcon from './AppBrandIcon.vue'
import ConfirmedLogoutButton from './ConfirmedLogoutButton.vue'

defineProps({
  height: { type: Number, required: true },
  backendHealth: { type: String, default: 'checking' },
  mineruHealth: { type: String, default: 'checking' },
  mineruStatusText: { type: String, default: '正在连接' },
  shortcutMenu: Boolean,
  shortcuts: { type: Object, required: true },
  user: { type: Object, default: null },
  projects: { type: Array, default: () => [] },
  activeProject: { type: Object, default: () => ({ name: '未选择项目' }) },
  buttonVisibility: { type: Object, required: true },
})

defineEmits([
  'update:shortcutMenu',
  'openQueue',
  'openAssistant',
  'openTutorial',
  'openSettings',
  'switchProject',
  'openProjectRules',
  'logout',
])
</script>

<template>
  <v-app-bar color="header" elevation="3" :height="height">
    <v-app-bar-title class="ml-5">
      <div class="app-title">
        <span class="app-title__icon" aria-hidden="true"><AppBrandIcon /></span>
        <span class="app-title__copy"><span>图纸标识识别系统</span><small>焊口 · 阀门 · 法兰 · 支架拓扑识别</small></span>
      </div>
    </v-app-bar-title>
    <div class="header-icon-area mr-4">
      <button v-if="buttonVisibility.analysisQueue" data-tour="analysis-queue-button" type="button" class="header-status-icon header-status-icon--interactive" :class="`status-${backendHealth}`" :aria-label="backendHealth === 'ready' ? '查看任务队列（图元引擎就绪）' : backendHealth === 'offline' ? '查看任务队列（后端未连接）' : '查看任务队列（图元引擎正在连接）'" @click="$emit('openQueue')">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="2.2" /><circle cx="19" cy="6" r="2.2" /><circle cx="19" cy="18" r="2.2" /><path d="m7 11 9.8-4M7 13l9.8 4" /></svg><span class="status-dot" />
        <v-tooltip activator="parent" location="bottom">{{ backendHealth === 'ready' ? '图元引擎就绪 · 点击查看任务队列' : backendHealth === 'offline' ? '后端未连接 · 点击查看任务队列' : '图元引擎正在连接 · 点击查看任务队列' }}</v-tooltip>
      </button>
      <button v-if="buttonVisibility.mineruStatus" data-tour="mineru-status" type="button" class="header-status-icon" :class="`status-${mineruHealth}`" :aria-label="mineruHealth === 'ready' ? 'MinerU 就绪' : mineruHealth === 'offline' ? 'MinerU 未就绪' : 'MinerU 检测中'">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 18V6l6 6 6-6v12" /><path d="M9 18h6" /></svg><span class="status-dot" />
        <v-tooltip activator="parent" location="bottom">{{ mineruHealth === 'ready' ? 'MinerU 就绪' : mineruHealth === 'offline' ? 'MinerU 未就绪' : mineruStatusText }}</v-tooltip>
      </button>
      <v-menu v-if="buttonVisibility.projectSwitch" location="bottom end">
          <template #activator="{ props }"><v-btn v-bind="props" data-tour="project-switch-button" class="header-icon-button project-switch-button" icon size="small" aria-label="切换项目"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 7.5h6l1.8 2h9.2v8.8a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7Z" /><path d="M7 4h10m0 0-2-2m2 2-2 2" /></svg><v-tooltip activator="parent" location="bottom">切换项目 · {{ activeProject.name }}</v-tooltip></v-btn></template>
        <v-card class="project-switch-card" min-width="320">
          <div class="project-switch-card__header"><div class="project-switch-card__copy"><small>当前识别项目</small><strong>{{ activeProject.name }}</strong></div><button type="button" class="project-rules-icon" aria-label="查看项目识别规则" @click="$emit('openProjectRules')"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /><circle cx="9" cy="6" r="2" /><circle cx="15" cy="12" r="2" /><circle cx="9" cy="18" r="2" /></svg><v-tooltip activator="parent" location="bottom">查看项目识别规则</v-tooltip></button></div>
          <v-list density="compact"><v-list-item v-for="project in projects" :key="project.id" :title="project.name" :subtitle="project.description" :active="project.id === activeProject.id" color="secondary" @click="$emit('switchProject', project.id)"><template #append><v-chip v-if="project.id === activeProject.id" size="x-small" color="secondary" variant="tonal">当前</v-chip></template></v-list-item></v-list>
        </v-card>
      </v-menu>
      <v-btn v-if="buttonVisibility.assistant" data-tour="assistant-button" class="header-icon-button assistant-button" icon size="small" aria-label="打开 AI 助手" @click="$emit('openAssistant')">
        <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M7 8.5A5 5 0 0 1 12 4a5 5 0 0 1 5 4.5A4.5 4.5 0 0 1 18 17H9l-4 3v-5.2A4.5 4.5 0 0 1 7 8.5Z" /><path d="M9 11h.01M12 11h.01M15 11h.01" /></svg>
        <v-tooltip activator="parent" location="bottom">AI 助手</v-tooltip>
      </v-btn>
      <div data-tour="header-help" class="header-help-actions">
        <v-btn v-if="buttonVisibility.tutorial" data-tour="tutorial-button" class="header-icon-button tutorial-button" icon size="small" aria-label="打开操作教程" @click="$emit('openTutorial')">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="m14.8 9.2-1.7 3.9-3.9 1.7 1.7-3.9Z" /><circle cx="12" cy="12" r="1" /></svg>
          <v-tooltip activator="parent" location="bottom">操作教程</v-tooltip>
        </v-btn>
        <v-menu v-if="buttonVisibility.shortcuts" :model-value="shortcutMenu" location="bottom end" :close-on-content-click="false" @update:model-value="$emit('update:shortcutMenu', $event)">
          <template #activator="{ props }"><v-btn v-bind="props" data-tour="shortcut-button" class="header-icon-button shortcut-button" icon size="small" aria-label="查看快捷键">?<v-tooltip activator="parent" location="bottom">快捷键（{{ shortcuts.toggleHelp }}）</v-tooltip></v-btn></template>
          <v-card class="shortcut-card" min-width="360">
            <v-card-title>系统快捷键</v-card-title>
            <v-list density="compact">
              <div class="shortcut-list-group"><v-list-subheader>常用操作</v-list-subheader><v-list-item :title="shortcuts.toggleHelp" subtitle="打开 / 关闭本面板" /><v-list-item :title="shortcuts.analyze" subtitle="开始智能编号" /><v-list-item :title="shortcuts.save" subtitle="保存当前校对结果" /><v-list-item :title="`${shortcuts.undo} / ${shortcuts.redo}`" subtitle="撤销 / 重做编辑" /></div>
              <div class="shortcut-list-group"><v-list-subheader>标识编辑</v-list-subheader><v-list-item :title="`${shortcuts.addWeld} / ${shortcuts.addValve} / ${shortcuts.addFlange} / ${shortcuts.addSupport} / ${shortcuts.modifyAll}`" subtitle="切换焊口 / 阀门 / 法兰 / 支架 / 其他标识修改模式" /><v-list-item :title="shortcuts.addAtPointer" subtitle="在修改模式中，于鼠标位置新增对应类型标识" /><v-list-item :title="`${shortcuts.copyMarker} / ${shortcuts.pasteMarker}`" subtitle="复制选中标识 / 粘贴到鼠标位置" /><v-list-item :title="shortcuts.shiftFollowing" subtitle="顺延选中标识后面的同类编号" /><v-list-item :title="shortcuts.deleteWeld" subtitle="删除选中标识（可撤销）" /><v-list-item :title="shortcuts.deleteRegion" subtitle="进入区域删除；左键拖框，Esc 退出" /><v-list-item :title="`${shortcuts.swapWeld}（按两次）`" subtitle="依次选择两个焊口并交换编号" /></div>
              <div class="shortcut-list-group"><v-list-subheader>页面与画布</v-list-subheader><v-list-item :title="shortcuts.openReferenceWindow" subtitle="打开对照图分屏窗口；已打开时聚焦" /><v-list-item :title="`${shortcuts.previousPage} / ${shortcuts.nextPage}`" subtitle="切换上一张或下一张设计图页" /><v-list-item title="滚轮 / Ctrl+滚轮" subtitle="上下翻页 / 以指针为中心缩放" /><v-list-item :title="shortcuts.fitCanvas" subtitle="使图纸适合当前画布" /><v-list-item title="空白处按住左键或中键拖动" subtitle="平移图纸" /><v-list-item title="双击标识编号 / Esc" subtitle="编辑编号 / 取消当前操作" /></div>
            </v-list>
          </v-card>
        </v-menu>
        <v-btn data-tour="settings-button" class="header-icon-button settings-button" icon size="small" aria-label="系统设置" @click="$emit('openSettings')"><span class="settings-glyph" aria-hidden="true">⚙</span><v-tooltip activator="parent" location="bottom">系统设置</v-tooltip></v-btn>
        <v-menu v-if="user && buttonVisibility.userMenu" location="bottom end" :close-on-content-click="false">
          <template #activator="{ props }">
            <v-btn v-bind="props" data-tour="user-menu-button" class="header-icon-button user-menu-button" icon size="small" aria-label="打开用户菜单">
              <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5" /><path d="M5.5 19c.7-3.5 3-5.3 6.5-5.3s5.8 1.8 6.5 5.3" /><circle cx="12" cy="12" r="9" /></svg>
              <v-tooltip activator="parent" location="bottom">用户中心</v-tooltip>
            </v-btn>
          </template>
          <v-card class="user-menu-card" min-width="310">
            <div class="user-menu-card__header">
              <span class="user-menu-avatar" aria-hidden="true">{{ String(user.username || 'U').slice(0, 1).toUpperCase() }}</span>
              <div><small>当前登录用户</small><strong>{{ user.username }}</strong></div>
            </div>
            <div class="user-menu-details">
              <div><span>用户名</span><strong>{{ user.username }}</strong></div>
              <div><span>创建时间</span><strong>{{ formatBeijingTime(user.createdAt) }}</strong></div>
            </div>
            <v-divider />
            <v-card-actions class="user-menu-actions">
              <v-btn v-if="user.isAdmin" block to="/admin" color="primary" variant="tonal">后台管理</v-btn>
              <ConfirmedLogoutButton @confirm="$emit('logout')" />
            </v-card-actions>
          </v-card>
        </v-menu>
      </div>
    </div>
  </v-app-bar>
</template>
