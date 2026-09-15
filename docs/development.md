# 开发与测试

## 项目目录

```text
backend/                   Python API、识别引擎、队列和测试
frontend/                  Vue 3 前端及测试
backend/tutorial/          内置教程文件和预解析结果
docs/                      使用、架构、部署和开发文档
data/jobs/                 本地任务数据，默认不纳入版本控制
data/audit/                审计日志，默认不纳入版本控制
releases/                  发布归档
```

## 后端模块边界

```text
backend/
├─ server.py                 # 兼容启动入口
├─ api/
│  ├─ views.py               # HTTP 请求、响应与路由
│  ├─ exceptions.py          # API 异常
│  └─ services/
│     ├─ uploads.py          # 上传暂存、校验和消费
│     └─ queue.py            # 队列查询与排序命令
├─ auth_store.py             # 账号与会话
├─ job_store.py              # SQLite 队列持久化
├─ page_lock_store.py        # 页面独占锁
├─ page_reviews.py           # 页面 revision 合并
├─ job_runner.py             # 单任务执行服务
├─ worker.py                 # Worker 入口
└─ engine.py                 # 识别流程编排
```

## 前端模块边界

前端遵循“页面负责组合、组件负责展示、composable 负责状态、service 负责 I/O、纯模块负责算法”的边界。

- `components/AppHeader.vue`：服务状态、快捷键和系统入口。
- `components/AnalysisProgressPanel.vue`、`AnalysisQueueDialog.vue`：解析进度与任务队列。
- `components/AssistantPanel.vue`：AI 对话、会话历史和配置状态。
- `components/DraftManagerDialogs.vue`：草稿恢复与管理。
- `components/SystemSettingsDialog.vue`、`SymbolConfigDialog.vue`：界面及识别配置。
- `components/AnalysisConfirmationDialogs.vue`：选中对象重新编号和重复任务确认。
- `composables/useAnalysisWorkflow.js`：上传、排队、轮询、取消和结果发布。
- `composables/usePageCollaboration.js`：页面锁、心跳、保存和切页。
- `composables/useProjectWorkspace.js`：主图、工程切换、教程和恢复。
- `composables/useReferenceWorkspace.js`：对照 PDF/PCF、页面匹配和懒加载。
- `composables/useMarkerEditor.js`：人工标识、复制粘贴、拖动、排除、删除和行内编辑。
- `composables/useWorkspaceHistory.js`：撤销、重做和快照恢复。
- `composables/useWorkspacePersistence.js`：浏览器草稿和后端保存状态。
- `composables/useCanvasRenderer.js`、`useCanvasViewport.js`：PDF 渲染、缓存、缩放和平移。
- `numbering.js`、`markerLayout.js`、`markerClipboard.js`：编号、布局和复制转换算法。

## AI 助手开发边界

- `backend/ai_assistant.py`：读取 DeepSeek 配置、验证对话、生成真实系统提示词和白名单界面上下文。
- `backend/assistant_agent.py`：重组流式 `tool_calls`、回传思考模式协议字段、限制工具轮次并产生类型化事件。
- `backend/assistant_tools.py`：定义两个只读文档工具，校验 JSON 参数并裁剪工具结果。
- `backend/document_knowledge.py`：校验清单和路径、解析 Markdown、维护缓存并执行中文词法检索。
- `frontend/src/components/AssistantRetrievalDetails.vue`：显示检索状态和安全来源字段。

代理向 API 层产生 `status`、`source` 和 `delta` 三类事件；API 正常结束后追加 `done`，流内异常转换为 `error`。新增事件必须同时更新后端路由测试、`frontend/src/api.js` 的分片解析测试和 AI 面板测试。

扩充知识库时，不要自动扫描目录。先更新真实技术文档，再同时修改 `docs/assistant-knowledge.json` 与 `APPROVED_DOCUMENT_PATHS`，并为用户可能采用的中文问题增加排序测试。知识清单中的相对路径、显示标题和文档 ID 应保持稳定，以免历史回答来源失效。

针对性测试：

```powershell
python -m unittest backend.tests.test_document_knowledge backend.tests.test_assistant_tools backend.tests.test_assistant_agent backend.tests.test_assistant_documentation -v
cd frontend
node --test tests/api.test.js tests/assistantConversations.test.js tests/assistantPanel.test.js
```

完整技术说明和 SSE 示例见 [AI 助手与本地文档检索](ai-assistant.md)。

新增功能应进入对应模块，避免继续扩大 `App.vue`。

## 运行测试

后端：

```powershell
python -m unittest discover -s backend/tests -v
```

前端：

```powershell
cd frontend
npm test
```

前端测试使用 Node.js 内置测试运行器，包含纯模块测试、composable 测试和 Vue SSR 组件测试。

## 生产构建

```powershell
cd frontend
npm run build
```

构建输出位于 `frontend/dist`。Python API 在生产部署中直接托管该目录，Dockerfile 使用 Node 构建阶段生成资源，再复制到 Python 运行镜像。

## 发布前检查

1. 运行前后端完整测试。
2. 执行前端生产构建。
3. 检查 `/api/health` 的版本、构建编号和 Worker 状态。
4. 使用代表性矢量 PDF 验证任务提交、恢复、页面保存和导出。
5. 确认发布归档不包含 `data/jobs`、`data/audit`、浏览器草稿或本地备份。
