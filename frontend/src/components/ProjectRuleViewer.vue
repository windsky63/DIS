<script setup>
import { computed } from 'vue'
import { EP3D_FIXED_PROFILE, EP3D_COMPONENT_RULES, DESIGN_COMPONENT_RULES, designRuleParameters } from '../recognitionRuleDisplay.js'

const props = defineProps({ config: { type: Object, required: true }, projects: { type: Array, required: true },
  activeProjectId: String, referenceFiles: { type: Array, default: () => [] } })
const emit = defineEmits(['switchProject', 'switchRule', 'assignReferenceRule'])
const activeProject = computed(() => props.projects.find(project => project.id === props.activeProjectId))
const activeRule = computed(() => props.config.referenceRules.find(rule => rule.id === props.config.activeReferenceRuleId))
const ruleItems = computed(() => props.config.referenceRules.map(rule => ({
  title: `${rule.name} · ${rule.kind === 'ep3d' ? 'EP3D' : '施工单位'}`, value: rule.id,
})))
const rootLabels = { 'solid-dot-required': '必须有实体焊口黑点', 'leader-end-on-process': '允许引线末端靠近管线', 'symbol-or-process-end': '黑点或管线附近端点' }
const designParameters = computed(() => designRuleParameters(props.config))
const parameters = computed(() => {
  const rule = activeRule.value
  if (!rule) return []
  const options = rule.options
  if (rule.kind === 'ep3d') return [['焊口端点策略', rootLabels[options.rootStrategy || 'solid-dot-required'] || options.rootStrategy],
    ['固定适配器', EP3D_FIXED_PROFILE.name], ['标识框形状', EP3D_FIXED_PROFILE.containerShapes],
    ['引线长度范围', `${EP3D_FIXED_PROFILE.minimumLeaderLength}–${EP3D_FIXED_PROFILE.maximumLeaderLength} pt`],
    ['标识框最小跨度', `${EP3D_FIXED_PROFILE.minimumContainerExtent} pt`],
    ['标识框最大宽度', `${EP3D_FIXED_PROFILE.maximumContainerWidth} pt`], ['标识框最大高度', `${EP3D_FIXED_PROFILE.maximumContainerHeight} pt`]]
  return [
    ['圆框到引线最大间隙', `${options.maximumFrameGap} pt`], ['引线分段连接间隙', `${options.segmentGap} pt`],
    ['圆框直径范围', `${options.minimumFrameDiameter}–${options.maximumFrameDiameter} pt`],
    ['引线段长度范围', `${options.minimumLeaderLength}–${options.maximumLeaderLength} pt`],
    ['最多引线段数', options.maximumLeaderSegments], ['引线颜色', options.leaderColor === 'red' ? '仅红色' : '红色或深色'],
    ['红色通道最低值', options.redMinimum], ['绿 / 蓝通道最高值', options.otherColorMaximum],
  ]
})
</script>

