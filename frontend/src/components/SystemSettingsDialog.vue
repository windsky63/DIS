<script setup>
defineProps({
  themeOptions: { type: Array, default: () => [] },
  canvasPerformanceOptions: { type: Array, default: () => [] },
  canvasPerformanceProfile: { type: Object, required: true },
  markerAppearanceGroups: { type: Array, default: () => [] },
  activeAppearanceGroup: { type: Object, required: true },
  shapeOptions: { type: Array, default: () => [] },
  shortcutRows: { type: Array, default: () => [] },
  shortcutEditors: { type: Object, required: true },
  shortcutModifierOptions: { type: Array, default: () => [] },
  shortcutConflict: { type: String, default: '' },
})

const themePreference = defineModel('themePreference', { type: String, required: true })
const open = defineModel({ type: Boolean, required: true })
const leftDrawer = defineModel('leftDrawer', { type: Boolean, required: true })
const reviewDrawer = defineModel('reviewDrawer', { type: Boolean, required: true })
const tooltips = defineModel('tooltips', { type: Boolean, required: true })
const operationMessages = defineModel('operationMessages', { type: Boolean, required: true })
const errorMessages = defineModel('errorMessages', { type: Boolean, required: true })
const pageButtonCount = defineModel('pageButtonCount', { type: Number, required: true })
const clearReferences = defineModel('clearReferences', { type: Boolean, required: true })
const autoReferenceWindow = defineModel('autoReferenceWindow', { type: Boolean, required: true })
const referenceHintLocation = defineModel('referenceHintLocation', { type: String, required: true })
const performanceMode = defineModel('performanceMode', { type: String, required: true })
const appearanceTab = defineModel('appearanceTab', { type: String, required: true })
const manualLeaderLength = defineModel('manualLeaderLength', { type: Number, required: true })
const referenceHintLocationOptions = [
  { title: '对照图窗口（默认）', value: 'reference' },
  { title: '设计图窗口', value: 'design' },
]
defineEmits(['open-drafts', 'update-shortcut-modifier', 'capture-shortcut-key', 'restore-default-shortcuts', 'open-symbol-config', 'auto-reference-window-change', 'reference-hint-location-change'])
</script>

