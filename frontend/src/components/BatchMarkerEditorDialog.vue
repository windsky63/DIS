<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { BATCH_MARKER_TYPES } from '../batchMarkerEdits.js'

const props = defineProps({ editor: { type: Object, required: true }, taskName: { type: String, default: '' }, tutorialColumns: { type: String, default: '' }, tutorialTools: { type: Boolean, default: false } })
const {
  open, loading, saving, rows, selected, messages, progress, success, search, typeFilter, pageFilter,
  tablePage, pageCount, pageOptions, pageRangeStart, pageRangeEnd, pageRangeError, pageBounds, visibleRows, filteredRows, changedRows, selectedCount, busy,
  close, save, updateRow, toggleRow, toggleFiltered, restoreChanges, numberSelected, setSelectedFields,
} = props.editor
const columns = ref('content')
watch(() => props.tutorialColumns, value => {
  if (['content', 'appearance'].includes(value)) columns.value = value
}, { immediate: true })
const discardAction = ref('')
const numbering = reactive({ prefix: '', suffix: '', start: 1, step: 1 })
const appearance = reactive({ shape: '', color: '', frameSize: '', fontSize: '', lineWidth: '', fillOpacity: '' })
const common = reactive({ secondaryText: '', referenceLabel: '', included: '' })
const shapes = [
  { title: '圆形', value: 'circle' }, { title: '方形框', value: 'rectangle' },
  { title: '分栏圆形', value: 'split-circle' }, { title: '菱形', value: 'diamond' },
]
const typeOptions = [{ title: '全部类型', value: 'all' }, ...BATCH_MARKER_TYPES]
const bulkShapeOptions = [{ title: '保持不变', value: '' }, ...shapes]
const participationOptions = [{ title: '保持不变', value: '' }, { title: '参与', value: 'yes' }, { title: '排除', value: 'no' }]
const allFilteredSelected = computed(() => filteredRows.value.length > 0 && filteredRows.value.every(row => selected.value.has(row.key)))
const someFilteredSelected = computed(() => filteredRows.value.some(row => selected.value.has(row.key)))
watch([search, typeFilter, pageFilter, pageRangeStart, pageRangeEnd], () => { tablePage.value = 1 })
function requestClose() {
  if (saving.value) return
  if (changedRows.value.length) discardAction.value = 'close'
  else close()
}
function discard() {
  discardAction.value = ''
  restoreChanges()
  close()
}
function applyCommon() {
  setSelectedFields({ ...common, included: common.included === '' ? null : common.included === 'yes' })
}
</script>

