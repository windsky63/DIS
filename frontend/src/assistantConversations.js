import { normalizeAssistantSources } from './assistantSources.js'

const PREFIX = 'weld-marker.ai-conversations.'
const SUFFIX = '.v1'
const MAX_CONVERSATIONS = 20

const storageKey = username => `${PREFIX}${String(username || 'anonymous')}${SUFFIX}`

const sanitizeMessage = message => {
  if (!message || !['user', 'assistant'].includes(message.role)) return null
  const content = String(message.content || '').trim()
  if (!content) return null
  const sanitized = { role: message.role, content }
  if (message.role === 'assistant') {
    const sources = normalizeAssistantSources(message.sources)
    if (sources.length) sanitized.sources = sources
  }
  return sanitized
}

const sanitizeConversation = conversation => {
  if (!conversation || !conversation.id) return null
  return {
    id: String(conversation.id),
    title: String(conversation.title || '新对话').slice(0, 40),
    updatedAt: Number(conversation.updatedAt) || 0,
    messages: (Array.isArray(conversation.messages) ? conversation.messages : []).map(sanitizeMessage).filter(Boolean),
  }
}

export function createAssistantConversation(now = Date.now, createId = () => globalThis.crypto?.randomUUID?.() || `${now()}-${Math.random()}`) {
  const timestamp = now()
  return {
    id: createId(),
    title: '新对话',
    updatedAt: timestamp,
    messages: [{
      role: 'assistant',
      content: '你好，我是图纸标识识别系统的 AI 助手。我可以介绍系统功能，并引导你完成上传、识别、核对和导出。你想先了解哪一步？',
    }],
  }
}

export function loadAssistantConversations(storage, username) {
  try {
    const value = JSON.parse(storage?.getItem(storageKey(username)) || '[]')
    if (!Array.isArray(value)) return []
    return value.map(sanitizeConversation).filter(Boolean).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS)
  } catch {
    return []
  }
}

export function saveAssistantConversations(storage, username, conversations) {
  const value = (Array.isArray(conversations) ? conversations : [])
    .map(sanitizeConversation).filter(Boolean).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS)
  try { storage?.setItem(storageKey(username), JSON.stringify(value)) } catch { /* private mode or full storage */ }
  return value
}
