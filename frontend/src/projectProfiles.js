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
    recognitionRules: structuredClone(CHENGDA_INDONESIA_RULES),
  }]
}
