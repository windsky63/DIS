import { tutorialSteps } from './tutorialSteps.js'

const welcome = tutorialSteps[0]

export const tutorialStepsById = Object.freeze({
  overview: Object.freeze([
    { ...welcome, stage: '认识系统', title: '图纸标识识别系统', description: '本教程只介绍系统定位、服务状态和 Header 功能入口，不重复图纸解析、对照核查或多人审核的具体操作。', tips: ['需要学习具体流程时，可从教程目录选择对应专题。'], target: '.app-title' },
    { ...tutorialSteps[1], stage: '认识 Header', title: '系统标题与功能范围', description: 'Header 左侧显示系统名称，下方列出当前支持的焊口、阀门、法兰和支架拓扑识别。', tips: ['右侧按钮将在后续步骤逐一介绍。'], target: '.app-title' },
    {
      stage: '认识 Header', icon: '⇅', title: '图元引擎状态与解析队列',
      description: '最左侧的节点图标同时显示后端图元引擎连接状态，也是解析任务队列入口。点击后可查看任务进度、展开任务详情、恢复核对、归档或删除。',
      tips: ['状态点为就绪时可以正常提交解析。', '任务详情会显示逐页审核与保存记录。'], target: '[data-tour="analysis-queue-button"]',
    },
    {
      stage: '认识 Header', icon: 'M', title: 'MinerU 服务状态',
      description: 'M 图标显示 MinerU 文档解析服务是否就绪。该服务用于系统需要的文档内容处理；悬停图标可以查看当前连接说明。',
      tips: ['检测中时可稍候再查看。', '离线状态不会改变已经完成的图元识别结果。'], target: '[data-tour="mineru-status"]',
    },
    {
      stage: '认识 Header', icon: '▰', title: '切换识别项目',
      description: '文件夹图标用于切换当前项目。项目决定识别规则、默认配置和任务归属；切换后 Header 会显示当前项目名称。',
      tips: ['现有识别规则属于成达印尼项目。', '上传和解析前应先确认项目。'], target: '[data-tour="project-switch-button"]',
    },
    {
      stage: '认识 Header', icon: 'AI', title: '打开 AI 助手',
      description: '气泡图标打开 AI 助手。助手会结合当前项目、任务、页面、编辑模式和选中标识提供操作说明，也可以通过常见问题快速提问。',
      tips: ['常见问题一次随机显示四个。', '每个问题右侧按钮可单独刷新。'], target: '[data-tour="assistant-button"]',
    },
    { ...tutorialSteps[2], stage: '帮助与设置', description: '指南针图标打开教程目录。教程按快速入门、解析编号、对照核查与编辑、任务队列与多人审核分类，可以随时重新选择专题。' },
    { ...tutorialSteps[3], stage: '帮助与设置', description: '问号图标打开快捷键面板，集中列出解析、保存、修改模式、新增、复制粘贴、页面切换、画布缩放和编号交换等按键。' },
    { ...tutorialSteps[4], stage: '帮助与设置', description: '齿轮图标打开系统设置，可管理界面抽屉、消息提示、Tooltip、页码数量、草稿、性能模式、默认标识外观、默认引线长度和快捷键。' },
    {
      stage: '帮助与设置', icon: '👤', title: '用户中心与退出登录',
      description: '最右侧用户图标显示当前登录用户、账号创建时间并提供退出登录。解析任务创建人、页面审核人和保存记录均与登录账号关联。',
      tips: ['多人使用时不要共用同一账号。', '退出前先保存并关闭当前工作区。'], target: '[data-tour="user-menu-button"]',
    },
  ]),
  analysis: Object.freeze([
    { ...welcome, stage: '准备开始' },
    ...tutorialSteps.slice(5, 12).map((step, index) => ({ ...step, stage: index < 4 ? '准备与配置' : '提交解析' })),
  ]),
  review: Object.freeze([
    { ...welcome, stage: '准备开始' },
    ...tutorialSteps.slice(12, 16).map((step, index) => ({ ...step, stage: index < 2 ? '对照核查' : '画布校对' })),
    {
      stage: '画布校对', icon: '+', title: '补充普通标识',
      description: '进入 W、V、F 或 S 模式，将鼠标移到图纸位置并按 A，新增对应的焊口、阀门、法兰或支架。',
      tips: ['线端点与整体移动点只在对应编辑模式显示。'],
      target: '[data-tour="canvas-toolbar"]',
    },
    {
      stage: '画布校对', icon: 'M', title: '新增特殊标识',
      description: '进入 M 全局模式后按 A 新增特殊标识；选中后可在右侧为这个对象单独设置外观。',
      tips: ['特殊标识的样式不会影响其他标识。'],
      target: '[data-tour="canvas-toolbar"]',
    },
    { stage: '右侧校对', icon: '◫', title: '调整普通标识统一外观', description: '在分类标签间切换，分别设置焊口、阀门、法兰和支架的外形、框尺寸、字号、线宽、颜色和填充。', tips: ['这里的修改会影响所选分类的全部标识。'], target: '[data-tour="marker-appearance"]' },
    { stage: '右侧校对', icon: '◎', title: '手动执行位置优化', description: '点击此按钮才会重新排列当前页标签。除解析完成时的自动优化外，撤销和其他编辑不会触发位置优化。', tips: ['优化范围仅限当前页。'], target: '[data-tour="position-optimization"]' },
    { stage: '右侧校对', icon: '▣', title: '查看选中对象并完成校对', description: '选中对象后可修改编号并查看证据、置信度和参考身份。普通标识可排除误识别对象，特殊标识在这里逐个设置独立外观。', tips: ['特殊标识默认框尺寸为 20、字号为 12。'], target: '[data-tour="selected-object"]', ensureSelection: true },
  ]),
  collaboration: Object.freeze([
    { ...welcome, stage: '准备开始' },
    {
      stage: '任务管理', icon: '⇅', title: '打开解析任务队列',
      description: 'Header 的状态按钮是任务队列入口。点击任一任务行可展开详情；展开另一任务时，当前任务会自动收起。',
      tips: ['任务操作按钮不会误触发展开。', '当前任务和已归档任务分开展示。'],
      target: '[data-tour="analysis-queue-button"]',
    },
    {
      stage: '任务管理', icon: '↕', title: '调整等待顺序与任务状态',
      description: '等待中的任务可以上移或下移；正在解析的任务不能重新排序。完成后可恢复核对、归档，或在确认后永久删除。',
      tips: ['归档不会删除数据。', '正在核对的任务不能永久删除。'],
      target: '[data-tour="analysis-queue-button"]',
    },
    {
      stage: '多人审核', icon: '👥', title: '按页协同审核',
      description: '不同用户可以同时审核同一任务的不同页面。页面锁防止同一页被并发覆盖，切页和关闭工作区前会先保存修改。',
      tips: ['被他人占用的页面会显示锁定用户。', '页面锁失效时当前页会转为只读。'],
      target: '[data-tour="canvas-toolbar"]',
    },
    {
      stage: '多人审核', icon: '🗂', title: '查看逐页修改记录',
      description: '展开任务后可以查看图纸数、解析页数、审核人，以及每一页由哪些用户保存过、各自保存次数和最后保存时间。',
      tips: ['每次成功保存都会写入任务数据库。', '尚未保存修改的页面会明确标注。'],
      target: '[data-tour="analysis-queue-button"]',
    },
  ]),
})

export const tutorialCatalog = Object.freeze([
  { id: 'overview', icon: '◉', title: '系统介绍', description: '了解系统定位、服务状态和 Header 中的各个功能入口。', level: '介绍', duration: '约 4 分钟', requiresSample: false, steps: tutorialStepsById.overview.length },
  { id: 'analysis', icon: '✦', title: '图纸解析与编号', description: '准备图纸与对照资料、配置编号并提交解析。', level: '基础流程', duration: '约 5 分钟', requiresSample: true, steps: tutorialStepsById.analysis.length },
  { id: 'review', icon: '⌖', title: '对照核查与标识编辑', description: '定位参考元件、校对结果、新增标识并保存导出。', level: '核心操作', duration: '约 6 分钟', requiresSample: true, steps: tutorialStepsById.review.length },
  { id: 'collaboration', icon: '👥', title: '任务队列与多人审核', description: '管理任务顺序、页面锁、审核人与逐页修改记录。', level: '协作', duration: '约 4 分钟', requiresSample: true, steps: tutorialStepsById.collaboration.length },
])
