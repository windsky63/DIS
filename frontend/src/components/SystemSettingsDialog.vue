<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  tutorialSection: { type: String, default: '' },
  themeOptions: { type: Array, default: () => [] },
  canvasPerformanceOptions: { type: Array, default: () => [] },
  canvasPerformanceProfiles: { type: Array, default: () => [] },
  canvasPerformanceProfile: { type: Object, required: true },
  markerAppearanceGroups: { type: Array, default: () => [] },
  activeAppearanceGroup: { type: Object, required: true },
  shapeOptions: { type: Array, default: () => [] },
  shortcutRows: { type: Array, default: () => [] },
  shortcutEditors: { type: Object, required: true },
  shortcutModifierOptions: { type: Array, default: () => [] },
  shortcutConflict: { type: String, default: '' },
  headerButtonOptions: { type: Array, default: () => [] },
  drawerSizeOptions: { type: Array, default: () => [] },
  messageLocationOptions: { type: Array, default: () => [] },
})

const themePreference = defineModel('themePreference', { type: String, required: true })
const open = defineModel({ type: Boolean, required: true })
const leftDrawer = defineModel('leftDrawer', { type: Boolean, required: true })
const reviewDrawer = defineModel('reviewDrawer', { type: Boolean, required: true })
const leftDrawerSize = defineModel('leftDrawerSize', { type: String, required: true })
const reviewDrawerSize = defineModel('reviewDrawerSize', { type: String, required: true })
const tooltips = defineModel('tooltips', { type: Boolean, required: true })
const operationMessages = defineModel('operationMessages', { type: Boolean, required: true })
const errorMessages = defineModel('errorMessages', { type: Boolean, required: true })
const messageLocation = defineModel('messageLocation', { type: String, required: true })
const pageButtonCount = defineModel('pageButtonCount', { type: Number, required: true })
const clearReferences = defineModel('clearReferences', { type: Boolean, required: true })
const autoReferenceWindow = defineModel('autoReferenceWindow', { type: Boolean, required: true })
const referenceHintLocation = defineModel('referenceHintLocation', { type: String, required: true })
const performanceMode = defineModel('performanceMode', { type: String, required: true })
const autoFitPdf = defineModel('autoFitPdf', { type: Boolean, required: true })
const appearanceTab = defineModel('appearanceTab', { type: String, required: true })
const manualAddInteraction = defineModel('manualAddInteraction', { type: String, required: true })
const manualAddInteractionOptions = [
  { title: '按键新增（默认）', value: 'keyboard' },
  { title: '拖拽新增', value: 'drag' },
]
const manualLeaderLength = defineModel('manualLeaderLength', { type: Number, required: true })
const headerButtons = defineModel('headerButtons', { type: Object, required: true })
const referenceHintLocationOptions = [
  { title: '对照图窗口（默认）', value: 'reference' },
  { title: '设计图窗口', value: 'design' },
]
const emit = defineEmits(['open-drafts', 'update-shortcut-modifier', 'capture-shortcut-key', 'restore-default-shortcuts', 'auto-reference-window-change', 'reference-hint-location-change', 'normalize-numbering-defaults', 'restore-system-defaults'])
const restoreConfirmOpen = ref(false)
const activeSection = ref('')
const settingsSections = [
  { key: 'interface', icon: '◫', title: '界面与 Header', description: '主题、面板、消息提示以及顶部按钮显示。' },
  { key: 'workspace', icon: '▤', title: '工作区与文件', description: '草稿、文件切换、分页、对照图与画布性能。' },
  { key: 'markers', icon: '◇', title: '标识默认设置', description: '各类新增标识的外观、编号规则和引线。' },
  { key: 'shortcuts', icon: '⌨', title: '快捷键', description: '调整工作台常用操作的键盘快捷方式。' },
  { key: 'other', icon: '•••', title: '其他设置', description: '恢复系统默认设置及其他辅助设置。' },
]
const shortcutGroups = computed(() => {
  const groups = new Map()
  props.shortcutRows.forEach(row => {
    const title = row.group || '其他'
    if (!groups.has(title)) groups.set(title, [])
    groups.get(title).push(row)
  })
  return [...groups].map(([title, rows]) => ({ title, rows }))
})

