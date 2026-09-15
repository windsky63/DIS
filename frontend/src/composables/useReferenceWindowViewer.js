import { ref, shallowRef } from 'vue'


function boundedPage(value, pageCount = Number.MAX_SAFE_INTEGER) {
  return Math.max(1, Math.min(Math.max(1, Number(pageCount) || 1), Number(value) || 1))
}

export function createReferenceViewerState() {
  const following = ref(true)
  const latest = shallowRef(null)
  const active = shallowRef(null)
  const currentPage = ref(1)

  function apply(snapshot) {
    if (!snapshot) {
      active.value = null
      currentPage.value = 1
      return
    }
    active.value = snapshot
    currentPage.value = boundedPage(snapshot.page)
  }

  function accept(snapshot) {
    latest.value = snapshot || null
    if (following.value) apply(snapshot)
  }

  function setFollowing(value) {
    following.value = Boolean(value)
    if (following.value) apply(latest.value)
  }

  function goToPage(page, pageCount) {
    currentPage.value = boundedPage(page, pageCount)
  }

  function navigate(page, pageCount) {
    following.value = false
    goToPage(page, pageCount)
  }

  return { following, latest, active, currentPage, accept, setFollowing, goToPage, navigate }
}