<template>
  <v-dialog v-model="open" max-width="620">
    <v-card class="settings-card">
      <div class="draft-manager-header settings-header"><span class="draft-manager-header__icon settings-header__icon" aria-hidden="true">⚙</span><div><v-card-title>系统设置</v-card-title><v-card-subtitle>管理界面、草稿、渲染性能和快捷操作。</v-card-subtitle></div></div>
      <v-card-text class="settings-content">
        <div class="settings-section"><div class="section-label">主题与配色</div><v-select v-model="themePreference" :items="themeOptions" label="界面主题" /><div class="shortcut-settings__hint mt-2">选择后立即生效并自动保存；跟随系统会随设备的浅色或深色设置切换。</div></div>
        <div class="settings-section"><div class="section-label">区域删除</div><div class="shortcut-settings__hint">默认按 D 进入，左键拖框删除定位点在框内的各类标识；Esc 退出，Ctrl+Z 撤销。可在下方修改快捷键。</div></div>
        <div class="settings-section"><div class="section-label">界面布局</div><v-switch v-model="leftDrawer" color="secondary" hide-details label="显示识别输入与操作抽屉" /><v-switch v-model="reviewDrawer" color="secondary" hide-details label="显示拓扑标识辅助区域" /><v-switch v-model="tooltips" color="secondary" hide-details label="启用全局 Tooltip 提示" /><v-switch v-model="operationMessages" color="secondary" hide-details label="显示操作信息" /><v-switch v-model="errorMessages" color="secondary" hide-details label="显示错误信息" /><v-switch v-model="autoReferenceWindow" color="secondary" hide-details label="自动使用独立对照图窗口" @update:model-value="$emit('auto-reference-window-change', $event)" /><div class="shortcut-settings__hint mt-1">开启后复用同一个窗口，并在切换设计页时自动显示对应对照页；浏览器首次可能要求允许弹窗。</div><v-select v-model="referenceHintLocation" class="mt-3" :items="referenceHintLocationOptions" label="分屏时对照图提示显示位置" @update:model-value="$emit('reference-hint-location-change', $event)" /><div class="shortcut-settings__hint mt-1">选择对照图窗口时，提示清单与定位标记会随对照 PDF 一起显示。</div><v-text-field v-model.number="pageButtonCount" class="mt-3" type="number" min="1" max="20" label="每组最多显示页码按钮数" hint="默认 5 个；超出后通过页码按钮两侧的箭头切换分组" persistent-hint /></div>
        <div class="settings-section"><div class="section-label">草稿与恢复</div><div class="shortcut-settings__hint">查看浏览器中自动保存的图纸工作区，可继续编辑或删除不需要的草稿。</div><v-btn block color="secondary" variant="tonal" class="mt-3" @click="$emit('open-drafts')">打开草稿</v-btn></div>
        <div class="settings-section"><div class="section-label">文件切换</div><v-switch v-model="clearReferences" color="secondary" hide-details label="上传设计图时清除已选对照文件" /><div class="shortcut-settings__hint mt-2">默认开启，避免新设计图误用上一个任务的对照 PDF；取消文件选择或选择无效文件不会清除。</div></div>
        <div class="settings-section"><div class="section-label">画布性能</div><v-select v-model="performanceMode" :items="canvasPerformanceOptions" label="清晰度与内存档位" hide-details class="mb-3" /><div class="canvas-profile-summary"><v-chip size="small" color="secondary" variant="tonal">{{ canvasPerformanceProfile.label }}</v-chip><span>单画布最高 {{ Math.round(canvasPerformanceProfile.maxPixels / 1_000_000) }} 百万像素 · 位图缓存约 {{ Math.round(canvasPerformanceProfile.cacheBytes / 1024 / 1024) }} MB</span></div><div class="shortcut-settings__hint mt-2">自动模式依据设备内存、CPU 核心数和触控屏尺寸选择；也可手动固定档位，选择后立即生效。</div></div>
        <div class="settings-section"><div class="section-label">标识添加</div><v-text-field v-model.number="manualLeaderLength" type="number" min="20" max="240" step="4" label="新增标识引线长度" suffix="图纸单位" hint="仅影响之后通过 W、V、F、S 模式新增的标识；默认 72" persistent-hint /></div>
        <div class="settings-section"><div class="section-label">标识默认外观</div><div class="marker-appearance-card settings-marker-appearance"><div class="marker-appearance-tabs" role="tablist" aria-label="默认标识外观分类"><button v-for="group in markerAppearanceGroups" :key="group.key" type="button" role="tab" :aria-selected="appearanceTab === group.key" :class="{ active: appearanceTab === group.key }" @click="appearanceTab = group.key"><i :style="{ backgroundColor: group.style.color }" />{{ group.title.replace('标识', '') }}</button></div><div class="marker-appearance-panel" role="tabpanel"><div class="marker-appearance-heading"><span><i :style="{ backgroundColor: activeAppearanceGroup.style.color }" /><strong>{{ activeAppearanceGroup.title }}</strong></span><small>{{ activeAppearanceGroup.subtitle }}</small></div><div class="marker-appearance-form"><div class="parameter-grid"><v-select v-model="activeAppearanceGroup.style.shape" :items="shapeOptions" label="外形" /><v-text-field v-model.number="activeAppearanceGroup.style.frameSize" type="number" min="18" max="64" label="框尺寸" /></div><div class="parameter-grid mt-3"><v-text-field v-model.number="activeAppearanceGroup.style.fontSize" type="number" min="7" max="24" label="字号" /><v-text-field v-model="activeAppearanceGroup.style.color" type="color" label="颜色" /></div></div></div></div></div>
        <div class="settings-section shortcut-settings"><div class="section-label">快捷键</div><div class="shortcut-settings__hint">修饰键和主按键可分开设置。选中“按键”输入框后按下目标键，Backspace 可清空主按键。</div><div class="shortcut-editor-list mt-3"><div v-for="row in shortcutRows" :key="row.action" class="shortcut-editor-row"><span class="shortcut-editor-row__label">{{ row.label }}</span><v-select :model-value="shortcutEditors[row.action].modifier" :items="shortcutModifierOptions" label="修饰键" density="compact" hide-details @update:model-value="$emit('update-shortcut-modifier', row.action, $event)" /><v-text-field :model-value="shortcutEditors[row.action].key" label="按键" density="compact" hide-details readonly @keydown="$emit('capture-shortcut-key', $event, row.action)" /></div></div><v-alert v-if="shortcutConflict" type="warning" variant="tonal" density="compact" class="mt-3">{{ shortcutConflict }}</v-alert><v-btn block variant="outlined" class="mt-3" @click="$emit('restore-default-shortcuts')">恢复默认快捷键</v-btn></div>
        <v-btn block color="primary" variant="tonal" class="settings-config-button" @click="open = false; $emit('open-symbol-config')">打开焊口符号研究配置</v-btn>
      </v-card-text>
      <v-card-actions class="settings-actions"><v-spacer /><v-btn color="primary" @click="open = false">完成</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
