import { ref } from 'vue'


export function useTimedConfirmation({
  timeout = 4000,
  schedule = (callback, delay) => window.setTimeout(callback, delay),
  cancelSchedule = handle => window.clearTimeout(handle),
} = {}) {
  const pendingId = ref('')
  let timer

  function clear() {
    pendingId.value = ''
    if (timer !== undefined) cancelSchedule(timer)
    timer = undefined
  }

  function request(id) {
    if (pendingId.value === id) {
      clear()
      return true
    }
    clear()
    pendingId.value = id
    timer = schedule(clear, timeout)
    return false
  }

  return { pendingId, request, clear, dispose: clear }
}
