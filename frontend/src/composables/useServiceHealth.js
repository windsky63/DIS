import { ref } from 'vue'

export function useServiceHealth({ client, pollInterval = 10000, afterRefresh } = {}) {
  const backend = ref('checking')
  const mineru = ref('checking')
  const mineruMessage = ref('正在连接')
  let timer

  async function refresh() {
    try {
      const status = await client.health()
      backend.value = status.status === 'ready' ? 'ready' : 'offline'
      mineru.value = status.mineru?.status || 'offline'
      mineruMessage.value = status.mineru?.message || (mineru.value === 'ready' ? '服务就绪' : '服务未就绪')
    } catch {
      backend.value = 'offline'
      mineru.value = 'offline'
      mineruMessage.value = '本地后端未连接，无法检测 MinerU'
    }
    await afterRefresh?.()
  }

  function start() {
    stop()
    timer = window.setInterval(refresh, pollInterval)
  }

  function stop() {
    if (timer) window.clearInterval(timer)
    timer = undefined
  }

  return { backend, mineru, mineruMessage, refresh, start, stop }
}
