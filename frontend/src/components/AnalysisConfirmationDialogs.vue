<script setup>
defineProps({
  pendingCandidate: { type: Object, default: null },
  pendingCount: { type: Number, default: 0 },
  duplicateInfo: { type: Object, default: null },
})

const renumber = defineModel('renumber', { type: Boolean, required: true })
const duplicate = defineModel('duplicate', { type: Boolean, required: true })
defineEmits(['renumber-from-selected', 'cancel-renumber', 'resolve-duplicate'])
</script>

<template>
  <v-dialog v-model="renumber" max-width="500" @click:outside="$emit('cancel-renumber')">
    <v-card>
      <v-card-title>确认重新智能编号</v-card-title>
      <v-card-text>将以第 {{ pendingCandidate?.page || '-' }} 页对象“{{ pendingCandidate?.number || '?' }}”为起点，循环重新编号当前页 {{ pendingCount }} 个同类有效对象。此操作会覆盖这些对象现有编号，但可以撤销。</v-card-text>
      <v-card-actions><v-btn variant="text" @click="$emit('cancel-renumber')">取消</v-btn><v-spacer /><v-btn color="secondary" @click="$emit('renumber-from-selected')">确认重新编号</v-btn></v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="duplicate" persistent max-width="540">
    <v-card>
      <v-card-title>发现已完成的相同解析任务</v-card-title>
      <v-card-text>
        <div>图纸“{{ duplicateInfo?.fileName || '-' }}”已有相同配置的解析结果。</div>
        <div class="mt-2 text-medium-emphasis">原任务解析范围：第 {{ duplicateInfo?.analyzedRange?.[0] || 1 }} 页至第 {{ duplicateInfo?.analyzedRange?.[1] || duplicateInfo?.totalPages || '-' }} 页；完成时间：{{ duplicateInfo?.existingUpdatedAt || '-' }}。</div>
        <div class="mt-3">可以直接读取已有结果，也可以重新创建解析任务。</div>
      </v-card-text>
      <v-card-actions><v-btn variant="text" @click="$emit('resolve-duplicate', 'cancel')">取消</v-btn><v-spacer /><v-btn variant="tonal" color="secondary" @click="$emit('resolve-duplicate', 'reuse')">使用已有结果</v-btn><v-btn color="accent" @click="$emit('resolve-duplicate', 'reanalyze')">重新解析</v-btn></v-card-actions>
    </v-card>
  </v-dialog>
</template>
