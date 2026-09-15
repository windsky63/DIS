# AI 助手与本地文档检索

## 功能边界

AI 助手用于说明图纸标识识别系统的真实功能、引导操作并协助排查问题。涉及系统功能、操作方法、技术机制、部署或故障排查时，助手会先搜索经过批准的本地系统文档，再结合当前界面上下文生成回答，并在回答下方列出实际使用的文档来源。

助手不会点击按钮、上传文件、修改标识、保存任务、释放页面锁、归档、删除或导出，也不会读取当前任务的 PDF、PCF、解析结果、账号数据、审计日志或环境变量。普通寒暄可以不检索文档。

## 组件与请求链路

一次流式对话经过以下真实组件：

```text
frontend/src/components/AssistantPanel.vue
  -> frontend/src/api.js: api.chatStream
  -> POST /api/ai/chat/stream
  -> backend/api/views.py: ApiHandler._stream_ai_chat
  -> backend/ai_assistant.py: stream_chat_completion
  -> backend/assistant_agent.py: stream_agent_events
  -> DeepSeek Chat Completions
            |
            +-> backend/assistant_tools.py
                  -> backend/document_knowledge.py
                        -> docs/assistant-knowledge.json
                        -> README.md 和 docs/*.md 中明确登记的文件
```

`AssistantPanel.vue` 管理会话选择、输入框、发送状态、Markdown 回答和参考资料展示。`api.js` 解析任意网络分片下的 SSE。`views.py` 负责登录校验和 HTTP/SSE 映射，不执行检索算法。`ai_assistant.py` 负责配置、输入限制、系统提示词和界面上下文。`assistant_agent.py` 负责 DeepSeek 流式响应、工具调用重组和轮次限制。

## DeepSeek 配置与优先级

后端启动后从进程环境变量和项目根目录 `.env` 读取配置，进程环境变量优先。设置 `DRAWING_MARK_RECOGNITION_AI_PROVIDER=deepseek`，或者在未设置 provider 时提供 `DEEPSEEK_API_KEY`，都会启用 DeepSeek 配置分支。

DeepSeek 默认值：

```text
DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
DEEPSEEK_MODEL=deepseek-v4-flash
```

通用变量 `DRAWING_MARK_RECOGNITION_AI_API_URL`、`DRAWING_MARK_RECOGNITION_AI_API_KEY` 和 `DRAWING_MARK_RECOGNITION_AI_MODEL` 可以覆盖地址、密钥和模型。密钥只用于服务端 `Authorization: Bearer` 请求头，不进入浏览器、系统提示词、工具结果或配置状态响应。

`GET /api/ai/status` 只返回是否配置以及模型名称。未配置时，AI 面板显示红色配置错误；识别、核对、保存和导出不受影响。

## 消息与界面上下文

后端只接受 `user` 和 `assistant` 两种历史消息角色，最后一条必须来自用户。单次模型请求最多采用最近 30 条消息，单条最多 4,000 个字符，总计最多 40,000 个字符。前端最多保留 20 个会话；服务端单个会话最多持久化 100 条消息。

前端只把以下白名单状态加入助手上下文：

- 是否已经加载任务；
- 当前页和分析总页数；
- 单文件或文件夹工程模式；
- 仅图元或 PDF 对照模式；
- 当前修改模式；
- 当前有效标识数量；
- 当前选中标识类型；
- 当前任务状态。

这些状态用于解释用户当前看到的界面，不构成任务文件读取权限。助手不得根据上下文编造候选对象、编号、锁持有人或识别结论。

## 文档知识清单

`docs/assistant-knowledge.json` 是唯一知识入口。每项包含稳定 `id`、项目相对 `path` 和显示 `title`。当前允许的文件固定为：

- `README.md`
- `docs/usage.md`
- `docs/architecture.md`
- `docs/development.md`
- `docs/deployment.md`
- `docs/ai-assistant.md`

路径必须与后端允许集合完全一致。后端将项目根目录和候选文件解析为真实路径，拒绝绝对路径、未登记路径、缺失文件、重复 ID、符号链接文件及越过项目根目录的路径。`data/`、`releases/`、`docs/superpowers/`、`.env` 和源代码均不能通过工具读取。

索引在第一次访问时构建于 API 进程内存。每次检索都会比较清单摘要以及文档真实路径、字节大小和纳秒修改时间；任一变化都会自动重建，无需重启服务。

## Markdown 分块与中文检索

解析器按 `#` 到 `######` 的 ATX 标题划分章节，并保留文档 ID、标题、相对路径、标题层级、章节 ID和起止行号。章节 ID由文档 ID、标题路径和起始行生成，不包含本机路径。

章节目标块长 1,800 字符，单块最多 2,200 字符，相邻长块最多保留 200 字符上下文。围栏代码块作为同一段处理，避免启动命令或接口示例被普通空行拆开。

查询先进行 Unicode NFKC 规范化和大小写折叠，再提取英文单词、数字、中文字符和中文二元片段。排序综合完整短语、章节标题、文档标题、正文命中和查询词覆盖率；覆盖率低于 40% 的片段不进入结果。一次搜索最多返回 6 个片段，工具 JSON 总字符数不超过 12,000。

