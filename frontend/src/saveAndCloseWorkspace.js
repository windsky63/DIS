export async function saveAndCloseWorkspace({ persist, leave, close, showTasks }) {
  await persist()
  await leave()
  close()
  await showTasks()
}
