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