## 工具调用协议

模型只能使用两个函数工具：

### search_system_documents

```json
{"query":"保存并关闭 页面锁","limit":5}
```

`query` 必须是 1 至 500 字符，`limit` 必须是 1 至 6 的整数。结果返回文档 ID、标题、相对路径、章节、章节 ID、行号和受限正文片段。

### read_document_section

```json
{"document_id":"usage-guide","section_id":"section-012345abcdef"}
```

文档 ID 和章节 ID 只能使用小写字母、数字和连字符，并且必须实际存在于当前索引。工具不接受 `path` 参数，模型无法通过构造 `../`、盘符或绝对路径读取文件。

## DeepSeek 工具循环

每轮请求都发送工具定义、`tool_choice: auto`、`temperature: 0.25` 和 `stream: true`。DeepSeek 可能把工具名称和 JSON 参数拆到多个 `delta.tool_calls` 片段中；代理按调用索引重组，并在服务端重新校验完整参数。

模型请求工具后，代理追加包含 `tool_calls` 的 assistant 消息以及具有相同 `tool_call_id` 的 tool 结果，再发起下一轮。思考模式返回的 `reasoning_content` 只为满足 DeepSeek 多轮协议回传给模型，不会写入 SSE、回答、来源或会话历史。

单次用户请求最多执行 3 轮工具调用。无效参数会作为结构化错误返回模型以允许修正；连续请求、未知工具或循环超过上限会终止本轮。模型不得在同一轮混合最终回答和工具调用。

## SSE 流式协议

`POST /api/ai/chat/stream` 在发送响应头前先取得第一个代理事件，因此输入错误、未配置和首次连接失败仍能返回正常 HTTP 错误状态。开始流式响应后使用以下事件：

```text
event: status
data: {"message":"正在分析问题"}

event: source
data: {"documentId":"usage-guide","title":"使用指南","path":"docs/usage.md","section":"使用指南 / 保存并关闭","startLine":30}

event: delta
data: {"content":"回答片段"}

event: done
data: {}
```

传输中发生错误时发送 `event: error` 和用户可理解的错误消息。前端要求收到 `done` 才认为正常完成；中断时保留已经收到的回答正文。状态事件只描述“正在分析、检索或读取”，不展示模型内部思维。

来源按文档 ID、章节和起始行去重。浏览器与 SQLite 会话历史只保存 `documentId`、`title`、`path`、`section` 和 `startLine`，最多 12 项；绝对路径、检索正文和其他字段会被丢弃。

## 安全与隐私

文档检索发生在本地后端，但命中的系统文档片段会作为工具结果发送给已配置的 DeepSeek 云端服务。当前知识清单只包含项目自带系统说明，不包含工程图纸或业务任务数据。AI 面板底部会持续说明这一边界。

检索文档被视为不可信证据。即使文档正文包含“忽略系统提示词”或要求访问其他文件，代理也只会执行固定工具，工具仍按清单和 JSON Schema 校验参数。回答来源只能来自工具本轮实际返回的数据。

未来若要检索工程 PDF、PCF 或解析结果，必须另行实现管理员策略、用户提示、任务归属校验、可发送字段清单和脱敏规则；不能仅把任务目录加入当前清单。

## 故障与排查

### 面板提示 AI 未配置

检查 `.env` 是否位于项目根目录、密钥变量是否为空，并在修改后重启 API。不要把真实密钥写入 `.env.example` 或日志。

### 一直显示正在分析

检查 API 到 DeepSeek 的外网连接和反向代理读取超时。Nginx 等代理必须关闭 `/api/ai/chat/stream` 的响应缓冲；服务端已经发送 `X-Accel-Buffering: no`。

### 找不到系统文档依据

确认文件已登记在 `docs/assistant-knowledge.json`，使用 UTF-8 编码并包含 Markdown 标题。运行文档知识库测试检查清单、路径和关键查询。修改文件后索引会依据文件签名自动刷新。

### 工具调用达到次数上限

这通常表示模型反复生成无效参数或文档不足。检查工具错误、搜索词和知识文档是否清楚覆盖问题，不要通过提高无限轮数规避。

### 回答传输中断

前端会保留已收到的内容并显示错误原因。检查 DeepSeek 响应、API 日志、代理缓冲和浏览器网络面板；客户端主动关闭面板造成的断连不会作为服务器内部错误再次写响应。

## 扩充知识文档

优先在主题对应的现有文档中增加真实内容。确需新增知识文档时：

1. 在 `docs/` 根层创建 UTF-8 Markdown，并使用清晰、稳定的标题。
2. 在 `APPROVED_DOCUMENT_PATHS` 增加精确相对路径。
3. 在 `docs/assistant-knowledge.json` 增加唯一稳定 ID、路径和标题。
4. 为代表性中文及技术查询增加检索质量测试。
5. 运行 `python -m unittest backend.tests.test_document_knowledge backend.tests.test_assistant_documentation -v`。

不要登记临时设计稿、实施计划、源代码、环境配置或运行数据。文档中的组件、路由、限制和默认值变化时，应在同一次代码变更中同步更新对应知识文档。
