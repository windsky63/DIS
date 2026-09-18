<script setup>
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api.js'
import { useAuthSession } from '../composables/useAuthSession.js'
import AuthGate from './AuthGate.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthSession({ client: api })

function loginDestination() {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  if (redirect === '/main' || redirect === '/admin' || redirect.startsWith('/admin/')) return redirect
  return '/main'
}

watch(auth.status, status => {
  if (status === 'authenticated') void router.replace(loginDestination())
})

onMounted(() => auth.restore())
</script>

<template>
  <v-app class="login-app">
    <div v-if="auth.status.value === 'loading'" class="login-loading">
      <v-progress-circular indeterminate color="primary" />
      <span>正在检查登录状态…</span>
    </div>
    <AuthGate v-else :auth="auth" />
  </v-app>
</template>

<style scoped>
.login-app { background: rgb(var(--v-theme-background)); }
.login-loading { min-height: 100vh; display: flex; align-items: center; justify-content: center; gap: 14px; color: rgb(var(--v-theme-on-surface)); }
</style>
