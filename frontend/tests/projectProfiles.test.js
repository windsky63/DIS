import assert from 'node:assert/strict'
import test from 'node:test'


test('the existing recognition rules belong to the Chengda Indonesia project', async () => {
  const profiles = await import('../src/projectProfiles.js').catch(() => ({}))
  assert.equal(typeof profiles.createProjectProfiles, 'function')
  const projects = profiles.createProjectProfiles()
  assert.equal(projects.length, 1)
  assert.equal(projects[0].id, 'chengda-indonesia')
  assert.equal(projects[0].name, '成达印尼项目')
  assert.equal(projects[0].recognitionRules.markerPolicy, 'strong-process-projection')
})

test('project profile factories return independent recognition rule objects', async () => {
  const { createProjectProfiles } = await import('../src/projectProfiles.js')
  const first = createProjectProfiles()
  first[0].recognitionRules.minimumConfidence = .8
  assert.equal(createProjectProfiles()[0].recognitionRules.minimumConfidence, 0)
})
