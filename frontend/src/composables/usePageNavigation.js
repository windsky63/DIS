import { computed } from 'vue'

export function resolvePageJump(value, availablePages) {
  const text = String(value ?? '').trim()
  if (!/^\d+$/.test(text)) return null
  const page = Number(text)
  return availablePages.includes(page) ? page : null
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

  function step(direction) {
    const next = navigationPages.value[navigationIndex.value + direction]
    if (Number.isFinite(next)) void changePage(next)
  }
  function stepGroup(direction) {
    const targetIndex = direction > 0 ? groupStart.value + pageButtonsPerGroup.value : Math.max(0, groupStart.value - pageButtonsPerGroup.value)
    const next = navigationPages.value[targetIndex]
    if (Number.isFinite(next)) void changePage(next)
  }
  function jump(value) {
    const page = resolvePageJump(value, navigationPages.value)
    if (page === null) return false
    void changePage(page)
    return true
  }
  function pageClasses(pageNumber) {
    const page = result.value ? pages.value.find(item => Number(item.page) === pageNumber) : null
    const complete = page ? pageMatchSummary(page).complete : false
    const lock = pageLocks?.value?.find(item => Number(item.page) === Number(pageNumber))
    const lockedByOther = Boolean(lock && lock.clientInstanceId !== clientInstanceId)
    return {
      'page-status-button--active': shownPage.value === pageNumber,
      'page-status-button--complete': Boolean(page && complete),
      'page-status-button--incomplete': Boolean(page && !complete),
      'page-status-button--locked': lockedByOther,
    }
  }
  return { shownPage, targetPageCount, navigationPages, visiblePages, canShowPreviousGroup, canShowNextGroup, step, stepGroup, jump, pageClasses }
}