<template>
  <v-dialog :model-value="open" max-width="1600" :persistent="saving" class="batch-marker-dialog" @update:model-value="value => { if (!value) requestClose() }">
    <v-card data-tour="batch-dialog" class="batch-marker-card settings-card" @keydown.esc.stop.prevent="requestClose" @keydown.ctrl.s.stop.prevent="save">
      <div class="draft-manager-header batch-marker-header">
        <span class="draft-manager-header__icon settings-header__icon" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 10v10M15 10v10M3 15h18" /></svg></span>
        <div class="batch-marker-header-copy"><v-card-title>批量修改标识</v-card-title><v-card-subtitle>{{ taskName || '当前任务' }} · 全部页面</v-card-subtitle></div>
        <v-spacer />
        <v-btn icon size="small" variant="text" class="batch-marker-close" aria-label="关闭批量修改" :disabled="saving" @click="requestClose"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></v-btn>
      </div>
      <v-progress-linear v-if="busy" :model-value="progress.total ? progress.completed / progress.total * 100 : 0" :indeterminate="!progress.total" color="secondary" height="3" />
      <div data-tour="batch-filters" class="batch-marker-filters" :class="{ 'batch-marker-filters-range': pageFilter === 'range' }">
        <v-text-field v-model="search" density="compact" hide-details clearable label="搜索编号、下栏或对照编号" :disabled="busy" @update:model-value="search = $event || ''" />
        <v-select v-model="pageFilter" :items="pageOptions" density="compact" hide-details label="页面" :disabled="busy" />
        <v-select v-model="typeFilter" :items="typeOptions" density="compact" hide-details label="类型" :disabled="busy" />
        <template v-if="pageFilter === 'range'">
          <v-text-field v-model="pageRangeStart" type="number" label="起始页" :min="pageBounds.min" :max="pageBounds.max" step="1" density="compact" variant="outlined" hide-details :error="Boolean(pageRangeError)" :disabled="busy" />
          <v-text-field v-model="pageRangeEnd" type="number" label="结束页" :min="pageBounds.min" :max="pageBounds.max" step="1" density="compact" variant="outlined" hide-details :error="Boolean(pageRangeError)" :disabled="busy" />
          <div v-if="pageRangeError" class="batch-marker-page-range-error" role="alert">{{ pageRangeError }}</div>
        </template>
      </div>
      <div class="batch-marker-selection">
        <div class="batch-marker-counts" role="status" aria-label="标识数量统计" aria-live="polite">
          <div class="batch-marker-stat"><span>全部</span><strong>{{ rows.length }}<small>个</small></strong></div>
          <div class="batch-marker-stat"><span>筛选结果</span><strong>{{ filteredRows.length }}<small>个</small></strong></div>
          <div class="batch-marker-stat batch-marker-stat-selected"><span>已选</span><strong>{{ selectedCount }}<small>个</small></strong></div>
        </div>
        <div class="batch-marker-selection-controls">
          <v-btn-toggle v-model="columns" class="batch-marker-view-toggle" mandatory density="compact" color="secondary" variant="outlined" :disabled="busy">
            <v-btn value="content" size="small">内容与状态</v-btn><v-btn value="appearance" size="small">标识外观</v-btn>
          </v-btn-toggle>
        </div>
      </div>
      <details data-tour="batch-tools" class="batch-marker-tools" :open="tutorialTools || undefined">
        <summary>统一修改选中标识 <span>{{ selectedCount ? `已选 ${selectedCount} 个` : '先勾选表格中的标识' }}</span></summary>
        <div class="batch-marker-tool-content">
          <template v-if="columns === 'content'">
            <div class="batch-marker-tool-row">
              <strong>连续编号</strong>
              <label>前缀<input v-model="numbering.prefix" maxlength="100" :disabled="busy" placeholder="可留空" /></label>
              <label>起始数字<input v-model="numbering.start" type="number" min="0" step="1" :disabled="busy" /></label>
              <label>步长<input v-model="numbering.step" type="number" min="1" step="1" :disabled="busy" /></label>
              <label>后缀<input v-model="numbering.suffix" maxlength="100" :disabled="busy" placeholder="可留空" /></label>
              <v-btn size="small" color="secondary" variant="tonal" :disabled="busy || !selectedCount" @click="numberSelected(numbering)">填入选中行</v-btn>
            </div>
            <div class="batch-marker-tool-row">
              <strong>统一内容</strong>
              <label>下栏内容<input v-model="common.secondaryText" maxlength="200" :disabled="busy" placeholder="留空不改变" /></label>
              <label>对照编号<input v-model="common.referenceLabel" maxlength="200" :disabled="busy" placeholder="留空不改变" /></label>
              <div class="batch-marker-tool-field"><span>参与标识</span><v-select v-model="common.included" :items="participationOptions" density="compact" variant="outlined" hide-details :disabled="busy" aria-label="选中标识参与状态" /></div>
              <v-btn size="small" color="secondary" variant="tonal" :disabled="busy || !selectedCount" @click="applyCommon">填入选中行</v-btn>
            </div>
          </template>
          <div v-else class="batch-marker-tool-row batch-marker-tool-row-appearance">
            <strong>统一外观</strong>
            <div class="batch-marker-tool-field"><span>外形</span><v-select v-model="appearance.shape" :items="bulkShapeOptions" density="compact" variant="outlined" hide-details :disabled="busy" aria-label="选中标识外形" /></div>
            <label>颜色<input v-model="appearance.color" :disabled="busy" placeholder="如 #1769d2" maxlength="7" /></label>

            <label>框尺寸<input v-model="appearance.frameSize" type="number" min="18" max="64" :disabled="busy" placeholder="不改变" /></label>
            <label>字号<input v-model="appearance.fontSize" type="number" min="7" max="24" :disabled="busy" placeholder="不改变" /></label>
            <label>线宽<input v-model="appearance.lineWidth" type="number" min="0.5" max="4" step="0.1" :disabled="busy" placeholder="不改变" /></label>
            <label>填充透明度<input v-model="appearance.fillOpacity" type="number" min="0" max="1" step="0.05" :disabled="busy" placeholder="不改变" /></label>
            <v-btn size="small" color="secondary" variant="tonal" :disabled="busy || !selectedCount" @click="setSelectedFields(appearance)">填入选中行</v-btn>
          </div>
        </div>
      </details>
      <div v-if="messages.length || success" class="batch-marker-messages" role="status" aria-live="polite">
        <v-alert v-if="success" type="success" variant="tonal" density="compact">{{ success }}</v-alert>
        <v-alert v-if="messages.length" type="warning" variant="tonal" density="compact"><div v-for="(message, index) in messages.slice(0, 5)" :key="index">{{ message }}</div><div v-if="messages.length > 5">另有 {{ messages.length - 5 }} 项提示，可在对应行查看。</div></v-alert>
      </div>
      <div data-tour="batch-table" class="batch-marker-table-wrap">
        <table class="batch-marker-table">
          <thead><tr>
            <th class="batch-check"><input type="checkbox" :checked="allFilteredSelected" :indeterminate="someFilteredSelected && !allFilteredSelected" :disabled="busy || !filteredRows.length" aria-label="选择全部筛选结果" @change="toggleFiltered($event.target.checked)" /></th>
            <th>图纸页</th><th>类型</th><th>编号 / 上栏</th>
            <template v-if="columns === 'content'"><th>下栏内容</th><th>对照编号</th><th>参与标识</th></template>
            <template v-else><th>外形</th><th>颜色</th><th>框尺寸</th><th>字号</th><th>线宽</th><th>填充透明度</th></template>
            <th>修改状态</th>
          </tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.key" :class="{ 'batch-row-changed': row.changed, 'batch-row-selected': selected.has(row.key) }">
              <td class="batch-check"><input type="checkbox" :checked="selected.has(row.key)" :disabled="busy" :aria-label="`选择第 ${row.page} 页标识 ${row.values.number}`" @change="toggleRow(row, $event.target.checked)" /></td>
              <td class="batch-page">P{{ row.page }}</td><td class="batch-type">{{ row.typeTitle }}</td>
              <td><input class="batch-cell-input batch-number-input" :value="row.values.number" maxlength="200" :disabled="busy" :aria-label="`第 ${row.page} 页 ${row.id} 编号`" @input="updateRow(row, 'number', $event.target.value)" /></td>
              <template v-if="columns === 'content'">
                <td><input class="batch-cell-input" :value="row.values.secondaryText" maxlength="200" :disabled="busy || row.values.shape !== 'split-circle'" :aria-label="`第 ${row.page} 页 ${row.id} 下栏内容`" :placeholder="row.values.shape === 'split-circle' ? '下栏内容' : '非分栏外形'" @input="updateRow(row, 'secondaryText', $event.target.value)" /></td>
                <td><input class="batch-cell-input" :value="row.values.referenceLabel" maxlength="200" :disabled="busy" :aria-label="`第 ${row.page} 页 ${row.id} 对照编号`" @input="updateRow(row, 'referenceLabel', $event.target.value)" /></td>
                <td class="batch-check"><input type="checkbox" :checked="row.values.included" :disabled="busy" :aria-label="`第 ${row.page} 页 ${row.id} 参与标识`" @change="updateRow(row, 'included', $event.target.checked)" /></td>
              </template>
              <template v-else>
                <td><v-select class="batch-marker-shape-select" :model-value="row.values.shape" :items="shapes" density="compact" variant="outlined" hide-details color="secondary" :disabled="busy" :aria-label="`${row.id} 外形`" :menu-props="{ maxHeight: 280 }" @update:model-value="updateRow(row, 'shape', $event)" /></td>
                <td><input type="color" class="batch-color-input" :value="row.values.color" :disabled="busy" :aria-label="`${row.id} 颜色`" @input="updateRow(row, 'color', $event.target.value)" /></td>

                <td><input type="number" class="batch-cell-input batch-numeric-input" :value="row.values.frameSize" min="18" max="64" :disabled="busy" :aria-label="`${row.id} 框尺寸`" @input="updateRow(row, 'frameSize', $event.target.value === '' ? '' : Number($event.target.value))" /></td>
                <td><input type="number" class="batch-cell-input batch-numeric-input" :value="row.values.fontSize" min="7" max="24" :disabled="busy" :aria-label="`${row.id} 字号`" @input="updateRow(row, 'fontSize', $event.target.value === '' ? '' : Number($event.target.value))" /></td>
                <td><input type="number" class="batch-cell-input batch-numeric-input" :value="row.values.lineWidth" min="0.5" max="4" step="0.1" :disabled="busy" :aria-label="`${row.id} 线宽`" @input="updateRow(row, 'lineWidth', $event.target.value === '' ? '' : Number($event.target.value))" /></td>
                <td><input type="number" class="batch-cell-input batch-numeric-input" :value="row.values.fillOpacity" min="0" max="1" step="0.05" :disabled="busy" :aria-label="`${row.id} 填充透明度`" @input="updateRow(row, 'fillOpacity', $event.target.value === '' ? '' : Number($event.target.value))" /></td>
              </template>
              <td class="batch-row-status" :title="row.error || (row.lockOwner ? `${row.lockOwner} 正在核对此页` : '')"><span :class="{ 'batch-status-error': row.error, 'batch-status-changed': row.changed }">{{ row.error ? '保存失败' : (row.changed ? '待保存' : (row.lockOwner ? '核对中' : '未修改')) }}</span></td>
            </tr>
            <tr v-if="!visibleRows.length"><td :colspan="columns === 'content' ? 8 : 11" class="batch-empty">{{ loading ? `正在加载表格 ${progress.completed}/${progress.total} 页…` : '没有符合筛选条件的标识' }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="batch-marker-pagination">
        <v-pagination v-model="tablePage" :length="pageCount" :total-visible="5" density="compact" size="small" :disabled="busy" />
      </div>
      <v-card-actions data-tour="batch-footer" class="batch-marker-footer settings-actions">
        <div><strong>{{ changedRows.length }} 个标识待保存</strong></div>
        <div class="batch-marker-footer-actions"><v-btn color="primary" variant="outlined" :disabled="busy || !changedRows.length" @click="restoreChanges">还原未保存修改</v-btn><v-btn color="primary" variant="outlined" :disabled="loading || !changedRows.length" :loading="saving" @click="save">{{ saving ? `保存中 ${progress.completed}/${progress.total} 页` : '保存批量修改' }}</v-btn></div>
      </v-card-actions>
    </v-card>
  </v-dialog>
  <v-dialog :model-value="Boolean(discardAction)" max-width="440" persistent>
    <v-card><v-card-title>保留未保存的修改？</v-card-title><v-card-text>表格中还有 {{ changedRows.length }} 个标识未保存。继续操作会放弃这些修改，已保存的内容会保留。</v-card-text><v-card-actions><v-spacer /><v-btn variant="text" @click="discardAction = ''">继续编辑</v-btn><v-btn color="error" variant="text" @click="discard">放弃未保存修改</v-btn></v-card-actions></v-card>
  </v-dialog>
</template>

<style scoped>
.batch-marker-card { height: min(860px, calc(100vh - 48px)); display: flex; flex-direction: column; overflow: hidden; }
.batch-marker-header { flex-shrink: 0; }
.batch-marker-header-copy { min-width: 0; }.batch-marker-header-copy .v-card-subtitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.batch-marker-header svg, .batch-marker-close svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.batch-marker-filters { display: grid; grid-template-columns: minmax(180px, 2fr) repeat(2, minmax(120px, 1fr)); gap: 12px; padding: 16px 22px 12px; align-items: center; flex-shrink: 0; overflow-x: auto; }
.batch-marker-filters-range { grid-template-columns: minmax(180px, 2fr) repeat(2, minmax(120px, 1fr)) repeat(2, minmax(90px, .65fr)); }
.batch-marker-filters > .v-input { min-width: 0; }
.batch-marker-page-range-error { grid-column: 1 / -1; font-size: 12px; color: rgb(var(--v-theme-error)); }
.batch-marker-selection { display: grid; grid-template-columns: minmax(240px, 1fr) auto; align-items: center; gap: 12px; margin: 0 22px 12px; padding: 10px 12px; border: 1px solid rgb(var(--v-theme-outline)); border-radius: 8px; background: rgb(var(--v-theme-surface-muted)); flex-shrink: 0; }
.batch-marker-counts { display: flex; align-items: center; min-width: 0; }
.batch-marker-stat { display: flex; align-items: center; gap: 8px; padding: 0 14px; border-right: 1px solid rgb(var(--v-theme-outline)); white-space: nowrap; }.batch-marker-stat:first-child { padding-left: 0; }.batch-marker-stat:last-child { border-right: 0; padding-right: 0; }
.batch-marker-stat > span, .batch-marker-stat small { font-size: 12px; font-weight: 400; line-height: 1.5; letter-spacing: 0; color: rgb(var(--v-theme-on-surface-muted)); }.batch-marker-stat strong { display: flex; align-items: baseline; gap: 4px; font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }.batch-marker-stat-selected strong { color: rgb(var(--v-theme-secondary)); }
.batch-marker-selection :deep(.v-btn) { font-size: 12px; font-weight: 400; letter-spacing: 0; }
.batch-marker-selection-controls, .batch-marker-selection-actions { display: flex; align-items: center; gap: 6px; }.batch-marker-selection-controls { gap: 16px; }.batch-marker-view-toggle { flex-shrink: 0; border-radius: 6px; }
.batch-marker-tools { margin: 0 24px 12px; border: 1px solid rgba(var(--v-theme-on-surface), .12); border-radius: 8px; flex-shrink: 0; }
.batch-marker-tools summary { padding: 10px 14px; cursor: pointer; font-size: 13px; font-weight: 600; }.batch-marker-tools summary span { margin-left: 12px; font-weight: 400; opacity: .6; }
.batch-marker-tool-content { padding: 0 14px 14px; max-height: 260px; overflow-y: auto; }
.batch-marker-tool-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)) auto; align-items: end; gap: 8px 12px; padding-top: 12px; }.batch-marker-tool-row + .batch-marker-tool-row { margin-top: 14px; border-top: 1px solid rgb(var(--v-theme-outline)); }.batch-marker-tool-row strong { grid-column: 1 / -1; font-size: 12px; font-weight: 600; }
.batch-marker-tool-row > label, .batch-marker-tool-field { display: grid; gap: 6px; min-width: 0; font-size: 12px; }
.batch-marker-tool-row > .v-btn { grid-column: 5; height: 40px; margin: 0; letter-spacing: 0; }
.batch-marker-tool-row > label > input { width: 100%; height: 40px; }
.batch-marker-tool-row-appearance { grid-template-columns: repeat(6, minmax(0, 1fr)) auto; }.batch-marker-tool-row-appearance > .v-btn { grid-column: 7; }
.batch-marker-tool-row > label > input, .batch-cell-input { border: 1px solid rgba(var(--v-theme-on-surface), .18); border-radius: 5px; padding: 6px 8px; color: inherit; background: rgb(var(--v-theme-surface)); font-size: 13px; min-width: 0; }
.batch-marker-tool-field :deep(.v-field) { font-size: 13px; }.batch-marker-shape-select { min-width: 145px; }.batch-marker-shape-select :deep(.v-field) { font-size: 13px; border-radius: 5px; }
.batch-marker-messages { display: grid; gap: 6px; margin: 0 24px 10px; max-height: 150px; overflow: auto; flex-shrink: 0; }
.batch-marker-table-wrap { flex: 1; min-height: 120px; overflow: auto; border-top: 1px solid rgb(var(--v-theme-outline)); }
.batch-marker-table { border-collapse: separate; border-spacing: 0; width: 100%; min-width: 980px; font-size: 13px; }.batch-marker-table th { position: sticky; top: 0; z-index: 1; background: rgb(var(--v-theme-surface)); text-align: left; padding: 12px 10px; border-bottom: 1px solid rgba(var(--v-theme-on-surface), .16); white-space: nowrap; }
.batch-marker-table td { padding: 7px 10px; border-bottom: 1px solid rgba(var(--v-theme-on-surface), .07); }.batch-marker-table tr.batch-row-changed td { background: rgba(var(--v-theme-secondary), .06); }.batch-marker-table tr.batch-row-selected td { background: rgba(var(--v-theme-secondary), .1); }
.batch-cell-input { width: 100%; min-width: 115px; }.batch-numeric-input { min-width: 65px; max-width: 90px; }.batch-number-input { min-width: 130px; }.batch-cell-input:focus, .batch-marker-tool-row > label > input:focus { outline: 2px solid rgba(var(--v-theme-secondary), .65); outline-offset: 1px; }
.batch-cell-input:disabled { opacity: .45; }.batch-color-input { width: 40px; height: 32px; cursor: pointer; }.batch-check { width: 42px; text-align: center !important; }.batch-check input { width: 15px; height: 15px; accent-color: rgb(var(--v-theme-secondary)); cursor: pointer; }.batch-page, .batch-type, .batch-row-status { white-space: nowrap; }.batch-row-status { font-size: 12px; opacity: .8; }.batch-status-changed { color: rgb(var(--v-theme-secondary)); font-weight: 600; }.batch-status-error { color: rgb(var(--v-theme-error)); }.batch-empty { padding: 60px !important; text-align: center; opacity: .65; }
.batch-marker-footer { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 24px; }.batch-marker-footer strong { display: block; font-size: 13px; }.batch-marker-footer small { display: block; font-size: 11px; opacity: .6; margin-top: 3px; }.batch-marker-footer-actions { display: flex; align-items: center; gap: 8px; }
.batch-marker-pagination { display: flex; justify-content: center; flex-shrink: 0; padding: 6px 12px; border-top: 1px solid rgb(var(--v-theme-outline)); border-bottom: 1px solid rgb(var(--v-theme-outline)); background: rgb(var(--v-theme-surface)); }
.batch-marker-footer-actions .v-btn { min-width: 128px; height: 36px; font-size: 13px; font-weight: 500; letter-spacing: 0; }
@media (max-width: 1080px) { .batch-marker-selection { grid-template-columns: 1fr; }.batch-marker-selection-controls { justify-content: flex-end; border-top: 1px solid rgb(var(--v-theme-outline)); padding-top: 10px; } }
@media (max-width: 760px) { .batch-marker-header, .batch-marker-footer { padding: 12px; }.batch-marker-header .draft-manager-header__icon { width: 36px; height: 36px; flex: 0 0 36px; }.batch-marker-filters { grid-template-columns: 1fr 1fr; padding: 12px 12px 10px; gap: 8px; }.batch-marker-filters > :first-child { grid-column: 1 / -1; }.batch-marker-selection { margin: 0 12px 10px; padding: 10px; gap: 10px; }.batch-marker-counts { justify-content: space-between; }.batch-marker-stat { padding: 0 10px; gap: 5px; }.batch-marker-stat strong { font-size: 15px; }.batch-marker-selection-controls { display: grid; grid-template-columns: 1fr; gap: 10px; }.batch-marker-selection-actions { justify-content: space-between; }.batch-marker-selection-actions .v-btn { padding: 0 6px; }.batch-marker-view-toggle { width: 100%; }.batch-marker-view-toggle .v-btn { flex: 1; }.batch-marker-tools { margin: 0 12px 10px; }.batch-marker-messages { margin: 0 12px 8px; }.batch-marker-footer-actions { flex-wrap: wrap; }.batch-marker-tool-row label, .batch-marker-tool-field { width: 110px; } }
@media (max-width: 760px) {
  .batch-marker-filters { grid-template-columns: 220px 140px 140px; padding: 12px 12px 10px; gap: 8px; }
  .batch-marker-filters-range { grid-template-columns: 220px 140px 140px 100px 100px; }
  .batch-marker-filters > :first-child { grid-column: auto; }
  .batch-marker-card { overflow-y: auto; }
  .batch-marker-table-wrap { flex: 0 0 auto; max-height: 300px; }
  .batch-marker-footer { flex-shrink: 0; }
  .batch-marker-tool-content { max-height: 160px; padding: 0 12px 12px; }
  .batch-marker-tool-row, .batch-marker-tool-row-appearance { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 10px; }
  .batch-marker-tool-row > label, .batch-marker-tool-field { width: auto; }
  .batch-marker-tool-row > .v-btn { grid-column: 1 / -1; grid-row: auto; justify-self: end; }
}
</style>
