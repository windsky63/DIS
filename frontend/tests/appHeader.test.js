import assert from 'node:assert/strict'
import test from 'node:test'

import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'


test('header renders the AI assistant icon with explicit visible dimensions', async () => {
    const vite = await createServer({
      server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent',
      vue: { template: { compilerOptions: { isCustomElement: tag => tag.startsWith('v-') } } },
    })
  try {
    const { default: AppHeader } = await vite.ssrLoadModule('/src/components/AppHeader.vue')
    const app = createSSRApp({ render: () => h(AppHeader, { height: 50, shortcuts: {} }) })
    const passThrough = { setup: (_props, { attrs, slots }) => () => h('div', attrs, slots.default?.()) }
    for (const name of ['v-app-bar', 'v-app-bar-title', 'v-btn', 'v-tooltip', 'v-menu', 'v-card', 'v-card-title', 'v-list', 'v-list-item', 'v-chip', 'v-divider', 'v-card-actions']) {
      app.component(name, passThrough)
    }
    const html = await renderToString(app)
    assert.match(html, /aria-label="打开 AI 助手"/)
    assert.match(html, /assistant-button[\s\S]*?<svg[^>]*width="22"[^>]*height="22"/)
  } finally {
    await vite.close()
  }
})

test('header exposes the active project switch instead of an unavailable placeholder', async () => {
  const { readFile } = await import('node:fs/promises')
  const source = await readFile(new URL('../src/components/AppHeader.vue', import.meta.url), 'utf8')
  assert.match(source, /aria-label="切换项目"/)
  assert.match(source, /activeProject\.name/)
  assert.match(source, /\$emit\('switchProject'/)
  assert.doesNotMatch(source, /切换项目（暂未开放）/)
})
