# 部署、配置、数据与安全

## 环境要求

- Python 3.14
- Node.js 22.13–24.x
- npm 11

前端和 Python 依赖均使用精确版本。

## 本地启动

前端：

```powershell
cd frontend
npm install
npm run dev
```

API：

```powershell
python backend/server.py
```

解析 Worker：

```powershell
python backend/worker.py
```

浏览器访问 `http://127.0.0.1:3004`。首次安装依赖后也可双击根目录的 `启动系统.bat`，同时启动前端、API 和 Worker。

## Docker 部署

```bash
cp .env.example .env
docker compose build drawing-mark-recognition
docker compose up -d --force-recreate
docker compose logs -f drawing-mark-recognition drawing-mark-recognition-worker
```

默认访问地址为 `http://127.0.0.1:8768`。检查状态：

```bash
docker compose ps
curl http://127.0.0.1:8768/api/health
```

健康接口中的 `version` 应为当前发布版本，`buildId` 应与 `.env` 一致。生产入口不缓存，带哈希静态资源使用长期缓存。

停止服务：

```powershell
docker compose down
```

该命令不会删除数据卷；`docker compose down -v` 会删除历史任务和审计数据，仅应在明确需要清空数据时执行。

修改端口：

```powershell
$env:DRAWING_MARK_RECOGNITION_PORT = '8877'
docker compose up -d
```

扩展 Worker：

```powershell
$env:DRAWING_MARK_RECOGNITION_MAX_CONCURRENT_ANALYSES = '2'
docker compose up -d --scale drawing-mark-recognition-worker=2
```

API 容器目前只能运行一个副本；Worker 可按负载扩展，并与 API 共用数据卷。

## 发布归档

仓库提供可重复执行的 PowerShell 打包脚本。普通发布默认不携带任何真实密钥：

```powershell
.\scripts\build-deployment-package.ps1
```

仅在明确需要把当前服务器配置交付给受信任部署人员时，才使用以下命令：

```powershell
.\scripts\build-deployment-package.ps1 -IncludeSecrets
```

`-IncludeSecrets` 会把项目根目录的 `.env` 原样写入 ZIP，并在打包前确认 `DEEPSEEK_API_KEY` 非空；脚本不会在终端输出密钥值。归档排除 Git 元数据、依赖目录、构建缓存、测试、运行数据、旧发布包和浏览器/服务器历史数据，并同时生成 `.sha256` 校验文件。

包含 `.env` 的 ZIP 等同于持有明文密钥：只应通过受控渠道传输，限制下载和读取权限，部署后删除多余副本，并在交付范围扩大或文件失控时立即轮换密钥。不要把该 ZIP 提交到 Git、公共网盘或普通群聊。解压后可直接按本页 Docker 部署步骤启动；上线前应核对 `.env` 中端口、版本、构建编号及生产安全参数。

## 运行参数

| 变量 | 默认值 | 作用 |
| --- | ---: | --- |
| `DRAWING_MARK_RECOGNITION_PORT` | `8768` | Docker 对外端口 |
| `DRAWING_MARK_RECOGNITION_MAX_BODY_MB` | `350` | 单个 JSON 请求体上限 |
| `DRAWING_MARK_RECOGNITION_MAX_UPLOAD_FILE_MB` | `350` | 单个原始上传文件上限 |
| `DRAWING_MARK_RECOGNITION_MAX_CONCURRENT_UPLOADS` | `2` | 同时接收的上传文件数 |
| `DRAWING_MARK_RECOGNITION_MIN_FREE_DISK_MB` | `512` | 上传后必须保留的最小磁盘空间 |
| `DRAWING_MARK_RECOGNITION_UPLOAD_RETENTION_HOURS` | `24` | 未关联临时上传保留时间 |
| `DRAWING_MARK_RECOGNITION_MAX_CONCURRENT_ANALYSES` | `1` | 前端显示的 Worker 容量 |
| `DRAWING_MARK_RECOGNITION_MAX_PENDING_ANALYSES` | `4` | 运行中及排队任务总上限 |
| `DRAWING_MARK_RECOGNITION_JOB_RETENTION_DAYS` | `90` | 终态任务保留天数 |
| `DRAWING_MARK_RECOGNITION_AUDIT_MAX_MB` | `10` | 单份审计日志轮转阈值 |
| `DRAWING_MARK_RECOGNITION_AUDIT_BACKUPS` | `5` | 审计历史文件数量 |
| `DRAWING_MARK_RECOGNITION_SESSION_SECURE` | `false` | HTTPS 部署时启用 Secure Cookie |
| `DRAWING_MARK_RECOGNITION_AI_PROVIDER` | 空 | AI 服务商；使用 DeepSeek 时设为 `deepseek` |
| `DEEPSEEK_API_KEY` | 空 | DeepSeek 密钥，仅由 API 服务读取 |
| `DEEPSEEK_API_URL` | `https://api.deepseek.com/chat/completions` | DeepSeek Chat Completions 完整地址 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | DeepSeek 模型名称 |
| `DRAWING_MARK_RECOGNITION_AI_API_URL` | 空 | 其他 OpenAI 兼容服务的完整地址，也可覆盖 DeepSeek 地址 |
| `DRAWING_MARK_RECOGNITION_AI_API_KEY` | 空 | 通用 AI 服务密钥，也可作为 DeepSeek 密钥使用 |
| `DRAWING_MARK_RECOGNITION_AI_MODEL` | 空 | 通用模型名称，也可覆盖 DeepSeek 模型 |

