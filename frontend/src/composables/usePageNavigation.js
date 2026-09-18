import { computed } from 'vue'

export function resolvePageJump(value, availablePages) {
  const text = String(value ?? '').trim()
  if (!/^\d+$/.test(text)) return null
  const page = Number(text)
  return availablePages.includes(page) ? page : null
}

export function pageLockFallbacks(targetPage, currentPage, availablePages) {
  const target = Number(targetPage)
  const current = Number(currentPage)
  const ordered = availablePages.map(Number).filter(Number.isFinite).sort((a, b) => a - b)
  const targetIndex = ordered.indexOf(target)
  if (targetIndex < 0 || target === current) return []
  return target > current
    ? ordered.slice(targetIndex + 1)
    : ordered.slice(0, targetIndex).reverse()
}

export function usePageNavigation({ result, pages, currentPage, previewPage, targetDocument, pageButtonsPerGroup, changePage, pageMatchSummary, pageLocks = null, clientInstanceId = '' }) {
  const shownPage = computed(() => result.value ? currentPage.value : previewPage.value)
  const targetPageCount = computed(() => targetDocument.value?.numPages || 0)
  const navigationPages = computed(() => result.value
    ? pages.value.map(page => Number(page.page)).filter(Number.isFinite).sort((a, b) => a - b)
    : Array.from({ length: targetPageCount.value }, (_, index) => index + 1))
  const navigationIndex = computed(() => navigationPages.value.indexOf(shownPage.value))
  const groupStart = computed(() => Math.max(0, Math.floor(Math.max(0, navigationIndex.value) / pageButtonsPerGroup.value) * pageButtonsPerGroup.value))
  const visiblePages = computed(() => navigationPages.value.slice(groupStart.value, groupStart.value + pageButtonsPerGroup.value))
  const canShowPreviousGroup = computed(() => groupStart.value > 0)
  const canShowNextGroup = computed(() => groupStart.value + pageButtonsPerGroup.value < navigationPages.value.length)

  function navigate(page) {
    void changePage(page, pageLockFallbacks(page, shownPage.value, navigationPages.value))
  }
  function step(direction) {
    const next = navigationPages.value[navigationIndex.value + direction]
    if (Number.isFinite(next)) navigate(next)
  }
  function stepGroup(direction) {
    const targetIndex = direction > 0 ? groupStart.value + pageButtonsPerGroup.value : Math.max(0, groupStart.value - pageButtonsPerGroup.value)
    const next = navigationPages.value[targetIndex]
    if (Number.isFinite(next)) navigate(next)
  }
  function jump(value) {
    const page = resolvePageJump(value, navigationPages.value)
    if (page === null) return false
    navigate(page)
    return true
  }
  function pageClasses(pageNumber) {
    const page = result.value ? pages.value.find(item => Number(item.page) === pageNumber) : null
    const complete = page ? pageMatchSummary(page).complete : false
    const lock = pageLocks?.value?.find(item => Number(item.page) === Number(pageNumber))
    const lockedByOther = Boolean(lock && lock.clientInstanceId !== clientInstanceId)
    return {
      'page-status-button--active': shownPage.value === pageNumber,
      'page-status-button--preview': !result.value,
      'page-status-button--complete': Boolean(page && complete),
      'page-status-button--incomplete': Boolean(page && !complete),
      'page-status-button--locked': lockedByOther,
    }
  }
  return { shownPage, targetPageCount, navigationPages, visiblePages, canShowPreviousGroup, canShowNextGroup, navigate, step, stepGroup, jump, pageClasses }
}
