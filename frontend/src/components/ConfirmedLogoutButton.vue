<script setup>
import { onBeforeUnmount } from 'vue'

import { useTimedConfirmation } from '../composables/useTimedConfirmation.js'

const emit = defineEmits(['confirm'])
const confirmation = useTimedConfirmation()
const pending = confirmation.pendingId

function requestLogout() {
  if (confirmation.request('logout')) emit('confirm')
}

onBeforeUnmount(confirmation.dispose)
</script>

<template>
  <v-menu :model-value="pending === 'logout'" :open-on-click="false" :close-on-content-click="false" location="bottom center" :offset="8" :min-width="0">
    <template #activator="{ props }">
      <v-btn v-bind="props" block color="error" variant="tonal" :aria-label="pending === 'logout' ? '确认退出登录' : '退出登录'" @click.stop="requestLogout">{{ pending === 'logout' ? '再次点击确认退出' : '退出登录' }}</v-btn>
    </template>
    <div class="analysis-queue-archive-confirm logout-confirm-bubble" role="status">确定退出登录</div>
  </v-menu>
</template>
