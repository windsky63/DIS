import assert from 'node:assert/strict'
import test from 'node:test'
import { computed, ref } from 'vue'

import { useWorkspaceHistory } from './composables/useWorkspaceHistory.js'

test('workspace history restores page edits through undo and redo', () => {
  const result = ref({ pages: [{ page: 1, candidates: [{ id: 'w1', number: '1' }] }] })
  const pages = computed(() => result.value.pages)
  const pageData = computed(() => pages.value[0])
  const markerStyle = ref({ color: '#f00' })
  const componentMarkerStyles = ref({ valve: { color: '#00f' } })
  const projectResults = ref([result.value])
  const selectedId = ref('w1')
  const notices = []
  const audits = []
  let saves = 0
  const history = useWorkspaceHistory({
    result,
    pages,
    pageData,
    markerStyle,
    componentMarkerStyles,
    projectResults,
    activeProjectIndex: ref(0),
    selectedId,
    cloneValue: value => JSON.parse(JSON.stringify(value)),
    scheduleSave: () => { saves += 1 },
    showNotice: value => notices.push(value),
    logAudit: (action, details) => audits.push({ action, details }),
  })

  history.begin('修改编号')
  result.value.pages[0].candidates[0].number = '2'
  history.commit()
  assert.equal(history.canUndo.value, true)
  assert.equal(saves, 1)

  history.undo()
  assert.equal(result.value.pages[0].candidates[0].number, '1')
  assert.equal(selectedId.value, '')
  assert.equal(notices.at(-1), '已撤销：修改编号')

  history.redo()
  assert.equal(result.value.pages[0].candidates[0].number, '2')
  assert.equal(audits.at(-1).action, 'redo')
})

test('workspace history ignores commits without changes', () => {
  const result = ref({ pages: [{ page: 1, candidates: [] }] })
  const pages = computed(() => result.value.pages)
  const history = useWorkspaceHistory({
    result,
    pages,
    pageData: computed(() => pages.value[0]),
    markerStyle: ref({}),
    componentMarkerStyles: ref({}),
    projectResults: ref([result.value]),
    activeProjectIndex: ref(0),
    selectedId: ref(''),
    cloneValue: value => JSON.parse(JSON.stringify(value)),
    scheduleSave() {},
    showNotice() {},
    logAudit() {},
  })
  history.begin('无变化')
  history.commit()
  assert.equal(history.canUndo.value, false)
})

test('workspace history keeps only the 30 most recent page edits', () => {
  const result = ref({ pages: [{ page: 1, candidates: [{ id: 'w1', number: '0' }] }] })
  const pages = computed(() => result.value.pages)
  const history = useWorkspaceHistory({
    result,
    pages,
    pageData: computed(() => pages.value[0]),
    markerStyle: ref({}),
    componentMarkerStyles: ref({}),
    projectResults: ref([result.value]),
    activeProjectIndex: ref(0),
    selectedId: ref(''),
    cloneValue: value => JSON.parse(JSON.stringify(value)),
    scheduleSave() {},
    showNotice() {},
    logAudit() {},
  })

  for (let edit = 1; edit <= 31; edit += 1) {
    history.begin(`edit ${edit}`)
    result.value.pages[0].candidates[0].number = String(edit)
    history.commit()
  }

  assert.equal(history.undoStack.value.length, 30)
  assert.equal(history.undoStack.value[0].value.candidates[0].number, '1')
})

test('workspace history caps snapshots across every page in the task', () => {
  const currentPage = ref(1)
  const result = ref({ jobId: 'large-task', pages: Array.from({ length: 5 }, (_, index) => ({
    page: index + 1,
    candidates: [{ id: `w${index + 1}`, number: '0' }],
  })) })
  const pages = computed(() => result.value.pages)
  const pageData = computed(() => pages.value.find(page => page.page === currentPage.value))
  const history = useWorkspaceHistory({
    result,
    pages,
    pageData,
    markerStyle: ref({}),
    componentMarkerStyles: ref({}),
    projectResults: ref([result.value]),
    activeProjectIndex: ref(0),
    selectedId: ref(''),
    cloneValue: value => JSON.parse(JSON.stringify(value)),
    scheduleSave() {},
    showNotice() {},
    logAudit() {},
    taskLimit: 3,
  })

  for (let page = 1; page <= 5; page += 1) {
    currentPage.value = page
    history.begin(`edit page ${page}`)
    pageData.value.candidates[0].number = String(page)
    history.commit()
  }

  currentPage.value = 1
  assert.equal(history.canUndo.value, false)
  currentPage.value = 2
  assert.equal(history.canUndo.value, false)
  currentPage.value = 3
  assert.equal(history.canUndo.value, true)
  currentPage.value = 4
  assert.equal(history.canUndo.value, true)
  currentPage.value = 5
  assert.equal(history.canUndo.value, true)
})
