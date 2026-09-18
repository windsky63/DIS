<script setup>
import { computed, ref, watch } from 'vue'

import { validateAuthForm } from '../authFormValidation.js'

const props = defineProps({ auth: { type: Object, required: true } })
const mode = ref('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const showPassword = ref(false)
const attempted = ref(false)
const rememberPassword = ref(false)

const validation = computed(() => validateAuthForm({ mode: mode.value, username: username.value, password: password.value, confirmPassword: confirmPassword.value }))
const usernameError = computed(() => attempted.value || username.value ? validation.value.username : '')
const passwordError = computed(() => attempted.value || password.value ? validation.value.password : '')
const confirmPasswordError = computed(() => attempted.value || confirmPassword.value ? validation.value.confirmPassword : '')

watch(mode, () => {
  password.value = ''
  confirmPassword.value = ''
  showPassword.value = false
  attempted.value = false
  props.auth.clearError?.()
})

async function submit() {
  attempted.value = true
  props.auth.clearError?.()
  if (!validation.value.valid) return
  try {
    if (mode.value === 'login') await props.auth.login(username.value.trim(), password.value, rememberPassword.value)
    else await props.auth.register(username.value.trim(), password.value)
  } catch { /* auth.error renders the server message */ }
}

</script>

<template>
  <main class="auth-gate">
    <div class="auth-background" aria-hidden="true">
      <span class="auth-background__grid" />
      <span class="auth-background__scan" />
      <span class="auth-background__orb auth-background__orb--one" />
      <span class="auth-background__orb auth-background__orb--two" />
    </div>
    <section class="auth-shell" aria-labelledby="auth-title">
      <aside class="auth-brand-panel">
        <div class="auth-brand-mark" aria-hidden="true"><span class="auth-brand-mark__pipe" /><span class="auth-brand-mark__joint auth-brand-mark__joint--one" /><span class="auth-brand-mark__joint auth-brand-mark__joint--two" /><span class="auth-brand-mark__scan" /></div>
        <div><span class="auth-eyebrow">DRAWING INTELLIGENCE</span><h1 id="auth-title">图纸标识识别系统</h1><p>面向工业图纸的智能解析、标识核对与多人按页协作。</p></div>
        <ul class="auth-feature-list">
          <li><i aria-hidden="true">01</i><span><strong>智能识别</strong><small>焊口、阀门、法兰和支架统一核对</small></span></li>
          <li><i aria-hidden="true">02</i><span><strong>按页协作</strong><small>页面锁与版本控制减少多人修改冲突</small></span></li>
          <li><i aria-hidden="true">03</i><span><strong>任务追踪</strong><small>解析进度、审核人员与修改记录清晰可查</small></span></li>
        </ul>
      </aside>

      <v-card class="auth-card" elevation="0">
        <v-card-title class="auth-card-title">{{ mode === 'login' ? '欢迎回来' : '创建账号' }}</v-card-title>
        <p class="auth-card-subtitle">{{ mode === 'login' ? '登录后继续图纸核对工作' : '注册完成后将自动登录系统' }}</p>
        <v-tabs v-model="mode" grow color="secondary" class="auth-tabs"><v-tab value="login">账号登录</v-tab><v-tab value="register">注册账号</v-tab></v-tabs>

        <v-card-text class="auth-form-wrap">
          <v-form novalidate @submit.prevent="submit">
            <label class="auth-field-label" for="auth-username">用户名</label>
            <v-text-field id="auth-username" v-model="username" placeholder="请输入 3–40 个字符" autocomplete="username" :disabled="auth.submitting.value" :error-messages="usernameError" hide-details="auto" class="auth-field" />

            <label class="auth-field-label" for="auth-password">密码</label>
            <v-text-field id="auth-password" v-model="password" :type="showPassword ? 'text' : 'password'" placeholder="请输入 6–128 个字符" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" :disabled="auth.submitting.value" :error-messages="passwordError" hide-details="auto" class="auth-field">
              <template #append-inner>
                <button type="button" class="password-toggle" :aria-label="showPassword ? '隐藏密码' : '显示密码'" @click="showPassword = !showPassword">
                  <svg v-if="showPassword" viewBox="0 0 24 24" aria-hidden="true"><path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" /><circle cx="12" cy="12" r="2.6" /></svg>
                  <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3 21 21" /><path d="M10.6 6.1A9.6 9.6 0 0 1 12 6c6 0 9.5 6 9.5 6a16 16 0 0 1-2.1 2.8M6.2 6.2C3.9 7.8 2.5 12 2.5 12s3.5 6 9.5 6a9.8 9.8 0 0 0 3-.5" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>
                </button>
              </template>
            </v-text-field>

            <label v-if="mode === 'login'" class="remember-password">
              <input v-model="rememberPassword" type="checkbox" :disabled="auth.submitting.value" />
              <span>记住密码</span>
            </label>

            <template v-if="mode === 'register'">
              <label class="auth-field-label" for="auth-confirm-password">确认密码</label>
              <v-text-field id="auth-confirm-password" v-model="confirmPassword" :type="showPassword ? 'text' : 'password'" placeholder="请再次输入密码" autocomplete="new-password" :disabled="auth.submitting.value" :error-messages="confirmPasswordError" hide-details="auto" class="auth-field" />
              <div class="password-requirement" :class="{ satisfied: password.length >= 6 }"><i aria-hidden="true" />密码至少包含 6 个字符</div>
            </template>

            <v-alert v-if="auth.error.value" type="error" variant="tonal" density="compact" class="auth-error">{{ auth.error.value }}</v-alert>
            <v-btn type="submit" color="secondary" block size="large" class="auth-submit" :loading="auth.submitting.value" :disabled="auth.submitting.value">{{ mode === 'login' ? '登录系统' : '注册并登录' }}</v-btn>
          </v-form>
        </v-card-text>
      </v-card>
    </section>
  </main>
</template>

<style scoped>
.auth-gate { position: relative; min-height: 100vh; display: grid; place-items: center; padding: 32px; overflow: hidden; isolation: isolate; background: linear-gradient(135deg, #d3e4e7 0%, #a9cbd0 46%, #dbe4ed 100%); color: #173e57; }
.auth-background { position: fixed; z-index: 0; inset: 0; overflow: hidden; pointer-events: none; }
.auth-background__grid { position: absolute; inset: -72px; opacity: .8; background-image: linear-gradient(rgba(20, 66, 82, .16) 1px, transparent 1px), linear-gradient(90deg, rgba(20, 66, 82, .16) 1px, transparent 1px), linear-gradient(rgba(32, 120, 122, .08) 1px, transparent 1px), linear-gradient(90deg, rgba(32, 120, 122, .08) 1px, transparent 1px); background-size: 48px 48px, 48px 48px, 12px 12px, 12px 12px; transform: translate3d(-48px, -48px, 0); animation: auth-grid-drift 14s linear infinite; will-change: transform; }
.auth-background__scan { position: absolute; width: clamp(220px, 24vw, 420px); height: 170vh; left: -38vw; top: -35vh; opacity: 0; background: linear-gradient(90deg, transparent, rgba(108, 229, 219, .13) 23%, rgba(236, 255, 253, .62) 50%, rgba(65, 172, 174, .14) 76%, transparent); transform: translate3d(0, 0, 0) rotate(14deg); animation: auth-scan-sweep 9s ease-in-out infinite; will-change: transform, opacity; }
.auth-background__orb { position: absolute; aspect-ratio: 1; border: 1px solid rgba(31, 105, 116, .32); border-radius: 50%; background: radial-gradient(circle at 42% 40%, rgba(105, 221, 208, .36), rgba(42, 130, 135, .12) 36%, transparent 68%); box-shadow: 0 0 0 48px rgba(35, 112, 121, .055), 0 0 0 96px rgba(35, 112, 121, .035); animation: auth-glow-drift 12s ease-in-out infinite alternate; will-change: transform, opacity; }
.auth-background__orb--one { width: clamp(360px, 39vw, 650px); left: -13vw; top: -28vh; opacity: .84; }
.auth-background__orb--two { width: clamp(300px, 32vw, 540px); right: -10vw; bottom: -30vh; opacity: .7; animation-duration: 16s; animation-direction: alternate-reverse; }
.auth-shell { position: relative; z-index: 1; width: min(980px, 100%); min-height: 610px; display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(380px, .92fr); overflow: hidden; border: 1px solid rgba(108, 138, 153, .22); border-radius: 22px; background: #fff; box-shadow: 0 28px 70px rgba(16, 42, 67, .18); }
.auth-brand-panel { position: relative; display: flex; flex-direction: column; justify-content: center; padding: 58px 64px; overflow: hidden; background: linear-gradient(145deg, #102a43 0%, #173e57 54%, #205963 100%); color: #fff; }
.auth-brand-panel::after { position: absolute; width: 360px; height: 360px; right: -215px; top: -110px; content: ''; border: 1px solid rgba(123, 210, 203, .24); border-radius: 50%; box-shadow: 0 0 0 55px rgba(123, 210, 203, .04), 0 0 0 110px rgba(123, 210, 203, .035); }
.auth-brand-mark { position: relative; width: 58px; height: 58px; margin-bottom: 30px; border: 1px solid rgba(255, 255, 255, .2); border-radius: 14px; background: rgba(255, 255, 255, .07); }
.auth-brand-mark__pipe { position: absolute; left: 10px; top: 27px; width: 38px; height: 4px; border-radius: 3px; background: #8ed5cf; transform: rotate(-28deg); }
.auth-brand-mark__joint { position: absolute; z-index: 1; width: 12px; height: 12px; border: 3px solid #fff; border-radius: 50%; background: #2d8b89; }
.auth-brand-mark__joint--one { left: 9px; top: 33px; }.auth-brand-mark__joint--two { right: 9px; top: 12px; }
.auth-brand-mark__scan { position: absolute; left: 27px; top: 7px; width: 1px; height: 44px; background: linear-gradient(transparent, #e49b78, transparent); box-shadow: 0 0 9px #e49b78; transform: rotate(18deg); }
.auth-eyebrow { display: block; margin-bottom: 12px; color: #83cbc6; font-size: 10px; font-weight: 800; letter-spacing: .2em; }
.auth-brand-panel h1 { margin: 0; font-size: clamp(27px, 3vw, 38px); font-weight: 700; letter-spacing: .02em; }
.auth-brand-panel p { max-width: 390px; margin: 15px 0 35px; color: #b9ccd5; font-size: 14px; line-height: 1.8; }
.auth-feature-list { display: grid; gap: 18px; margin: 0; padding: 0; list-style: none; }
.auth-feature-list li { display: flex; align-items: center; gap: 15px; }.auth-feature-list i { width: 32px; height: 32px; display: grid; place-items: center; flex: 0 0 auto; border: 1px solid rgba(126, 207, 201, .36); border-radius: 8px; color: #8ed5cf; font-size: 9px; font-style: normal; }
.auth-feature-list span { display: grid; gap: 2px; }.auth-feature-list strong { font-size: 13px; }.auth-feature-list small { color: #9db4bf; font-size: 10px; }
.auth-card { align-self: center; padding: 38px 42px; background: #fff; }.auth-card-title { padding: 0; color: #102a43; font-size: 26px; font-weight: 750; }.auth-card-subtitle { margin: 6px 0 25px; color: #718893; font-size: 12px; }
.auth-tabs { margin-bottom: 28px; border-bottom: 1px solid rgb(var(--v-theme-outline)); }.auth-tabs :deep(.v-tab) { font-size: 12px; font-weight: 700; letter-spacing: .03em; }
.auth-form-wrap { padding: 0; }.auth-field-label { display: block; margin: 0 0 7px; color: #294e61; font-size: 11px; font-weight: 700; }.auth-field { margin-bottom: 17px; }
.auth-field :deep(.v-field) { border-radius: 9px; background: #fbfcfd; }.auth-field :deep(.v-field--focused) { background: #fff; box-shadow: 0 0 0 3px rgba(45, 139, 137, .08); }
.password-toggle { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; border: 0; border-radius: 6px; background: transparent; color: #607d8a; cursor: pointer; transition: color .16s ease, background .16s ease; }.password-toggle:hover { background: #e9f3f3; color: #2d7f80; }.password-toggle:focus-visible { outline: 2px solid #2d8b89; outline-offset: 1px; }.password-toggle svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.remember-password { width: fit-content; margin: -5px 0 18px; display: flex; align-items: center; gap: 8px; color: #526c7a; font-size: 11px; cursor: pointer; }.remember-password input { width: 15px; height: 15px; accent-color: #2d8b89; }
.password-requirement { display: flex; align-items: center; gap: 6px; margin: -7px 0 16px; color: #80939d; font-size: 10px; }.password-requirement i { width: 6px; height: 6px; border-radius: 50%; background: #aebdc4; }.password-requirement.satisfied { color: #2d7f68; }.password-requirement.satisfied i { background: #45a37f; }
.auth-error { margin: 4px 0 16px; font-size: 11px; }.auth-submit { min-height: 46px; margin-top: 5px; border-radius: 9px !important; font-size: 12px; font-weight: 700; letter-spacing: .08em; box-shadow: 0 8px 18px rgba(45, 139, 137, .22); }
@keyframes auth-grid-drift { to { transform: translate3d(0, 0, 0); } }
@keyframes auth-glow-drift { to { opacity: .98; transform: translate3d(72px, 46px, 0) scale(1.08); } }
@keyframes auth-scan-sweep { 0%, 8% { opacity: 0; transform: translate3d(0, 0, 0) rotate(14deg); } 18%, 72% { opacity: .82; } 88%, 100% { opacity: 0; transform: translate3d(155vw, 0, 0) rotate(14deg); } }
@media (prefers-reduced-motion: reduce) { .auth-background > span { animation: none; will-change: auto; }.auth-background__scan { display: none; } }
@media (max-width: 760px) { .auth-gate { padding: 18px; }.auth-background__orb--two { display: none; }.auth-background__scan { animation-duration: 13s; }.auth-shell { width: min(460px, 100%); min-height: auto; grid-template-columns: 1fr; }.auth-brand-panel { padding: 28px 30px 25px; }.auth-brand-mark, .auth-feature-list { display: none; }.auth-brand-panel p { margin: 8px 0 0; line-height: 1.5; }.auth-card { padding: 30px; } }
@media (max-width: 420px) { .auth-card { padding: 26px 22px; }.auth-brand-panel { padding: 24px 22px; } }
</style>
