<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import {
  createAssistantConversation,
  loadAssistantConversations,
  saveAssistantConversations,
} from '../assistantConversations'
import { resizeComposerTextarea, shouldSubmitComposer } from '../assistantComposer'
import { renderAssistantMarkdown } from '../assistantMarkdown'
import { assistantConfigurationAlert, replaceQuickStart, selectQuickStarts, visibleAssistantError } from '../assistantPresentation'
import { createStreamingAssistantMessage } from '../assistantStreamState'
import AssistantRetrievalDetails from './AssistantRetrievalDetails.vue'

const props = defineProps({
  modelValue: Boolean,
  username: { type: String, default: '' },
  context: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue'])

const conversations = ref([createAssistantConversation()])
const activeId = ref(conversations.value[0].id)
const draft = ref('')
const sending = ref(false)
const error = ref('')
const configuration = ref({ configured: null, model: '' })
const messageList = ref(null)
const composerInput = ref(null)
const historyOpen = ref(false)
const retrievalStatus = ref('')
let loadedUsername = ''
let persistenceChain = Promise.resolve()

const activeConversation = computed(() => conversations.value.find(item => item.id === activeId.value) || conversations.value[0])
const displayedError = computed(() => visibleAssistantError(configuration.value.configured, error.value))
const configurationAlert = computed(() => assistantConfigurationAlert(configuration.value.configured))
const quickStartPool = [
  '如何开始识别？',
  '怎样选择对照资料？',
  '如何核对识别结果？',
  '怎样保存和导出？',
  '如何推入解析队列？',
  '怎样恢复已完成的任务？',
  '如何调整标识框和引线？',
  '怎样新增遗漏的标识？',
  '如何修改标识编号？',
  '怎样撤销或重做操作？',
  '如何调整各类标识外观？',
  '怎样归档或删除解析任务？',
]
const quickStarts = ref(selectQuickStarts(quickStartPool))

function persist() {
  if (!loadedUsername) return
  const storage = typeof localStorage === 'undefined' ? null : localStorage
  const snapshot = saveAssistantConversations(storage, loadedUsername, conversations.value)
  persistenceChain = persistenceChain.then(() => api.saveAiConversations(snapshot)).catch(() => undefined)
}

async function loadHistory() {
  const username = props.username || 'anonymous'
  loadedUsername = username
  const storage = typeof localStorage === 'undefined' ? null : localStorage
  const cached = loadAssistantConversations(storage, username)
  try {
    const response = await api.getAiConversations()
    if (loadedUsername !== username) return
    const remote = Array.isArray(response.conversations) ? response.conversations : []
    conversations.value = remote.length ? remote : (cached.length ? cached : [createAssistantConversation()])
    saveAssistantConversations(storage, username, conversations.value)
    if (!remote.length) persist()
  } catch {
    if (loadedUsername !== username) return
    conversations.value = cached.length ? cached : [createAssistantConversation()]
  }
  activeId.value = conversations.value[0].id
}

function newConversation() {
  const conversation = createAssistantConversation()
  conversations.value = [conversation, ...conversations.value].slice(0, 20)
  activeId.value = conversation.id
  draft.value = ''
  error.value = ''
  historyOpen.value = false
  quickStarts.value = selectQuickStarts(quickStartPool)
  persist()
  nextTick(scrollToBottom)
}

function refreshQuickStart(index) {
  quickStarts.value = replaceQuickStart(quickStarts.value, quickStartPool, index)
}

function selectConversation(id) {
  activeId.value = id
  error.value = ''
  historyOpen.value = false
  nextTick(scrollToBottom)
}

function deleteConversation(id) {
  conversations.value = conversations.value.filter(item => item.id !== id)
  if (!conversations.value.length) conversations.value = [createAssistantConversation()]
  if (!conversations.value.some(item => item.id === activeId.value)) activeId.value = conversations.value[0].id
  persist()
}

function scrollToBottom() {
  if (messageList.value) messageList.value.scrollTop = messageList.value.scrollHeight
}

function resizeComposer() {
  resizeComposerTextarea(composerInput.value)
}

async function refreshConfiguration() {
  try {
    configuration.value = await api.getAiStatus()
  } catch {
    configuration.value = { configured: null, model: '' }
  }
}

async function sendMessage(content = draft.value) {
  const text = String(content || '').trim()
  if (!text || sending.value || !activeConversation.value) return
  error.value = ''
  const conversation = activeConversation.value
  conversation.messages.push({ role: 'user', content: text })
  const requestMessages = conversation.messages.map(message => ({ role: message.role, content: message.content }))
  if (conversation.title === '新对话') conversation.title = text.replace(/\s+/g, ' ').slice(0, 22)
  conversation.updatedAt = Date.now()
  draft.value = ''
  persist()
  await nextTick()
  scrollToBottom()
  sending.value = true
  retrievalStatus.value = '正在连接 AI 服务'
  const streamedMessage = createStreamingAssistantMessage()
  conversation.messages.push(streamedMessage)
  try {
    const response = await api.chatStream(requestMessages, props.context, delta => {
      retrievalStatus.value = ''
      streamedMessage.content += delta
      conversation.updatedAt = Date.now()
      void nextTick(scrollToBottom)
    }, {
      onStatus: status => {
        retrievalStatus.value = status
        void nextTick(scrollToBottom)
      },
      onSource: source => {
        streamedMessage.sources.push(source)
        conversation.updatedAt = Date.now()
        void nextTick(scrollToBottom)
      },
    })
    streamedMessage.content = response.message.content
    streamedMessage.sources = response.message.sources
    conversation.updatedAt = Date.now()
    persist()
  } catch (requestError) {
    if (!streamedMessage.content) {
      const index = conversation.messages.indexOf(streamedMessage)
      if (index >= 0) conversation.messages.splice(index, 1)
    } else {
      conversation.updatedAt = Date.now()
      persist()
    }
    error.value = streamedMessage.content
      ? `回答传输中断：${requestError?.message || '请稍后重试。'}`
      : (requestError?.message || 'AI 助手暂时无法回答，请稍后重试。')
    if (requestError?.status === 503) configuration.value.configured = false
  } finally {
    retrievalStatus.value = ''
    sending.value = false
    await nextTick()
    scrollToBottom()
  }
}

function onComposerKeydown(event) {
  if (shouldSubmitComposer(event)) {
    event.preventDefault()
    void sendMessage()
  }
}

watch(draft, () => { void nextTick(resizeComposer) })
watch(() => props.username, () => { if (props.modelValue) void loadHistory() })
watch(() => props.modelValue, open => {
  if (!open) return
  if (loadedUsername !== (props.username || 'anonymous')) void loadHistory()
  void refreshConfiguration()
  nextTick(scrollToBottom)
})
onMounted(() => { if (props.modelValue) void loadHistory() })
</script>

<template>
  <Teleport to="body">
    <Transition name="assistant-fade">
      <div v-if="modelValue" class="assistant-layer" role="presentation">
        <button class="assistant-backdrop" type="button" aria-label="关闭 AI 助手" @click="emit('update:modelValue', false)" />
        <aside class="assistant-panel" role="dialog" aria-modal="true" aria-labelledby="assistant-title">
          <header class="assistant-header">
            <button class="assistant-mobile-history" type="button" aria-label="打开会话记录" @click="historyOpen = !historyOpen">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h11" /></svg>
            </button>
            <span class="assistant-brand-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 8.5A5 5 0 0 1 12 4a5 5 0 0 1 5 4.5A4.5 4.5 0 0 1 18 17H9l-4 3v-5.2A4.5 4.5 0 0 1 7 8.5Z" /><path d="M9 11h.01M12 11h.01M15 11h.01" /></svg></span>
            <div><h2 id="assistant-title">AI 助手</h2><span>{{ configuration.model || '系统引导与操作教学' }}</span></div>
            <button class="assistant-close" type="button" aria-label="关闭 AI 助手" @click="emit('update:modelValue', false)">×</button>
          </header>

          <div class="assistant-workspace">
            <nav class="assistant-history" :class="{ 'assistant-history--open': historyOpen }" aria-label="会话记录">
              <button class="assistant-new-chat" type="button" @click="newConversation"><span>＋</span> 新建对话</button>
              <div class="assistant-history-label">会话记录</div>
              <div class="assistant-history-list">
                <div v-for="conversation in conversations" :key="conversation.id" class="assistant-history-row" :class="{ active: conversation.id === activeId }">
                  <button type="button" class="assistant-history-select" @click="selectConversation(conversation.id)">
                    <strong>{{ conversation.title }}</strong>
                    <small>{{ conversation.messages.length > 1 ? `${conversation.messages.length - 1} 条消息` : '尚未提问' }}</small>
                  </button>
                  <button type="button" class="assistant-history-delete" :aria-label="`删除会话：${conversation.title}`" @click="deleteConversation(conversation.id)">×</button>
                </div>
              </div>
              <div class="assistant-history-note">历史记录已同步到服务器，浏览器保留缓存</div>
            </nav>

            <main class="assistant-chat">
              <div ref="messageList" class="assistant-messages" aria-live="polite">
                <div v-for="(message, index) in activeConversation?.messages || []" :key="index" class="assistant-message" :class="`assistant-message--${message.role}`">
                  <span class="assistant-message-avatar" aria-hidden="true">{{ message.role === 'assistant' ? 'AI' : String(username || '我').slice(0, 1).toUpperCase() }}</span>
                  <div>
                    <small>{{ message.role === 'assistant' ? '系统助手' : '你' }}</small>
                    <div v-if="message.content && message.role === 'assistant'" class="assistant-markdown" v-html="renderAssistantMarkdown(message.content)" />
                    <p v-else-if="message.content">{{ message.content }}</p>
                    <p v-else-if="sending && message.role === 'assistant' && !retrievalStatus" class="assistant-thinking"><i /><i /><i /></p>
                    <AssistantRetrievalDetails
                      v-if="message.role === 'assistant'"
                      :status="sending && index === activeConversation.messages.length - 1 ? retrievalStatus : ''"
                      :sources="message.sources || []"
                    />
                  </div>
                </div>
              </div>

              <div class="assistant-composer-area">
                <div v-if="(activeConversation?.messages?.length || 0) <= 1" class="assistant-quick-starts">
                  <div v-for="(item, index) in quickStarts" :key="item" class="assistant-quick-start-row">
                    <button class="assistant-quick-start-question" type="button" @click="sendMessage(item)">{{ item }}</button>
                    <button class="assistant-quick-start-refresh" type="button" :aria-label="`换一个问题：${item}`" title="换一个问题" @click="refreshQuickStart(index)"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5" /><path d="M18.1 16.5A8 8 0 1 1 19.5 9L20 12" /></svg></button>
                  </div>
                </div>
                <div v-if="configurationAlert" class="assistant-config-error" role="alert">{{ configurationAlert.message }}</div>
                <div v-if="displayedError" class="assistant-error" role="alert">{{ displayedError }}</div>
                <div class="assistant-composer">
                  <textarea ref="composerInput" v-model="draft" rows="2" maxlength="4000" aria-label="输入你的问题" placeholder="输入你的问题，Enter 发送，Shift+Enter 换行" :disabled="sending" @input="resizeComposer" @keydown="onComposerKeydown" />
                  <button type="button" :disabled="!draft.trim() || sending" aria-label="发送消息" @click="sendMessage()">
                    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 14-7-4 14-3.2-5.8Z" /><path d="m11.8 13.2 3.6-3.6" /></svg>
                  </button>
                </div>
                <small class="assistant-disclaimer">检索时，批准的系统文档片段会发送到已配置的 AI 服务。AI 回答可能存在偏差，关键操作请以系统界面和项目规范为准。</small>
              </div>
            </main>
          </div>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.assistant-layer { position: fixed; inset: 0; z-index: 12000; }
.assistant-backdrop { position: absolute; inset: 0; width: 100%; border: 0; background: rgba(7,25,36,.42); backdrop-filter: blur(2px); cursor: default; }
.assistant-panel { position: absolute; inset: 0 0 0 auto; width: min(1040px, calc(100vw - 80px)); display: flex; flex-direction: column; overflow: hidden; background: #f7fafb; color: #203d4e; box-shadow: -18px 0 60px rgba(8,30,43,.28); }
.assistant-header { height: 68px; padding: 0 20px; flex: 0 0 68px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid #d8e3e7; background: #fff; }
.assistant-brand-icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 11px; background: linear-gradient(135deg,#2d8b89,#246574); color: #fff; box-shadow: 0 6px 16px rgba(45,139,137,.25); }
.assistant-brand-icon svg, .assistant-mobile-history svg, .assistant-composer svg { width: 21px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.assistant-header > div { min-width: 0; display: grid; }.assistant-header h2 { margin: 0; color: #173e57; font-size: 17px; line-height: 1.15; }.assistant-header div span { margin-top: 3px; color: #728691; font-size: 11px; }
.assistant-close, .assistant-mobile-history { width: 36px; height: 36px; border: 0; border-radius: 9px; background: transparent; color: #607985; cursor: pointer; }.assistant-close { margin-left: auto; font-size: 25px; }.assistant-close:hover, .assistant-mobile-history:hover { background: #edf3f5; color: #173e57; }.assistant-mobile-history { display: none; place-items: center; }
.assistant-workspace { min-height: 0; flex: 1; display: flex; }
.assistant-history { width: 248px; padding: 16px 12px 12px; flex: 0 0 248px; display: flex; flex-direction: column; border-right: 1px solid #dce5e8; background: #eef3f5; }
.assistant-new-chat { height: 42px; border: 1px solid rgba(45,139,137,.38); border-radius: 10px; background: #fff; color: #286f73; font-size: 13px; font-weight: 700; cursor: pointer; }.assistant-new-chat:hover { background: #e6f5f3; }.assistant-new-chat span { margin-right: 5px; font-size: 18px; }
.assistant-history-label { margin: 22px 8px 8px; color: #7b8d96; font-size: 10px; font-weight: 800; letter-spacing: .12em; }
.assistant-history-list { min-height: 0; flex: 1; overflow-y: auto; }.assistant-history-row { margin-bottom: 4px; display: flex; align-items: center; border-radius: 9px; }.assistant-history-row:hover, .assistant-history-row.active { background: #dfeaec; }.assistant-history-select { min-width: 0; padding: 9px 5px 9px 10px; flex: 1; display: grid; gap: 2px; border: 0; background: transparent; text-align: left; cursor: pointer; }.assistant-history-select strong { overflow: hidden; color: #365665; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.assistant-history-select small { color: #82939b; font-size: 9px; }.assistant-history-delete { width: 28px; height: 28px; margin-right: 4px; border: 0; border-radius: 7px; background: transparent; color: transparent; cursor: pointer; }.assistant-history-row:hover .assistant-history-delete, .assistant-history-row.active .assistant-history-delete { color: #76909b; }.assistant-history-delete:hover { background: rgba(255,255,255,.7); color: #ad4e39 !important; }
.assistant-history-note { padding: 10px 4px 0; border-top: 1px solid #d7e2e5; color: #8999a0; font-size: 9px; text-align: center; }
.assistant-chat { min-width: 0; flex: 1; display: flex; flex-direction: column; background: #fff; }
.assistant-messages { min-height: 0; padding: 32px clamp(24px,6vw,78px); flex: 1; overflow-y: auto; scroll-behavior: smooth; }
.assistant-message { max-width: 760px; margin: 0 auto 26px; display: flex; align-items: flex-start; gap: 12px; }.assistant-message-avatar { width: 32px; height: 32px; flex: 0 0 32px; display: grid; place-items: center; border-radius: 9px; background: #e5f2f1; color: #287475; font-size: 10px; font-weight: 800; }.assistant-message--user .assistant-message-avatar { background: #e9eef1; color: #526b78; }.assistant-message > div { min-width: 0; flex: 1; }.assistant-message small { color: #71858f; font-size: 10px; font-weight: 700; }.assistant-message p { margin: 5px 0 0; color: #294957; font-size: 13px; line-height: 1.75; white-space: pre-wrap; overflow-wrap: anywhere; }
.assistant-markdown { margin-top: 6px; overflow-x: auto; color: #294957; font-size: 13px; line-height: 1.75; overflow-wrap: anywhere; }.assistant-markdown :deep(> :first-child) { margin-top: 0; }.assistant-markdown :deep(> :last-child) { margin-bottom: 0; }.assistant-markdown :deep(p) { margin: 0 0 10px; white-space: normal; }.assistant-markdown :deep(h1), .assistant-markdown :deep(h2), .assistant-markdown :deep(h3), .assistant-markdown :deep(h4) { margin: 18px 0 8px; color: #173e57; line-height: 1.4; }.assistant-markdown :deep(h1) { font-size: 19px; }.assistant-markdown :deep(h2) { padding-bottom: 5px; border-bottom: 1px solid #dce7ea; font-size: 17px; }.assistant-markdown :deep(h3) { font-size: 15px; }.assistant-markdown :deep(h4) { font-size: 13px; }.assistant-markdown :deep(ul), .assistant-markdown :deep(ol) { margin: 6px 0 12px; padding-left: 24px; }.assistant-markdown :deep(li) { margin: 3px 0; padding-left: 2px; }.assistant-markdown :deep(blockquote) { margin: 10px 0; padding: 8px 12px; border-left: 3px solid #61aaa7; border-radius: 0 7px 7px 0; background: #f0f7f7; color: #496974; }.assistant-markdown :deep(code) { padding: 2px 5px; border-radius: 5px; background: #edf2f4; color: #9a4938; font-family: Consolas, "SFMono-Regular", monospace; font-size: .9em; }.assistant-markdown :deep(pre) { margin: 10px 0 12px; padding: 12px 14px; overflow-x: auto; border: 1px solid #d6e1e4; border-radius: 9px; background: #182a34; color: #dcebee; line-height: 1.6; }.assistant-markdown :deep(pre code) { padding: 0; background: transparent; color: inherit; font-size: 11px; white-space: pre; }.assistant-markdown :deep(a) { color: #237b80; text-decoration-thickness: 1px; text-underline-offset: 2px; }.assistant-markdown :deep(a:hover) { color: #185d64; }.assistant-markdown :deep(table) { width: 100%; min-width: 420px; margin: 10px 0 12px; border-collapse: collapse; font-size: 12px; }.assistant-markdown :deep(th), .assistant-markdown :deep(td) { padding: 7px 9px; border: 1px solid #d5e1e4; text-align: left; vertical-align: top; }.assistant-markdown :deep(th) { background: #edf5f5; color: #244d5c; font-weight: 700; }.assistant-markdown :deep(hr) { margin: 16px 0; border: 0; border-top: 1px solid #d8e3e7; }
.assistant-thinking { display: flex; gap: 5px; padding-top: 7px; }.assistant-thinking i { width: 6px; height: 6px; border-radius: 50%; background: #61aaa7; animation: assistant-dot 1.2s infinite ease-in-out; }.assistant-thinking i:nth-child(2) { animation-delay: .15s; }.assistant-thinking i:nth-child(3) { animation-delay: .3s; }
.assistant-composer-area { padding: 10px clamp(24px,6vw,78px) 18px; background: linear-gradient(180deg,rgba(255,255,255,0),#fff 20%); }.assistant-quick-starts { max-width: 760px; margin: 0 auto 10px; display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 7px; }.assistant-quick-start-row { min-width: 0; display: flex; overflow: hidden; border: 1px solid #d9e5e7; border-radius: 9px; background: #f8fbfb; }.assistant-quick-start-row:hover { border-color: #68aaa8; background: #edf8f7; }.assistant-quick-start-row button { background: inherit; color: #426674; cursor: pointer; }.assistant-quick-start-question { min-width: 0; padding: 9px 8px 9px 12px; flex: 1; border: 0; font-size: 11px; text-align: left; }.assistant-quick-start-refresh { width: 34px; min-width: 34px; padding: 0; display: grid; place-items: center; border: 0; border-radius: 0; }.assistant-quick-start-refresh svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }.assistant-quick-start-row button:hover { color: #246d70; }.assistant-quick-start-refresh:hover { background: rgba(45,139,137,.08); }
.assistant-config-error, .assistant-error { max-width: 760px; margin: 0 auto 8px; padding: 8px 11px; border: 1px solid #efb8ab; border-radius: 8px; background: #fff0ec; color: #a34531; font-size: 10px; }
.assistant-composer { max-width: 760px; margin: 0 auto; padding: 8px 8px 8px 14px; display: flex; align-items: flex-end; gap: 10px; border: 1px solid #c7d8dc; border-radius: 16px; background: #fff; box-shadow: 0 8px 30px rgba(18,57,75,.1); transition: border-color .16s ease, box-shadow .16s ease; }.assistant-composer:focus-within { border-color: #4d9b99; box-shadow: 0 0 0 3px rgba(45,139,137,.1),0 10px 32px rgba(18,57,75,.12); }.assistant-composer textarea { width: 100%; height: 52px; min-height: 52px; max-height: 168px; padding: 7px 2px; flex: 1; resize: none; overflow-y: hidden; border: 0; outline: 0; background: transparent; color: #264653; font: inherit; font-size: 13px; line-height: 1.65; scrollbar-width: thin; }.assistant-composer textarea::placeholder { color: #93a4ab; }.assistant-composer textarea:disabled { color: #6f838c; cursor: wait; }.assistant-composer button { width: 40px; height: 40px; margin-bottom: 1px; flex: 0 0 40px; display: grid; place-items: center; border: 0; border-radius: 11px; background: #2d7d80; color: #fff; cursor: pointer; box-shadow: 0 4px 12px rgba(45,125,128,.2); }.assistant-composer button:disabled { background: #d4dfe2; box-shadow: none; cursor: not-allowed; }.assistant-composer button:not(:disabled):hover { background: #236b70; }
.assistant-disclaimer { display: block; max-width: 760px; margin: 7px auto 0; color: #91a0a6; font-size: 9px; text-align: center; }
.assistant-fade-enter-active, .assistant-fade-leave-active { transition: opacity .18s ease; }.assistant-fade-enter-active .assistant-panel, .assistant-fade-leave-active .assistant-panel { transition: transform .22s ease; }.assistant-fade-enter-from, .assistant-fade-leave-to { opacity: 0; }.assistant-fade-enter-from .assistant-panel, .assistant-fade-leave-to .assistant-panel { transform: translateX(40px); }
@keyframes assistant-dot { 0%,60%,100% { transform: translateY(0); opacity: .4; } 30% { transform: translateY(-4px); opacity: 1; } }
@media (max-width: 720px) { .assistant-panel { width: 100vw; }.assistant-mobile-history { display: grid; }.assistant-brand-icon { display: none; }.assistant-history { position: absolute; z-index: 3; top: 68px; bottom: 0; left: 0; transform: translateX(-100%); box-shadow: 12px 0 32px rgba(9,35,48,.2); transition: transform .2s ease; }.assistant-history--open { transform: translateX(0); }.assistant-messages { padding: 24px 17px; }.assistant-composer-area { padding-inline: 14px; }.assistant-quick-starts { grid-template-columns: 1fr; } }
</style>
