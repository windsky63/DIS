<script setup>
defineProps({
  duplicateInfo: { type: Object, default: null },
})

const duplicate = defineModel('duplicate', { type: Boolean, required: true })
defineEmits(['resolve-duplicate'])
</script>

<template>
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
