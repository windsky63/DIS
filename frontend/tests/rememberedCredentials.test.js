import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clearRememberedCredentials,
  loadRememberedCredentials,
  saveRememberedCredentials,
} from '../src/rememberedCredentials.js'

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
  }
}

test('remembered login credentials survive a later form load', () => {
  const storage = memoryStorage()
  saveRememberedCredentials(storage, { username: 'alice', password: 'secret-123' })
  assert.deepEqual(loadRememberedCredentials(storage), {
    username: 'alice', password: 'secret-123', remembered: true,
  })
})

test('invalid saved credentials are ignored and can be cleared', () => {
  const storage = memoryStorage({ 'drawing-marker.login-credentials': '{bad json' })
  assert.deepEqual(loadRememberedCredentials(storage), { username: '', password: '', remembered: true })
  saveRememberedCredentials(storage, { username: 'alice', password: 'secret-123' })
  clearRememberedCredentials(storage)
  assert.deepEqual(loadRememberedCredentials(storage), { username: '', password: '', remembered: true })
})

test('remember password is enabled by default without saved credentials', () => {
  assert.deepEqual(loadRememberedCredentials(memoryStorage()), {
    username: '', password: '', remembered: true,
  })
})

test('login form includes remember password and removes obsolete promotional copy', async () => {
  const source = await import('node:fs/promises').then(({ readFile }) => readFile(new URL('../src/components/AuthGate.vue', import.meta.url), 'utf8'))
  assert.match(source, /记住密码/)
  assert.match(source, /saveRememberedCredentials/)
  assert.doesNotMatch(source, /双屏核对/)
  assert.doesNotMatch(source, /注册成功后可访问全部图纸任务/)
})

test('login background animation stays on compositor-friendly properties and respects reduced motion', async () => {
  const source = await import('node:fs/promises').then(({ readFile }) => readFile(new URL('../src/components/AuthGate.vue', import.meta.url), 'utf8'))
  assert.match(source, /class="auth-background"/)
  assert.match(source, /auth-background__grid/)
  assert.match(source, /auth-background__scan/)
  assert.match(source, /auth-background__orb--one/)
  assert.match(source, /auth-background__orb--two/)
  assert.match(source, /@keyframes auth-grid-drift/)
  assert.match(source, /@keyframes auth-glow-drift/)
  assert.match(source, /@keyframes auth-scan-sweep/)
  assert.match(source, /will-change: transform/)
  assert.match(source, /@media \(prefers-reduced-motion: reduce\)/)
  assert.doesNotMatch(source, /@keyframes auth-[^{]+\{[^}]*background-position/s)
})
