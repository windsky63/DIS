<script setup>
const open = defineModel({ type: Boolean, required: true })
const themePreference = defineModel('themePreference', { type: String, required: true })
const tooltips = defineModel('tooltips', { type: Boolean, required: true })

defineProps({ themeOptions: { type: Array, default: () => [] } })
</script>

<template>
  <v-dialog v-model="open" max-width="560">
    <v-card class="settings-card">
      <div class="draft-manager-header settings-header">
        <span class="draft-manager-header__icon settings-header__icon" aria-hidden="true">⚙</span>
        <div><v-card-title>系统设置</v-card-title><v-card-subtitle>管理后台界面与全局显示偏好。</v-card-subtitle></div>
      </div>
      <v-card-text class="settings-content">
        <div class="settings-section"><div class="section-label">主题与配色</div><v-select v-model="themePreference" :items="themeOptions" label="界面主题" /><div class="shortcut-settings__hint mt-2">选择后立即应用到后台和工作台，并保存在当前浏览器。</div></div>
        <div class="settings-section"><div class="section-label">交互提示</div><v-switch v-model="tooltips" color="secondary" hide-details label="启用全局 Tooltip 提示" /><div class="shortcut-settings__hint mt-2">该偏好与工作台共享。</div></div>
        <div class="settings-section"><div class="section-label">工作台专属设置</div><div class="shortcut-settings__hint">画布性能、标识外观、编号规则和快捷键需在工作台中调整。</div><v-btn block to="/main" color="secondary" variant="tonal" class="mt-3">返回工作台继续设置</v-btn></div>
      </v-card-text>
      <v-card-actions class="settings-actions"><v-spacer /><v-btn color="primary" @click="open = false">完成</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
