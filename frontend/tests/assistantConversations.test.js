import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createAssistantConversation,
  loadAssistantConversations,
  saveAssistantConversations,
} from '../src/assistantConversations.js'


test('new assistant conversations start with a product greeting', () => {
  const conversation = createAssistantConversation(() => 1234, () => 'conversation-1')
  assert.equal(conversation.id, 'conversation-1')
  assert.equal(conversation.title, '新对话')
  assert.equal(conversation.messages.length, 1)
  assert.equal(conversation.messages[0].role, 'assistant')
  assert.match(conversation.messages[0].content, /图纸标识识别系统/)
})

test('conversation history is isolated by logged-in username and malformed data is ignored', () => {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  const conversation = createAssistantConversation(() => 1234, () => 'conversation-1')
  conversation.title = '识别流程'
  saveAssistantConversations(storage, 'alice', [conversation])

  assert.equal(loadAssistantConversations(storage, 'bob').length, 0)
  assert.equal(loadAssistantConversations(storage, 'alice')[0].title, '识别流程')
  values.set('weld-marker.ai-conversations.alice.v1', '{broken')
  assert.deepEqual(loadAssistantConversations(storage, 'alice'), [])
})

test('history persistence drops unsupported roles and caps retained conversations', () => {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  const conversations = Array.from({ length: 25 }, (_, index) => ({
    id: `c-${index}`,
    title: `会话 ${index}`,
    updatedAt: index,
    messages: [{ role: 'assistant', content: 'ok' }, { role: 'system', content: 'unsafe' }],
  }))
  saveAssistantConversations(storage, 'alice', conversations)
  const restored = loadAssistantConversations(storage, 'alice')
  assert.equal(restored.length, 20)
  assert.equal(restored[0].id, 'c-24')
  assert.deepEqual(restored[0].messages, [{ role: 'assistant', content: 'ok' }])
})

test('assistant sources persist only safe citation fields', () => {
  const values = new Map()
  const storage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
  const conversation = {
    id: 'c-sources', title: '保存', updatedAt: 10,
    messages: [{
      role: 'assistant', content: '回答',
      sources: [{
        documentId: 'usage-guide', title: '使用指南', path: 'docs/usage.md',
        section: '保存并关闭', startLine: 31, absolutePath: 'C:/secret', content: '原始正文',
      }],
    }],
  }

  saveAssistantConversations(storage, 'alice', [conversation])
  const source = loadAssistantConversations(storage, 'alice')[0].messages[0].sources[0]

  assert.deepEqual(source, {
    documentId: 'usage-guide', title: '使用指南', path: 'docs/usage.md', section: '保存并关闭', startLine: 31,
  })
  assert.equal(source.absolutePath, undefined)
  assert.equal(source.content, undefined)
})