function confirmRestoreDefaults() {
  restoreConfirmOpen.value = false
  emit('restore-system-defaults')
}
watch([open, () => props.tutorialSection], ([value, section]) => {
  if (value) activeSection.value = settingsSections.some(item => item.key === section) ? section : ''
})
</script>

<template>
  <v-dialog v-model="open" max-width="760">
    <v-card data-tour="system-settings-dialog" class="settings-card">
      <div class="draft-manager-header settings-header"><span class="draft-manager-header__icon settings-header__icon" aria-hidden="true">⚙</span><div><v-card-title>{{ activeSection ? settingsSections.find(item => item.key === activeSection)?.title : '系统设置' }}</v-card-title><v-card-subtitle>{{ activeSection ? '修改后立即生效并自动保存。' : '选择一个设置类型。' }}</v-card-subtitle></div><v-spacer /><v-btn v-if="activeSection" class="settings-back-button" icon size="small" variant="text" aria-label="返回设置类型" @click="activeSection = ''"><span aria-hidden="true">‹</span><v-tooltip activator="parent">返回设置类型</v-tooltip></v-btn></div>
      <v-card-text v-if="!activeSection" class="settings-type-grid">
        <button v-for="section in settingsSections" :key="section.key" type="button" class="settings-type-item" @click="activeSection = section.key">
          <span class="settings-type-item__icon" aria-hidden="true">{{ section.icon }}</span>
          <span><strong>{{ section.title }}</strong><small>{{ section.description }}</small></span>
          <i aria-hidden="true">›</i>
        </button>
      </v-card-text>
      <v-card-text v-else data-tour="system-settings-content" class="settings-content">
        <template v-if="activeSection === 'interface'">
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">主题与配色</span><h3>界面主题</h3></div><p>主题会同步应用到工作台、后台和独立对照图窗口。</p></div>
            <v-select v-model="themePreference" :items="themeOptions" label="界面主题" hide-details />
          </section>
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">工作区域</span><h3>界面布局</h3></div><p>分别控制左右区域是否显示以及展开后的宽度。</p></div>
            <div class="settings-control-grid"><v-select v-model="leftDrawerSize" :items="drawerSizeOptions" label="左侧区域宽度" hide-details /><v-select v-model="reviewDrawerSize" :items="drawerSizeOptions" label="右侧区域宽度" hide-details /></div>
            <div class="settings-switch-grid"><v-switch v-model="leftDrawer" color="secondary" hide-details label="显示识别输入与操作区域" /><v-switch v-model="reviewDrawer" color="secondary" hide-details label="显示拓扑标识辅助区域" /></div>
          </section>
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">反馈方式</span><h3>交互提示</h3></div><p>控制辅助说明和操作结果的显示方式。</p></div>
            <div class="settings-switch-grid settings-switch-grid--three"><v-switch v-model="tooltips" color="secondary" hide-details label="Tooltip 提示" /><v-switch v-model="operationMessages" color="secondary" hide-details label="操作信息" /><v-switch v-model="errorMessages" color="secondary" hide-details label="错误信息" /></div>
            <v-select v-model="messageLocation" class="mt-3" :items="messageLocationOptions" label="信息提示位置" hide-details />
          </section>
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">顶部区域</span><h3>Header 按钮</h3></div><p>系统设置入口始终显示，其余入口可按需要隐藏。</p></div>
            <div class="header-button-settings"><v-switch v-for="item in headerButtonOptions" :key="item.key" v-model="headerButtons[item.key]" color="secondary" hide-details :label="item.label"><v-tooltip activator="parent">{{ item.description }}</v-tooltip></v-switch></div>
          </section>
        </template>
        <template v-if="activeSection === 'workspace'">
          <section class="settings-section settings-action-section">
            <div class="settings-section-heading"><div><span class="section-label">本地数据</span><h3>草稿与恢复</h3></div><p>查看浏览器中保存的工作区，继续编辑或删除不再需要的草稿。</p></div>
            <v-btn color="secondary" variant="tonal" @click="$emit('open-drafts')">打开草稿管理器</v-btn>
          </section>
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">输入管理</span><h3>文件与对照图</h3></div><p>避免不同任务之间误用旧对照文件，并设置分屏提示所在窗口。</p></div>
            <div class="settings-switch-grid"><v-switch v-model="clearReferences" color="secondary" hide-details label="上传设计图时清除已选对照文件" /><v-switch v-model="autoReferenceWindow" color="secondary" hide-details label="自动使用独立对照图窗口" @update:model-value="$emit('auto-reference-window-change', $event)" /></div>
            <v-select v-model="referenceHintLocation" class="mt-3" :items="referenceHintLocationOptions" label="分屏时对照图提示显示位置" hide-details @update:model-value="$emit('reference-hint-location-change', $event)" />
          </section>
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">页面导航</span><h3>分页显示</h3></div><p>页码超过当前分组数量后，可使用两侧箭头切换分组。</p></div>
            <v-text-field v-model.number="pageButtonCount" type="number" min="1" max="20" label="每组最多显示页码按钮数" hide-details />
          </section>
          <section class="settings-section">
            <div class="settings-section-heading"><div><span class="section-label">渲染资源</span><h3>画布性能</h3></div><p>档位越高，放大后的图纸越清晰，同时需要更多内存。</p></div>
            <div class="settings-control-grid"><v-select v-model="performanceMode" :items="canvasPerformanceOptions" label="清晰度与内存档位" hide-details /><v-switch v-model="autoFitPdf" color="secondary" hide-details label="自动适应 PDF 大小" /></div>
            <div class="canvas-profile-summary"><v-chip size="small" color="secondary" variant="tonal">当前：{{ canvasPerformanceProfile.label }}</v-chip><span>单画布最高 {{ Math.round(canvasPerformanceProfile.maxPixels / 1_000_000) }} 百万像素 · 位图缓存约 {{ Math.round(canvasPerformanceProfile.cacheBytes / 1024 / 1024) }} MB</span></div>
            <div class="performance-guidance-grid" aria-label="画布性能档位内存建议">
              <article v-for="profile in canvasPerformanceProfiles" :key="profile.key" class="performance-guidance-card" :class="{ 'performance-guidance-card--active': profile.key === canvasPerformanceProfile.key }">
                <div><strong>{{ profile.label }}</strong><span>{{ Math.round(profile.maxPixels / 1_000_000) }} 百万像素</span></div>
                <dl><div><dt>建议设备内存</dt><dd>{{ profile.recommendedDeviceMemoryGb }} GB</dd></div><div><dt>建议可用内存</dt><dd>{{ profile.recommendedAvailableMemoryGb }} GB</dd></div></dl>
              </article>
            </div>
            <div class="settings-note">自动模式依据设备内存档位、CPU 核心数和触控屏尺寸选择。浏览器无法可靠读取整台机器的实时可用内存，因此“建议可用内存”仅用于配置参考。</div>
          </section>
        </template>
        <div v-if="activeSection === 'markers'" class="settings-section">
          <div class="settings-section-heading"><div><span class="section-label">新建任务默认值</span><h3>标识默认设置</h3></div><p>按标识类型分别设置外观和新增编号规则，修改后同步应用到当前任务。</p></div>
          <section class="settings-form-group mb-4">
            <div class="settings-form-group__heading"><strong>标识编辑操作方式</strong><span>适用于焊口、阀门、法兰、支架和其他标识。</span></div>
            <v-select v-model="manualAddInteraction" :items="manualAddInteractionOptions" label="新增方式" hide-details />
            <div class="settings-note">{{ manualAddInteraction === 'drag' ? '进入编辑模式后，按住左键从引线定位点拖向标识框位置，松开新增。引线方向和长度由拖拽决定；使用中键平移画布。' : '进入编辑模式后，将鼠标移到定位点并按新增快捷键（默认 A）。左键可拖动画布。' }}</div>
          </section>
          <div class="marker-appearance-card settings-marker-appearance">
            <div class="marker-appearance-tabs" role="tablist" aria-label="默认标识外观分类">
              <button v-for="group in markerAppearanceGroups" :key="group.key" type="button" role="tab" :aria-selected="appearanceTab === group.key" :class="{ active: appearanceTab === group.key }" @click="appearanceTab = group.key"><i :style="{ backgroundColor: group.style.color }" />{{ group.title.replace('标识', '') }}</button>
            </div>
            <div class="marker-appearance-panel" role="tabpanel">
              <div class="marker-appearance-heading"><span><i :style="{ backgroundColor: activeAppearanceGroup.style.color }" /><strong>{{ activeAppearanceGroup.title }}</strong></span><small>{{ activeAppearanceGroup.subtitle }}</small></div>
              <div class="marker-appearance-form settings-marker-form">
                <section class="settings-form-group"><div class="settings-form-group__heading"><strong>外观</strong><span>控制标识框、文字和填充效果。</span></div><div class="settings-control-grid"><v-select v-model="activeAppearanceGroup.style.shape" :items="shapeOptions" label="外形" hide-details /><v-text-field v-model.number="activeAppearanceGroup.style.frameSize" type="number" min="18" max="64" label="框尺寸" hide-details /><v-text-field v-model.number="activeAppearanceGroup.style.fontSize" type="number" min="7" max="24" label="字号" hide-details /></div><div class="settings-control-grid mt-3"><v-text-field v-model.number="activeAppearanceGroup.style.lineWidth" type="number" min="0.5" max="4" step="0.1" label="线宽" hide-details /><v-text-field v-model="activeAppearanceGroup.style.color" type="color" label="颜色" hide-details /></div><v-slider v-model="activeAppearanceGroup.style.fillOpacity" min="0" max="1" step="0.05" color="accent" label="填充透明度" thumb-label hide-details class="mt-4" /></section>
                <template v-if="activeAppearanceGroup.numbering">
                  <section class="settings-form-group"><div class="settings-form-group__heading"><strong>新增编号规则</strong><span>只影响之后人工新增的同类标识。</span></div>
                  <template v-if="activeAppearanceGroup.style.shape === 'split-circle'">
                    <div class="split-numbering-sections">
                      <section class="split-numbering-section"><div class="section-label">上栏</div><div class="parameter-grid"><v-text-field v-model="activeAppearanceGroup.numbering.prefix" maxlength="20" label="上栏前缀" hide-details @blur="$emit('normalize-numbering-defaults')" /><v-text-field v-model="activeAppearanceGroup.numbering.suffix" maxlength="20" label="上栏后缀" hide-details @blur="$emit('normalize-numbering-defaults')" /></div><v-text-field v-model.number="activeAppearanceGroup.numbering.start" class="mt-3" type="number" min="1" step="1" label="上栏起始数字" hide-details @blur="$emit('normalize-numbering-defaults')" /></section>
                      <section class="split-numbering-section"><div class="section-label">下栏</div><div class="parameter-grid"><v-text-field v-model="activeAppearanceGroup.numbering.secondaryPrefix" maxlength="20" label="下栏前缀" hide-details @blur="$emit('normalize-numbering-defaults')" /><v-text-field v-model="activeAppearanceGroup.numbering.secondarySuffix" maxlength="20" label="下栏后缀" hide-details @blur="$emit('normalize-numbering-defaults')" /></div><v-text-field v-model.number="activeAppearanceGroup.numbering.secondaryStart" class="mt-3" type="number" min="1" step="1" label="下栏起始数字" hide-details @blur="$emit('normalize-numbering-defaults')" /></section>
                    </div>
                  </template>
                  <template v-else>
                    <div class="settings-control-grid settings-control-grid--three"><v-text-field v-model="activeAppearanceGroup.numbering.prefix" maxlength="20" label="前缀" hide-details @blur="$emit('normalize-numbering-defaults')" /><v-text-field v-model="activeAppearanceGroup.numbering.suffix" maxlength="20" label="后缀" hide-details @blur="$emit('normalize-numbering-defaults')" /><v-text-field v-model.number="activeAppearanceGroup.numbering.start" type="number" min="1" step="1" label="起始数字" hide-details @blur="$emit('normalize-numbering-defaults')" /></div>
                  </template>
                  </section>
                </template>
                <section v-if="manualAddInteraction === 'keyboard'" class="settings-form-group"><div class="settings-form-group__heading"><strong>新增标识引线</strong><span>系统默认 72，修改后同时应用到当前任务。</span></div><v-text-field v-model.number="manualLeaderLength" type="number" min="20" max="240" step="4" label="引线长度" suffix="图纸单位" hide-details /></section>
              </div>
            </div>
          </div>
        </div>
        <div v-if="activeSection === 'shortcuts'" class="settings-section shortcut-settings"><div class="section-label">快捷键</div><div class="shortcut-settings__hint">修饰键和主按键可分开设置。选中“按键”输入框后按下目标键，Backspace 可清空主按键。</div><div class="shortcut-editor-list mt-3"><section v-for="group in shortcutGroups" :key="group.title" class="shortcut-editor-group"><h3>{{ group.title }}</h3><div v-for="row in group.rows" :key="row.action" class="shortcut-editor-row"><span class="shortcut-editor-row__label">{{ row.label }}</span><v-select :model-value="shortcutEditors[row.action].modifier" :items="shortcutModifierOptions" label="修饰键" density="compact" hide-details @update:model-value="$emit('update-shortcut-modifier', row.action, $event)" /><v-text-field :model-value="shortcutEditors[row.action].key" label="按键" density="compact" hide-details readonly @keydown="$emit('capture-shortcut-key', $event, row.action)" /></div></section></div><v-alert v-if="shortcutConflict" type="warning" variant="tonal" density="compact" class="mt-3">{{ shortcutConflict }}</v-alert><v-btn block variant="outlined" class="mt-3" @click="$emit('restore-default-shortcuts')">恢复默认快捷键</v-btn></div>
        <div v-if="activeSection === 'other'" class="settings-section settings-danger-section"><div class="settings-section-heading"><div><span class="section-label">危险操作</span><h3>恢复默认设置</h3></div><p>恢复主题、布局、Header、消息、分页、对照图、画布性能、标识与快捷键设置，不会删除业务数据。</p></div><v-btn color="error" variant="outlined" @click="restoreConfirmOpen = true">恢复系统默认设置</v-btn></div>
      </v-card-text>
      <v-card-actions class="settings-actions"><v-spacer /><v-btn color="primary" @click="open = false">完成</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
  <v-dialog v-model="restoreConfirmOpen" max-width="460">
    <v-card>
      <v-card-title>确认恢复系统默认设置？</v-card-title>
      <v-card-text>所有常态化设置将恢复为系统默认值，标识默认设置也会立即同步到当前任务。任务、草稿和业务数据不会被删除。</v-card-text>
      <v-card-actions><v-spacer /><v-btn variant="text" @click="restoreConfirmOpen = false">取消</v-btn><v-btn color="error" variant="flat" @click="confirmRestoreDefaults">确认恢复</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
