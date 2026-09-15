import { onBeforeUnmount, onMounted, ref } from 'vue'


export function useUploadCloseGuard({ dirty = ref(false), uncertain = ref(false) } = {}) {
  const commitPending = ref(false)

  function warnBeforeUnload(event) {
    if (!commitPending.value && !dirty.value && !uncertain.value) return
    event.preventDefault()
    // Browsers intentionally render their own localized confirmation text.
    event.returnValue = ''
    return ''
  }

  onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
  onBeforeUnmount(() => window.removeEventListener('beforeunload', warnBeforeUnload))
  return commitPending
}
