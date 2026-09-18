import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { createProjectProfiles, createReferenceRule, createRecognitionRules, validateProjectProfiles, validateRecognitionRules, recognitionConfigForFiles } from '../src/projectProfiles.js'


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

test('a project supports EP3D plus multiple independently configured contractors', () => {
  const project = createProjectProfiles()[0]
  project.recognitionRules.referenceRules.push(createReferenceRule('contractor', '施工单位 B'))
  const rules = project.recognitionRules.referenceRules
  assert.deepEqual(rules.map(rule => rule.kind), ['ep3d', 'contractor', 'contractor'])
  rules[2].options.maximumFrameGap = 40
  assert.equal(rules[1].options.maximumFrameGap, 24)
  validateProjectProfiles([project])
})

test('reactive project profiles can be normalized and invalid configs fail validation', () => {
  const projects = ref(createProjectProfiles())
  assert.equal(createRecognitionRules(projects.value[0].recognitionRules).referenceRules.length, 2)
  const config = createRecognitionRules()
  config.referenceRules[1].options.labelPattern = '['
  assert.throws(() => validateRecognitionRules(config), /表达式无效/)
  config.referenceRules[1].options.labelPattern = '\\d+'
  config.referenceRules[1].options.maximumFrameGap = -1
  assert.throws(() => validateRecognitionRules(config), /maximumFrameGap/)
})

test('reference assignment slots are remapped to upload order and job config is immutable', () => {
  const config = createRecognitionRules()
  config.referenceRuleAssignments = { '2': 'contractor', '4': 'ep3d' }
  const snapshot = recognitionConfigForFiles(config, [{ weldMarkerReferenceIndex: 2 }, { weldMarkerReferenceIndex: 4 }])
  assert.deepEqual(snapshot.referenceRuleAssignments, { '0': 'contractor', '1': 'ep3d' })
  config.referenceRules[1].options.maximumFrameGap = 40
  assert.equal(snapshot.referenceRules[1].options.maximumFrameGap, 24)
})
