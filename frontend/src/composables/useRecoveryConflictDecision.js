import { ref } from 'vue'


export function useRecoveryConflictDecision() {
  const dialog = ref(false)
  const info = ref(null)
  let pendingResolve = null

  function resolve(choice = 'cancel') {
    const finish = pendingResolve
    pendingResolve = null
    dialog.value = false
    info.value = null
    finish?.(choice)
  }

  function request(nextInfo) {
    if (pendingResolve) resolve('cancel')
    info.value = nextInfo
    dialog.value = true
    return new Promise(resolvePromise => { pendingResolve = resolvePromise })
  }

  function dispose() { resolve('cancel') }
  return { dialog, info, request, resolve, dispose }
}