<template>
  <section class="project-rule-viewer">
    <div class="project-rule-selectors">
      <v-select :model-value="activeProjectId" :items="projects" item-title="name" item-value="id" label="当前项目" variant="outlined" density="comfortable" hide-details @update:model-value="emit('switchProject', $event)" />
      <v-select :model-value="config.activeReferenceRuleId" :items="ruleItems" label="默认对照来源 / 查看规则" variant="outlined" density="comfortable" hide-details @update:model-value="emit('switchRule', $event)" />
    </div>
    <p v-if="activeProject?.description" class="project-rule-description">{{ activeProject.description }}</p>
    <details :key="`design-${activeProjectId}`" class="project-rule-section project-rule-collapsible" aria-label="设计图识别规则">
      <summary class="project-rule-section-heading"><span class="project-rule-section-title">设计图识别规则</span><span class="project-rule-summary-meta"><v-chip size="small" variant="tonal">只读</v-chip><svg class="project-rule-chevron" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg></span></summary>
      <div class="project-rule-section-body">
      <dl class="project-rule-parameters"><div v-for="[label, value] in designParameters" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
      <dl class="project-rule-descriptions"><div v-for="[label, value] in DESIGN_COMPONENT_RULES" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
      <p class="project-rule-footnote">展示当前配置及后端默认值；组件识别说明为后端固定规则，不可在前端修改。</p>
      <details class="project-rule-raw"><summary>查看设计图原始配置</summary><pre>{{ JSON.stringify({ detectionMode: config.detectionMode || 'placement', placementSymbols: config.placementSymbols, fixedSymbolPolicy: config.fixedSymbolPolicy, markerPolicy: config.markerPolicy, minimumConfidence: config.minimumConfidence, comparisonSymbols: config.comparisonSymbols }, null, 2) }}</pre></details>
      </div>
    </details>
    <details v-if="activeRule" :key="`reference-${activeProjectId}-${activeRule.id}`" class="project-rule-section project-rule-collapsible" aria-label="对照图识别规则">
      <summary class="project-rule-section-heading"><span class="project-rule-section-title">对照图识别规则 · {{ activeRule.name }}</span><span class="project-rule-summary-meta"><v-chip size="small" variant="tonal" color="secondary">{{ activeRule.kind === 'ep3d' ? 'EP3D 出图' : '施工单位出图' }}</v-chip><svg class="project-rule-chevron" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg></span></summary>
      <div class="project-rule-section-body">
      <div class="project-rule-pattern"><span>编号正则表达式</span><code>{{ activeRule.options.labelPattern }}</code></div>
      <p v-if="activeRule.kind === 'contractor'" class="project-rule-note">识别红色圆圈内编号，允许圆框与引线分离；引线末端直接作为焊口坐标，不要求接触管线或实体焊口。</p>
      <dl class="project-rule-parameters"><div v-for="[label, value] in parameters" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
      <template v-if="activeRule.kind === 'ep3d'">
        <dl class="project-rule-descriptions"><div v-for="[label, value] in EP3D_COMPONENT_RULES" :key="label"><dt>{{ label }}</dt><dd>{{ value }}</dd></div></dl>
        <p class="project-rule-footnote">编号表达式与焊口端点策略为项目配置项；框形、尺寸、引线长度及组件规则为后端固定值。长度阈值随页面矢量比例自适应，以上为基础值。默认读取 PDF 原生文字和矢量图元，扫描图或转曲编号需另行接入 OCR。</p>
      </template>
      <details class="project-rule-raw"><summary>查看出图原始配置</summary><pre>{{ JSON.stringify(activeRule, null, 2) }}</pre></details>
      <p v-if="activeRule.kind === 'contractor'" class="project-rule-footnote">pt 为 PDF 点，1 pt = 1/72 英寸。</p>
      </div>
    </details>
    <section v-if="referenceFiles.length" class="project-rule-section" aria-label="对照文件来源">
      <div class="project-rule-section-heading"><h3>逐文件指定出图来源</h3></div>
      <p class="project-rule-note">未指定时使用默认规则。这里只切换使用的来源，不修改规则参数。</p>
      <div class="project-rule-files"><div v-for="(file, index) in referenceFiles" :key="file.weldMarkerReferenceIndex ?? index" class="project-rule-file">
        <span class="project-rule-filename" :title="file.name">{{ file.name || `对照文件 ${index + 1}` }}</span>
        <v-select :model-value="config.referenceRuleAssignments[String(file.weldMarkerReferenceIndex ?? index)]" :items="ruleItems" label="出图来源" placeholder="使用默认规则" variant="outlined" density="compact" hide-details clearable @update:model-value="emit('assignReferenceRule', { slot: String(file.weldMarkerReferenceIndex ?? index), ruleId: $event })" />
      </div></div>
    </section>
    <p class="project-rule-footnote">前端仅支持切换和查看规则，不提供新增、编辑或删除。规则切换仅影响后续解析，已有结果需重新解析。</p>
  </section>
</template>
