export function createJsonRequester({
  fetchRequest = (...args) => globalThis.fetch(...args),
  setTimer = (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimer = handle => globalThis.clearTimeout(handle),
} = {}) {
  return async function request(url, options = {}) {
    const method = String(options.method || 'GET').toUpperCase()
    const mutation = !['GET', 'HEAD'].includes(method)
    const { timeoutMs = mutation ? 45_000 : 30_000, signal, ...fetchOptions } = options
    const controller = new AbortController()
    let timer, abortListener
    const deadline = new Promise((_, reject) => {
      timer = setTimer(() => {
        const error = new Error(mutation ? '请求超时，操作结果尚未确认，请勿重复提交。' : '读取超时，请检查网络后重试。')
        error.code = 'REQUEST_TIMEOUT'
        error.outcomeUnknown = mutation
        reject(error)
        controller.abort()
      }, timeoutMs)
      abortListener = () => { reject(new DOMException('请求已取消', 'AbortError')); controller.abort() }
      if (signal?.aborted) abortListener()
      else signal?.addEventListener('abort', abortListener, { once: true })
    })
    try {
      const operation = (async () => {
        const response = await fetchRequest(url, {
          ...fetchOptions, signal: controller.signal, credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', ...(fetchOptions.headers || {}) },
        })
        let payload
        try { payload = await response.json() } catch (cause) {
          if (response.ok) {
            const error = new Error(mutation ? '服务器回执不完整，操作结果尚未确认。' : '服务器读取回执不完整，请重试。', { cause })
            error.code = 'RESPONSE_INCOMPLETE'; error.outcomeUnknown = mutation
            throw error
          }
          payload = {}
        }
        if (!response.ok) {
          const error = new Error(payload.error || `请求失败：HTTP ${response.status}`)
          error.status = response.status; error.payload = payload
          if (response.status === 401) globalThis.window?.dispatchEvent?.(new Event('drawing-marker:authentication-required'))
          throw error
        }
        return payload
      })()
      return await Promise.race([operation, deadline])
    } catch (cause) {
      if (cause?.status || cause?.code || cause?.name === 'AbortError') throw cause
      const error = new Error(mutation ? '连接中断，操作结果尚未确认，请勿重复提交。' : '无法读取服务器数据，请检查网络后重试。', { cause })
      error.code = 'NETWORK_ERROR'; error.outcomeUnknown = mutation
      throw error
    } finally {
      clearTimer(timer)
      signal?.removeEventListener('abort', abortListener)
    }
  }
}
