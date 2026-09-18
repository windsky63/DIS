const CHENGDA_INDONESIA_RULES = {
  fixedSymbolPolicy: 'complete-signature',
  markerPolicy: 'strong-process-projection',
  minimumConfidence: 0,
  placementSymbols: {
    blackCircleEnabled: true,
    baseVectorShape: 'circle',
    approximateCircleEnabled: false,
    plainCircleEnabled: true,
    prefabricatedXEnabled: true,
    socketThreadBracketEnabled: true,
    mainPipeOnly: true,
    includeResearchFallback: false,
    minimumDiameter: 1.5,
    maximumDiameter: 14,
    darkThreshold: 0.25,
    mainPipeTolerance: 3.5,
  },
}

export function createProjectProfiles() {
  return [{
    id: 'chengda-indonesia',
    name: '成达印尼项目',
    description: '当前焊口、阀门、法兰和支架识别规则',
    recognitionRules: createRecognitionRules(),
  }]
}

export const PROJECT_PROFILES_KEY = 'drawing-marker.project-profiles.v1'

export function createReferenceRule(kind = 'contractor', name = '') {
  return {
    id: `${kind}-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`,
    name: name || (kind === 'ep3d' ? 'EP3D 出图' : '施工单位出图'), kind,
    options: kind === 'ep3d' ? { labelPattern: '(?:F|FS|T)\\d+', rootStrategy: 'solid-dot-required' } : {
      labelPattern: '[A-Z]*\\d+(?:[-.]\\d+)*', maximumFrameGap: 24,
      minimumFrameDiameter: 6, maximumFrameDiameter: 72,
      minimumLeaderLength: 4, maximumLeaderLength: 250, segmentGap: 3,
      maximumLeaderSegments: 8, leaderColor: 'any', redMinimum: .8, otherColorMaximum: .3,
    },
  }
}

export function createRecognitionRules(raw = {}) {
  raw = JSON.parse(JSON.stringify(raw))
  const rules = {
    ...structuredClone(CHENGDA_INDONESIA_RULES), ...structuredClone(raw),
    placementSymbols: { ...CHENGDA_INDONESIA_RULES.placementSymbols, ...raw.placementSymbols },
    referenceRules: raw.referenceRules?.length ? structuredClone(raw.referenceRules) : [
      { ...createReferenceRule('ep3d'), id: 'ep3d' },
      { ...createReferenceRule('contractor'), id: 'contractor' },
    ],
    referenceRuleAssignments: { ...raw.referenceRuleAssignments },
  }
  rules.referenceRules = rules.referenceRules.map(rule => ({
    ...rule, options: { ...createReferenceRule(rule.kind).options, ...rule.options },
  }))
  rules.activeReferenceRuleId = raw.activeReferenceRuleId || rules.referenceRules[0].id
  return rules
}

export function validateProjectProfiles(projects) {
  if (!Array.isArray(projects) || !projects.length || projects.length > 50) throw new Error('项目数量必须为 1 至 50 个')
  const ids = new Set()
  return projects.map(project => {
    if (!project?.id || ids.has(project.id) || !String(project.name || '').trim()) throw new Error('项目 ID 必须唯一，名称不能为空')
    ids.add(project.id)
    const recognitionRules = createRecognitionRules(project.recognitionRules)
    validateRecognitionRules(recognitionRules)
    return { id: String(project.id), name: String(project.name), description: String(project.description || ''), recognitionRules }
  })
}

export function validateRecognitionRules(config) {
  if (!config.referenceRules?.length || config.referenceRules.length > 64) throw new Error('出图来源规则必须为 1 至 64 个')
  const ids = new Set()
  for (const rule of config.referenceRules) {
    if (!rule.id || ids.has(rule.id) || !['ep3d', 'contractor'].includes(rule.kind) || !rule.name?.trim()) throw new Error('出图规则 ID 必须唯一，名称和类型必须有效')
    ids.add(rule.id)
    const pattern = rule.options?.labelPattern || ''
    if (!pattern || pattern.length > 120) throw new Error('编号表达式不能为空，且不能超过 120 个字符')
    try { new RegExp(pattern, 'i') } catch { throw new Error(`“${rule.name}”的编号表达式无效`) }
    if (rule.kind === 'contractor') {
      const bounds = { maximumFrameGap: [0, 200], minimumFrameDiameter: [1, 200], maximumFrameDiameter: [1, 300],
        minimumLeaderLength: [.5, 300], maximumLeaderLength: [1, 1000], segmentGap: [0, 30],
        maximumLeaderSegments: [1, 20], redMinimum: [0, 1], otherColorMaximum: [0, 1] }
      for (const [key, [low, high]] of Object.entries(bounds)) {
        const value = Number(rule.options[key])
        if (!Number.isFinite(value) || value < low || value > high) throw new Error(`“${rule.name}”的 ${key} 必须在 ${low} 至 ${high} 之间`)
      }
      if (rule.options.minimumFrameDiameter > rule.options.maximumFrameDiameter || rule.options.minimumLeaderLength > rule.options.maximumLeaderLength) throw new Error('最小值不能大于最大值')
      if (!['any', 'red'].includes(rule.options.leaderColor)) throw new Error('引线颜色规则无效')
    } else if (!['solid-dot-required', 'leader-end-on-process', 'symbol-or-process-end'].includes(rule.options.rootStrategy)) {
      throw new Error('EP3D 端点策略无效')
    }
  }
  if (!ids.has(config.activeReferenceRuleId) || Object.values(config.referenceRuleAssignments || {}).some(id => !ids.has(id))) throw new Error('默认规则或文件指定的规则不存在')
  return config
}

export function recognitionConfigForFiles(config, files) {
  validateRecognitionRules(config)
  const snapshot = JSON.parse(JSON.stringify(config))
  snapshot.referenceRuleAssignments = Object.fromEntries(files.map((file, index) => [String(index),
    config.referenceRuleAssignments?.[String(file.weldMarkerReferenceIndex ?? index)],
  ]).filter(([, id]) => id))
  return { ...snapshot, detectionMode: 'placement' }
}

export function loadProjectProfiles(storage = globalThis.localStorage) {
  try {
    const saved = storage?.getItem(PROJECT_PROFILES_KEY)
    return saved ? validateProjectProfiles(JSON.parse(saved)) : createProjectProfiles()
  } catch { return createProjectProfiles() }
}
