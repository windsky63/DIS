import assert from 'node:assert/strict'
import test from 'node:test'

import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'
import { createVuetify } from 'vuetify'
import { APP_THEMES } from '../src/themePreferences.js'


test('App setup resolves every workspace operation binding', async () => {
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { deviceMemory: 8, hardwareConcurrency: 8, maxTouchPoints: 0 },
  })
  Object.assign(globalThis, {
    localStorage: { getItem: () => null, setItem: () => undefined },
    window: {
      devicePixelRatio: 1, innerWidth: 1280, innerHeight: 800,
      setTimeout, clearTimeout, setInterval, clearInterval,
      addEventListener: () => undefined, removeEventListener: () => undefined,
    },
    document: { body: { classList: { toggle: () => undefined } } },
  })
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent' })
  try {
    const { default: App } = await vite.ssrLoadModule('/src/App.vue')
    const app = createSSRApp(App)
    app.use(createVuetify({ theme: { defaultTheme: 'weldLight', themes: APP_THEMES } }))
    app.config.warnHandler = () => undefined
    await assert.doesNotReject(() => renderToString(app))
  } finally {
    await vite.close()
  }
})
