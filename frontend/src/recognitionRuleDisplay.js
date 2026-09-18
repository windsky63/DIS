// Fixed EP3D profile values mirror Ep3dCalloutProfile in dual_pdf_topology.py.
// They are informational, not additional configurable options.
export const EP3D_FIXED_PROFILE = {
  name: 'indonesia-solid-dot', containerShapes: '菱形、圆形、椭圆',
  minimumLeaderLength: 8, maximumLeaderLength: 180,
  minimumContainerExtent: 8, maximumContainerWidth: 90, maximumContainerHeight: 60,
}

export const EP3D_COMPONENT_RULES = [
  ['组件编号表达式', '(?:FL|SP|V)\\d+（忽略大小写、完整匹配）'],
  ['阀门', 'V 编号框及引线；局部图元验证双三角阀门或 Z 形箭头阀门'],
  ['法兰', 'FL 编号框及引线；局部平行线图元验证'],
  ['支架', 'SP 编号框及引线；局部平行线图元验证'],
  ['组件定位', '以标识框和引线关联定位，局部几何用于验证及修正子类型'],
]
export const DESIGN_COMPONENT_RULES = [
  ['阀门', '材料表中的阀门类型与图中方框材料编号引线联合识别，不仅依赖外形'],
  ['法兰', '方框 F 引线标识与管线上的矩形法兰装配几何合并识别'],
  ['支架', '管线两侧短平行线及 S 编号引线标识合并、去重'],
  ['支架编号表达式', 'S\\d+[A-Z0-9-]*（忽略大小写、完整匹配）'],
  ['图元归属', '焊口的圆、X 和方括号图元不重复用于组件识别'],
  ['出图匹配', '焊口、阀门、法兰、支架复用管线拓扑匹配流程，类型不交叉匹配'],
]

export function designRuleParameters(config) {
  const p = { blackCircleEnabled: true, baseVectorShape: 'circle', approximateCircleEnabled: false,
    plainCircleEnabled: true, prefabricatedXEnabled: true, socketThreadBracketEnabled: true,
    mainPipeOnly: true, includeResearchFallback: false, minimumDiameter: 1.5, maximumDiameter: 14,
    darkThreshold: .25, mainPipeTolerance: 3.5, excludeDashedInPlacement: true, ...config.placementSymbols }
  const enabled = value => value ? '开启' : '关闭'
  return [
    ['检测模式', (config.detectionMode || 'placement') === 'comparison' ? '对比识别' : '设计图落图识别'],
    ['黑色圆形检测', enabled(p.blackCircleEnabled)], ['基础图元形状', p.baseVectorShape === 'circle' ? '圆形' : p.baseVectorShape],
    ['低边数近似圆补充', enabled(p.approximateCircleEnabled)], ['普通黑圆焊口', enabled(p.plainCircleEnabled)],
    ['黑圆 + X 预制焊口', enabled(p.prefabricatedXEnabled)], ['黑圆 + 方括号承插 / 螺纹焊', enabled(p.socketThreadBracketEnabled)],
    ['必须位于主管线', enabled(p.mainPipeOnly)], ['圆直径范围', `${p.minimumDiameter}–${p.maximumDiameter} pt`],
    ['主管投影容差', `${p.mainPipeTolerance} pt`], ['黑色阈值', p.darkThreshold],
    ['排除虚线', enabled(p.excludeDashedInPlacement)], ['研究候选回退', enabled(p.includeResearchFallback)],
    ['固定符号策略', config.fixedSymbolPolicy || 'complete-signature'], ['填充标记策略', config.markerPolicy || 'strong-process-projection'],
    ['最低候选置信度', config.minimumConfidence ?? 0],
  ]
}
