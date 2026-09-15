import { reactive } from 'vue'


export function createStreamingAssistantMessage() {
  return reactive({ role: 'assistant', content: '', sources: [] })
}
