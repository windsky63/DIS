<script setup>
defineProps({
  config: { type: Object, required: true },
  symbolPolicyOptions: { type: Array, default: () => [] },
  markerPolicyOptions: { type: Array, default: () => [] },
})
const open = defineModel({ type: Boolean, required: true })
</script>

<template>
  <v-dialog v-model="open" max-width="720">
    <v-card class="symbol-config-card">
      <v-card-title>焊口符号研究配置</v-card-title>
      <v-card-text>
          <div class="section-label">落图模式 / 黑色焊口符号</div>
          <v-switch v-model="config.placementSymbols.blackCircleEnabled" color="secondary" hide-details label="检测主管上的黑色圆"><v-tooltip activator="parent">按闭合路径的圆度识别贝塞尔圆或高圆度密集折线路径</v-tooltip></v-switch>
          <v-switch v-model="config.placementSymbols.approximateCircleEnabled" color="warning" hide-details label="允许低边数近似圆（补充规则，默认关闭）"><v-tooltip activator="parent">开启后才接受六边形等低边数近似圆及非圆形复合恢复候选，可能增加误识别</v-tooltip></v-switch>
          <v-switch v-model="config.placementSymbols.plainCircleEnabled" color="secondary" hide-details label="普通黑圆焊口"><v-tooltip activator="parent">没有附加 X 或方括号的普通焊口</v-tooltip></v-switch><v-switch v-model="config.placementSymbols.prefabricatedXEnabled" color="secondary" hide-details label="黑圆 + X（预制焊口）"><v-tooltip activator="parent">圆内或圆上的两条交叉短线表示预制符号</v-tooltip></v-switch><v-switch v-model="config.placementSymbols.socketThreadBracketEnabled" color="secondary" hide-details label="黑圆 + 方括号（承插焊/螺纹焊）"><v-tooltip activator="parent">圆附近成对方括号表示承插焊或螺纹焊</v-tooltip></v-switch><v-switch v-model="config.placementSymbols.mainPipeOnly" color="secondary" hide-details label="必须位于主管线上"><v-tooltip activator="parent">默认开启，用强过程线投影排除图签、文字和材料表中的圆</v-tooltip></v-switch>
          <div class="parameter-grid mt-3"><v-text-field v-model.number="config.placementSymbols.minimumDiameter" type="number" min="0.4" step="0.1" label="最小圆直径" /><v-text-field v-model.number="config.placementSymbols.maximumDiameter" type="number" min="1" step="0.5" label="最大圆直径" /><v-text-field v-model.number="config.placementSymbols.mainPipeTolerance" type="number" min="0.2" step="0.1" label="主管投影容差" /><v-text-field v-model.number="config.placementSymbols.darkThreshold" type="number" min="0" max="1" step="0.05" label="黑色阈值" /></div>
          <v-switch v-model="config.placementSymbols.includeResearchFallback" color="warning" hide-details label="允许研究候选回退（默认关闭）"><v-tooltip activator="parent">开启后，未呈现圆形的研究包泛化候选也可进入结果；可能增加误识别</v-tooltip></v-switch>
          <v-divider class="my-4" /><div class="section-label">研究候选辅助配置</div><v-select v-model="config.fixedSymbolPolicy" :items="symbolPolicyOptions" label="固定焊口符号策略" /><v-select v-model="config.markerPolicy" :items="markerPolicyOptions" label="填充标记策略" class="mt-3" /><v-slider v-model="config.minimumConfidence" min="0" max="1" step="0.05" color="secondary" label="最低候选置信度" thumb-label class="mt-3" />
          <v-alert type="info" variant="tonal" density="compact" class="mt-3">默认硬门禁是主管上的黑色圆形闭合路径。PDF 没有原生圆指令，因此贝塞尔圆和通过严格圆拟合的密集折线路径都按圆处理；六边形等低边数近似圆默认关闭。</v-alert>
      </v-card-text>
      <v-card-actions><v-spacer /><v-btn color="primary" @click="open = false">应用配置<v-tooltip activator="parent">关闭弹窗；下次图元研究将使用当前配置</v-tooltip></v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