`/api/health` 的 `analysisWorkers` 报告存活 Worker 数。没有 Worker 时 API 仍会接收并持久化任务，任务保持等待直至 Worker 恢复。

## AI 助手配置

DeepSeek 使用与 OpenAI 兼容的 Chat Completions 协议。将 `.env.example` 复制为 `.env`，填写密钥后重启服务：

```text
DRAWING_MARK_RECOGNITION_AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=请填写实际密钥
DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
DEEPSEEK_MODEL=deepseek-v4-flash
```

`DEEPSEEK_API_URL` 和 `DEEPSEEK_MODEL` 可省略，此时使用上述默认值。现有 `DRAWING_MARK_RECOGNITION_AI_API_URL`、`DRAWING_MARK_RECOGNITION_AI_API_KEY` 和 `DRAWING_MARK_RECOGNITION_AI_MODEL` 仍可用于覆盖默认值或接入其他 OpenAI 兼容服务。项目根目录 `.env` 会被后端自动读取，Docker Compose 也会将这些变量传入 API 容器。

密钥只保存在服务端，不会发送到浏览器，也不应写入或提交 `.env.example`。未配置 AI 时对话面板显示红色错误提示，图纸识别和人工核对功能不受影响。

AI 回答使用 `/api/ai/chat/stream` 的 Server-Sent Events 流式传输。若在 API 前部署 Nginx 等反向代理，应对该路径关闭响应缓冲并适当延长读取超时；服务端同时发送 `X-Accel-Buffering: no`，避免回答被代理攒成整段后再返回。

AI 助手的本地知识清单位于 `docs/assistant-knowledge.json`。部署镜像必须同时包含 README、`docs/` 目录以及清单中登记的全部 Markdown；缺失或路径不合法时，文档工具不可用。索引位于 API 进程内存，文档大小或修改时间变化后自动重建，不需要独立数据库或向量服务。

后台管理的 AI 余额卡片使用同一份服务端密钥访问 DeepSeek 官方 `GET /user/balance`，只把币种、总余额、充值余额、赠金余额和可用状态返回管理员浏览器。自定义 OpenAI 兼容地址不会自动调用未知的账户接口。生产网络策略应同时允许 API 容器访问 DeepSeek 的对话和余额接口。

API 服务需要访问 `DEEPSEEK_API_URL` 所指向的外部 HTTPS 地址。检索发生在本地，但命中的系统文档片段会作为工具结果发送给 DeepSeek。当前代码明确排除任务 PDF、PCF、结果 JSON、SQLite 数据库、审计日志、发布归档和 `.env`。如果组织策略禁止系统文档离开内网，应改用组织批准的兼容模型地址或关闭 AI 配置。

建议在反向代理中对 `/api/ai/chat/stream` 设置不低于 90 秒的读取超时、关闭代理缓冲和压缩聚合，并保持 Cookie 转发。AI 路由在执行模型或文档工具前要求有效登录会话。

## PCF 解析器配置

系统自带 PCF 语义拓扑解析器。使用完整的 `idf-pipe-viewer` 解析器时可设置：

```powershell
$env:PCF_REFERENCE_PARSER = 'D:\tools\idf-pipe-viewer\scripts\parse_pcf_to_json.py'
$env:IDF_REFERENCE_PARSER = 'D:\tools\idf-pipe-viewer\scripts\parse_idf_to_json.py'
```

也可把解析器放入 `backend/vendor/idf-pipe-viewer/scripts`。解析器不可用时结果中的 `pcf.degraded` 为 `true`。Docker 使用外部解析器或 PCF 文件库时，应增加只读目录挂载。

## 数据、备份与安全

- 任务文件位于 `data/jobs/<任务编号>`。
- 队列、账号、会话、页锁和逐页识别/核对结果位于 `data/jobs/.queue/jobs.db`。
- 审计日志位于 `data/audit`。
- 浏览器草稿包含工程 PDF/PCF 副本，敏感文件应及时清理。
- 完成、失败或取消任务默认保留 90 天；审计日志默认达到 10 MB 后轮转并保留 5 份。
- 备份时应同时备份整个共享数据卷，尤其是 `jobs.db`；`result.json` 不再包含完成任务的页面正文。

系统允许开放注册，注册后的有效账号可以查看全部图纸任务，因此应部署在可信内网，或由反向代理增加组织级访问控制。公网部署必须使用 TLS、网络隔离并启用 `DRAWING_MARK_RECOGNITION_SESSION_SECURE=true`。密码使用独立随机盐的 scrypt 哈希，前端不会把用户名或密码写入 Web Storage。“记住密码”只控制 HttpOnly、SameSite=Lax 登录 Cookie 是否带 7 天 `Max-Age`；未勾选时使用无持久化期限的浏览器会话 Cookie，关闭浏览器后失效。注册后的自动登录同样使用会话 Cookie。

## 基础镜像拉取 403

若错误发生在 Dockerfile 的 `FROM` 阶段，先检查：

```bash
docker pull node:24-bookworm-slim
docker pull python:3.14-slim-bookworm
docker login
```

403 通常来自出口网络、代理、账号或组织策略；拉取配额超限通常返回 429。项目允许通过 `DRAWING_MARK_RECOGNITION_NODE_IMAGE` 和 `DRAWING_MARK_RECOGNITION_PYTHON_IMAGE` 替换基础镜像地址。完全无法访问 Docker Hub 时，可在联网机器 `docker save`，传输后用 `docker load` 导入。
