import assert from 'node:assert/strict'
import test from 'node:test'

import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createServer } from 'vite'


test('assistant panel renders conversation history, quick starts, and input affordance', async () => {
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent' })
  try {
    const { default: AssistantPanel } = await vite.ssrLoadModule('/src/components/AssistantPanel.vue')
    const app = createSSRApp({
      render: () => h(AssistantPanel, {
        modelValue: true,
        username: 'alice',
        context: { currentPage: 1, totalPages: 0, jobLoaded: false, mode: 'single' },
      }),
    })
    const renderContext = {}
    const html = `${await renderToString(app, renderContext)}${Object.values(renderContext.teleports || {}).join('')}`
    assert.match(html, /AI 助手/)
    assert.match(html, /新建对话/)
    assert.match(html, /同步到服务器/)
    assert.equal((html.match(/class="assistant-quick-start-question"/g) || []).length, 4)
    assert.equal((html.match(/class="assistant-quick-start-refresh"/g) || []).length, 4)
    assert.match(html, /输入你的问题/)
    assert.match(html, /批准的系统文档片段会发送到已配置的 AI 服务/)
  } finally {
    await vite.close()
  }
})

test('assistant retrieval details render status and safe document sources', async () => {
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'custom', logLevel: 'silent' })
  try {
    const { default: AssistantRetrievalDetails } = await vite.ssrLoadModule('/src/components/AssistantRetrievalDetails.vue')
    const app = createSSRApp({
      render: () => h(AssistantRetrievalDetails, {
        status: '正在检索系统文档',
        sources: [{
          documentId: 'usage-guide', title: '使用指南', path: 'docs/usage.md',
          section: '保存并关闭', startLine: 31,
        }],
      }),
    })
    const html = await renderToString(app)
    assert.match(html, /aria-live="polite"/)
    assert.match(html, /正在检索系统文档/)
    assert.match(html, /参考资料/)
    assert.match(html, /使用指南/)
    assert.match(html, /保存并关闭/)
    assert.match(html, /docs\/usage\.md/)
    assert.doesNotMatch(html, /file:\/\//)
    assert.doesNotMatch(html, /C:\\/)
  } finally {
    await vite.close()
  }
})
