# AI Local Document Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the DeepSeek-backed AI assistant a bounded tool loop that searches approved local system documentation, streams progress and answers, and cites the exact documents used.

**Architecture:** A manifest-backed `DocumentKnowledge` service parses approved Markdown into searchable sections, while a narrow `AssistantToolExecutor` exposes only document IDs and validated queries. A streaming assistant agent handles DeepSeek tool-call deltas and emits typed events that the existing HTTP SSE endpoint and Vue panel display.

**Tech Stack:** Python 3.14 standard library, DeepSeek OpenAI-compatible Chat Completions API, `unittest`, Vue 3.5, Vuetify 4, browser `fetch`/SSE parsing, Node.js built-in test runner.

**Spec:** `docs/superpowers/specs/2026-09-15-ai-local-document-retrieval-design.md`

## Global Constraints

- The first release indexes only `README.md`, `docs/usage.md`, `docs/architecture.md`, `docs/development.md`, `docs/deployment.md`, and `docs/ai-assistant.md`.
- Never index `.env`, source files, databases, audit logs, `data/`, `releases/`, or `docs/superpowers/`.
- Tool inputs contain a query or stable document/section IDs, never a filesystem path.
- Resolve and verify every manifest path beneath the project root; reject absolute paths, traversal, hidden files, disallowed directories, and symlink escapes.
- Markdown chunks target 1,800 characters with at most 200 characters of overlap; one returned chunk is capped at 2,200 characters.
- Search returns at most 6 chunks and at most 12,000 characters per tool result.
- Permit at most 3 tool-call rounds per assistant request.
- Stream only short operational status, sources, and final answer content; never expose model reasoning content.
- Retrieved documentation is untrusted evidence and cannot override system instructions.
- Do not send task PDF, PCF, result data, account data, audit data, or environment configuration to DeepSeek.
- Keep README focused on user-visible product functionality; put implementation details under `docs/`.

---

### Task 1: Manifest-backed Markdown knowledge index

**Files:**
- Create: `docs/assistant-knowledge.json`
- Create: `backend/document_knowledge.py`
- Create: `backend/tests/test_document_knowledge.py`

**Interfaces:**
- Produces: `KnowledgeError`, `KnowledgeSource`, `KnowledgeChunk`, and `DocumentKnowledge(project_root: Path, manifest_path: Path | None = None)`.
- Produces: `DocumentKnowledge.status() -> dict[str, object]`, `search(query: str, limit: int = 6) -> list[KnowledgeChunk]`, and `read_section(document_id: str, section_id: str) -> KnowledgeChunk`.
- `KnowledgeChunk.as_tool_payload()` returns only `documentId`, `title`, `path`, `section`, `sectionId`, `startLine`, `endLine`, and `content`.

- [ ] **Step 1: Write manifest validation and retrieval tests**

Create table-driven tests that build a temporary project and assert valid Markdown can be found, while traversal and disallowed paths fail:

```python
def test_manifest_rejects_paths_outside_the_approved_document_set(self):
    root = self.make_project({".env": "SECRET=value"})
    self.write_manifest(root, [{"id": "secret", "path": ".env", "title": "Secret"}])
    with self.assertRaisesRegex(document_knowledge.KnowledgeError, "不允许"):
        document_knowledge.DocumentKnowledge(root).status()

def test_chinese_query_prefers_matching_heading_and_preserves_source(self):
    root = self.make_project({
        "docs/usage.md": "# 使用指南\n\n## 保存并关闭\n\n先保存页面，再释放页面锁。",
    })
    self.write_manifest(root, [{"id": "usage", "path": "docs/usage.md", "title": "使用指南"}])
    result = document_knowledge.DocumentKnowledge(root).search("保存并关闭 页面锁")
    self.assertEqual(result[0].source.document_id, "usage")
    self.assertEqual(result[0].section, "使用指南 / 保存并关闭")
    self.assertIn("释放页面锁", result[0].content)
```

Also cover duplicate IDs, missing files, absolute paths, `../`, `data/`, `docs/superpowers/`, symlink escape where supported, empty query, limit clamping, long-section splitting, code-fence preservation, overlap de-duplication, cache reuse, and file modification invalidation.

- [ ] **Step 2: Run the new test module and confirm the red state**

Run: `python -m unittest backend.tests.test_document_knowledge -v`

Expected: import failure because `backend/document_knowledge.py` does not exist.

- [ ] **Step 3: Add the approved document manifest**

Create `docs/assistant-knowledge.json` with version `1` and stable IDs:

```json
{
  "version": 1,
  "documents": [
    {"id": "product-overview", "path": "README.md", "title": "图纸标识识别系统"},
    {"id": "usage-guide", "path": "docs/usage.md", "title": "使用指南"},
    {"id": "system-architecture", "path": "docs/architecture.md", "title": "系统架构与工作机制"},
    {"id": "development-guide", "path": "docs/development.md", "title": "开发与测试"},
    {"id": "deployment-guide", "path": "docs/deployment.md", "title": "部署、配置、数据与安全"}
  ]
}
```

- [ ] **Step 4: Implement path validation, Markdown sectioning, caching, and lexical ranking**

Use frozen dataclasses and keep filesystem access inside `DocumentKnowledge`. Start path validation with an exact final-release allowlist; the AI guide is added to the manifest only when Task 7 creates it:

```python
@dataclass(frozen=True)
class KnowledgeSource:
    document_id: str
    title: str
    relative_path: str

@dataclass(frozen=True)
class KnowledgeChunk:
    source: KnowledgeSource
    section: str
    section_id: str
    start_line: int
    end_line: int
    content: str
    score: float = 0.0

APPROVED_DOCUMENT_PATHS = frozenset({
    "README.md",
    "docs/usage.md",
    "docs/architecture.md",
    "docs/development.md",
    "docs/deployment.md",
    "docs/ai-assistant.md",
})

def _resolve_approved_path(project_root: Path, relative_path: str) -> Path:
    if relative_path not in APPROVED_DOCUMENT_PATHS:
        raise KnowledgeError("知识文档路径不允许")
    root = project_root.resolve(strict=True)
    candidate = root / relative_path
    resolved = candidate.resolve(strict=True)
    if candidate.is_symlink() or not resolved.is_relative_to(root):
        raise KnowledgeError("知识文档路径越过项目边界")
    return resolved
```

Permit exactly `README.md` and direct Markdown children of `docs/`, then additionally reject `docs/superpowers/`. Normalize search text with `unicodedata.normalize("NFKC", value).casefold()`, tokenize ASCII words/numbers plus Chinese characters and adjacent Chinese bigrams, and weight exact phrase, document title, heading,正文, and query-term coverage in that order. Cache the parsed index against a signature of manifest contents plus every file's resolved path, byte size, and `st_mtime_ns`.

- [ ] **Step 5: Run knowledge tests to green**

Run: `python -m unittest backend.tests.test_document_knowledge -v`

Expected: all tests pass, including the secret-file rejection and Chinese ranking cases.

- [ ] **Step 6: Commit the knowledge index**

```powershell
git add docs/assistant-knowledge.json backend/document_knowledge.py backend/tests/test_document_knowledge.py
git commit -m "feat: add manifest-backed assistant knowledge index"
```

---

### Task 2: Validated assistant document tools

**Files:**
- Create: `backend/assistant_tools.py`
- Create: `backend/tests/test_assistant_tools.py`

**Interfaces:**
- Consumes: `DocumentKnowledge.search()` and `DocumentKnowledge.read_section()` from Task 1.
- Produces: `ASSISTANT_TOOL_DEFINITIONS: tuple[dict[str, object], ...]`.
- Produces: `ToolExecution(content: str, sources: tuple[dict[str, object], ...])`.
- Produces: `AssistantToolExecutor(knowledge: DocumentKnowledge).execute(name: str, arguments_json: str) -> ToolExecution`.

- [ ] **Step 1: Write failing tool-schema and execution tests**

```python
def test_search_tool_returns_json_content_and_only_real_sources(self):
    knowledge = StubKnowledge(search_results=[self.chunk])
    execution = assistant_tools.AssistantToolExecutor(knowledge).execute(
        "search_system_documents", '{"query":"保存并关闭","limit":5}',
    )
    payload = json.loads(execution.content)
    self.assertEqual(payload["results"][0]["documentId"], "usage-guide")
    self.assertEqual(execution.sources[0]["path"], "docs/usage.md")

def test_tool_rejects_unknown_names_and_path_like_arguments(self):
    executor = assistant_tools.AssistantToolExecutor(StubKnowledge())
    with self.assertRaisesRegex(assistant_tools.AssistantToolError, "未知工具"):
        executor.execute("read_file", '{"path":".env"}')
```

Cover malformed JSON, unknown properties, query length `1..500`, limit `1..6`, document/section ID patterns, missing sections, and total output truncation at 12,000 characters.

- [ ] **Step 2: Run tests and verify they fail before implementation**

Run: `python -m unittest backend.tests.test_assistant_tools -v`

Expected: import failure for `assistant_tools`.

- [ ] **Step 3: Implement strict function definitions and executor dispatch**

Define only these functions:

```python
ASSISTANT_TOOL_DEFINITIONS = ({
    "type": "function",
    "function": {
        "name": "search_system_documents",
        "description": "搜索图纸标识识别系统的已批准本地文档。涉及系统功能、操作、机制、部署或排查时先调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 6},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "read_document_section",
        "description": "读取搜索结果中某个已批准文档章节的完整内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "pattern": "^[a-z0-9-]{1,64}$"},
                "section_id": {"type": "string", "pattern": "^[a-z0-9-]{1,128}$"},
            },
            "required": ["document_id", "section_id"],
            "additionalProperties": False,
        },
    },
})
```

Parse JSON into a dictionary, compare its exact key set with the selected schema, validate every value again in Python, dispatch through a fixed name-to-method mapping, and serialize tool content with `ensure_ascii=False`. Never call `Path` from tool arguments.

- [ ] **Step 4: Run tool tests to green**

Run: `python -m unittest backend.tests.test_assistant_tools -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the tool boundary**

```powershell
git add backend/assistant_tools.py backend/tests/test_assistant_tools.py
git commit -m "feat: expose bounded assistant document tools"
```

---

### Task 3: Streaming DeepSeek tool-call agent

**Files:**
- Create: `backend/assistant_agent.py`
- Create: `backend/tests/test_assistant_agent.py`
- Modify: `backend/ai_assistant.py:13-264`
- Modify: `backend/tests/test_ai_assistant.py`

**Interfaces:**
- Consumes: `ASSISTANT_TOOL_DEFINITIONS` and `AssistantToolExecutor.execute()` from Task 2.
- Produces: `AssistantAgentEvent(type: Literal["status", "source", "delta"], payload: dict[str, object])`.
- Produces: `stream_agent_events(messages: list[dict[str, object]], *, url: str, key: str, model: str, tools: AssistantToolExecutor, opener=urlopen, max_tool_rounds: int = 3) -> Iterator[AssistantAgentEvent]`.
- Updates: `ai_assistant.stream_chat_completion(...)` yields typed event dictionaries; `chat_completion(...)` consumes those events and returns the concatenated final text.

- [ ] **Step 1: Add failing tests for split tool calls and final answer streaming**

Use sequential fake upstream responses. The first response splits a tool call's name and JSON arguments across SSE deltas; the second streams the answer:

```python
first = [
    sse({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"search_system_","arguments":"{\\\"query\\\":\\\"保存"}}]}}]}),
    sse({"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"documents","arguments":"并关闭\\\"}"}}]},"finish_reason":"tool_calls"}]}),
    b"data: [DONE]\n",
]
second = [
    sse({"choices":[{"delta":{"reasoning_content":"private","content":"依据文档，"}}]}),
    sse({"choices":[{"delta":{"content":"系统会先保存。"},"finish_reason":"stop"}]}),
    b"data: [DONE]\n",
]
self.assertEqual([event.type for event in events], ["status", "status", "source", "delta", "delta"])
self.assertNotIn("private", repr(events))
```

Assert the second upstream payload contains the original assistant `tool_calls` message and matching `role: tool`/`tool_call_id`. Also test a no-tool greeting, malformed SSE, upstream error objects, invalid arguments with one correction round, unknown tools, mixed text/tool output rejection, and the 3-round ceiling.

- [ ] **Step 2: Run agent and existing assistant tests to verify the red state**

Run: `python -m unittest backend.tests.test_assistant_agent backend.tests.test_ai_assistant -v`

Expected: import failure for `assistant_agent` and old string-only stream assertions requiring updates.

- [ ] **Step 3: Implement the streaming event and tool-call accumulator**

Represent fragmented calls by their upstream index:

```python
@dataclass(frozen=True)
class AssistantAgentEvent:
    type: Literal["status", "source", "delta"]
    payload: dict[str, object]

@dataclass
class PendingToolCall:
    call_id: str = ""
    name: str = ""
    arguments: str = ""
```

For every DeepSeek round, post `messages`, `tools`, `tool_choice: "auto"`, `temperature: 0.25`, and `stream: true`. Ignore `reasoning_content`. Concatenate `delta.tool_calls[*].id`, function name, and arguments by `index`. If the round ends with tool calls, execute each call, emit a search/read status, emit only sources returned by the executor, append the canonical assistant/tool messages, and continue. If content begins, emit `delta` immediately and reject a later tool call in that same round.

- [ ] **Step 4: Integrate the agent without exposing configuration or document contents incorrectly**

In `ai_assistant.py`, create a project-scoped knowledge service lazily and expand the system prompt with the retrieval rules from the spec. Preserve `_settings()`, `configuration_status()`, and `validate_messages()` behavior. Keep the public entry point compatible with injected test dependencies:

```python
def stream_chat_completion(messages, *, context=None, opener=urlopen, tool_executor=None):
    normalized = validate_messages(messages)
    url, key, model = _settings()
    executor = tool_executor or _default_tool_executor()
    yield from stream_agent_events(
        [{"role": "system", "content": _system_message(context)}, *normalized],
        url=url, key=key, model=model, tools=executor, opener=opener,
    )
```

`configuration_status()` may add `knowledgeConfigured`, `knowledgeDocuments`, and `knowledgeError`, but must never include the API key, absolute paths, or document content.

- [ ] **Step 5: Run agent and assistant tests to green**

Run: `python -m unittest backend.tests.test_assistant_agent backend.tests.test_ai_assistant -v`

Expected: all tests pass; assertions confirm reasoning is absent and only real tool sources are emitted.

- [ ] **Step 6: Commit the agent loop**

```powershell
git add backend/assistant_agent.py backend/ai_assistant.py backend/tests/test_assistant_agent.py backend/tests/test_ai_assistant.py
git commit -m "feat: add DeepSeek document tool loop"
```

---

### Task 4: Map typed assistant events to the protected SSE API

**Files:**
- Modify: `backend/api/views.py:793-822,1215-1240`
- Modify: `backend/tests/test_server.py:83-134`

**Interfaces:**
- Consumes: typed events yielded by `ai_assistant.stream_chat_completion()`.
- Produces: SSE `status`, `source`, `delta`, `done`, and `error` events on `POST /api/ai/chat/stream`.
- Preserves: authentication middleware and JSON error behavior before SSE headers are sent.

- [ ] **Step 1: Replace the string-only server test with typed-event coverage**

```python
events = iter([
    {"type": "status", "payload": {"message": "正在检索系统文档"}},
    {"type": "source", "payload": {"documentId": "usage-guide", "title": "使用指南", "path": "docs/usage.md", "section": "保存并关闭", "startLine": 30}},
    {"type": "delta", "payload": {"content": "第一段回答"}},
])
with patch.object(server.ai_assistant, "stream_chat_completion", return_value=events):
    handler.do_POST()
self.assertIn("event: status", body)
self.assertIn("event: source", body)
self.assertIn("event: delta", body)
self.assertTrue(body.endswith("event: done\ndata: {}\n\n"))
```

Add a test proving unauthenticated access is rejected before document or DeepSeek work begins, and a test proving an agent exception after headers becomes an SSE `error` event.

- [ ] **Step 2: Run the focused API tests and verify failure**

Run: `python -m unittest backend.tests.test_server.ServerTests.test_ai_chat_stream_forwards_typed_agent_events -v`

Expected: failure because `_stream_ai_chat` still treats every yielded item as a text delta.

- [ ] **Step 3: Implement a whitelist-based event bridge**

Read the first event before sending HTTP 200 so configuration/input failures still use normal JSON status codes. After headers, accept only `status`, `source`, and `delta`; send their payload through `_sse_event`. Reject unknown event types as `AssistantRequestError`. Always send `done` only after the iterator ends normally.

```python
for event in chain([first_event], events):
    event_type = event.get("type")
    if event_type not in {"status", "source", "delta"}:
        raise ai_assistant.AssistantRequestError("AI 服务产生了未知事件")
    self._sse_event(event_type, event.get("payload") or {})
```

- [ ] **Step 4: Run all server API tests**

Run: `python -m unittest backend.tests.test_server -v`

Expected: all tests pass.

- [ ] **Step 5: Commit the SSE API mapping**

```powershell
git add backend/api/views.py backend/tests/test_server.py
git commit -m "feat: stream assistant retrieval status and sources"
```

---

### Task 5: Parse and persist assistant source events in the frontend

**Files:**
- Modify: `frontend/src/api.js:28-91`
- Modify: `frontend/tests/api.test.js`
- Modify: `frontend/src/assistantConversations.js`
- Modify: `frontend/tests/assistantConversations.test.js`

**Interfaces:**
- Consumes: SSE payloads from Task 4.
- Produces: `api.chatStream(messages, context, onDelta, { signal, onStatus, onSource })` returning `{ message: { role, content, sources } }`.
- Persists: safe source fields `documentId`, `title`, `path`, `section`, and integer `startLine`; drops all other fields.

- [ ] **Step 1: Extend the split-SSE test with status and duplicate sources**

```javascript
const statuses = []
const sources = []
const result = await api.chatStream(messages, context, delta => deltas.push(delta), {
  onStatus: value => statuses.push(value),
  onSource: value => sources.push(value),
})
assert.deepEqual(statuses, ['正在分析问题', '正在检索系统文档'])
assert.equal(sources.length, 1)
assert.deepEqual(result.message.sources, sources)
```

Include an `event: source` split across byte chunks twice and assert it is de-duplicated by `documentId + section + startLine`. Assert an invalid source payload is ignored and `done` still remains mandatory.

- [ ] **Step 2: Add a conversation sanitizer test for safe source persistence**

```javascript
messages: [{
  role: 'assistant', content: '回答',
  sources: [{ documentId: 'usage-guide', title: '使用指南', path: 'docs/usage.md', section: '保存', startLine: 31, absolutePath: 'C:/secret' }],
}]
```

After saving and loading, assert `absolutePath` is absent and the five allowed fields remain.

- [ ] **Step 3: Run focused frontend tests and verify failure**

Run: `node --test tests/api.test.js tests/assistantConversations.test.js`

Expected: status/source callbacks are not invoked and persisted messages lose sources.

- [ ] **Step 4: Implement event parsing, source normalization, and persistence**

Keep the existing line-ending and partial-buffer handling. Add branches to `consumeEvent`:

```javascript
if (eventName === 'status' && typeof payload.message === 'string') options.onStatus?.(payload.message)
if (eventName === 'source') {
  const source = normalizeAssistantSource(payload)
  const key = `${source.documentId}\0${source.section}\0${source.startLine}`
  if (!sourceKeys.has(key)) { sourceKeys.add(key); sources.push(source); options.onSource?.(source) }
}
```

Update `sanitizeMessage` to retain `sources` only for assistant messages and cap them at 12 entries. Never persist absolute paths or raw retrieved text.

- [ ] **Step 5: Run focused frontend tests to green**

Run: `node --test tests/api.test.js tests/assistantConversations.test.js`

Expected: all tests pass.

- [ ] **Step 6: Commit the frontend protocol state**

```powershell
git add frontend/src/api.js frontend/tests/api.test.js frontend/src/assistantConversations.js frontend/tests/assistantConversations.test.js
git commit -m "feat: retain assistant retrieval sources"
```

---

### Task 6: Show retrieval progress and cited documents in the AI panel

**Files:**
- Modify: `frontend/src/components/AssistantPanel.vue:1-230`
- Modify: `frontend/src/style.css`
- Modify: `frontend/tests/assistantPanel.test.js`

**Interfaces:**
- Consumes: `onStatus`, `onSource`, and returned message sources from Task 5.
- Produces: an `aria-live` retrieval status during sending and a semantic source list beneath assistant answers.

- [ ] **Step 1: Add a failing SSR rendering test for sources and retrieval state**

Extract a small renderable source list if needed, or seed panel state through an exported pure normalizer. Assert rendered output includes `参考资料`, `使用指南`, `保存并关闭`, and `docs/usage.md`, and does not render `C:\` or a clickable `file://` URL.

Also assert the panel template contains a polite live status element and the disclaimer states that approved system-document excerpts are sent to the configured AI service when retrieval is used.

- [ ] **Step 2: Run the panel test and verify failure**

Run: `node --test tests/assistantPanel.test.js`

Expected: source and retrieval-status affordances are absent.

- [ ] **Step 3: Wire streaming status and sources into each assistant message**

Initialize the pending response as:

```javascript
const streamedMessage = { role: 'assistant', content: '', sources: [] }
const retrievalStatus = ref('')
const response = await api.chatStream(requestMessages, props.context, onDelta, {
  onStatus: status => { retrievalStatus.value = status },
  onSource: source => { streamedMessage.sources.push(source) },
})
streamedMessage.sources = response.message.sources
```

Clear status in `finally`. Render status only while `sending`; render a `<details>` block for non-empty assistant sources. Display title, section, relative path, and optional line number as text. Do not create filesystem links.

- [ ] **Step 4: Add compact styles consistent with the existing assistant panel**

Use existing panel colors and typography. Keep the source list visually secondary, wrap long section/path text, and ensure the `<summary>` has a keyboard-visible focus state. Do not add a configuration chip to the header.

- [ ] **Step 5: Run panel, composer, Markdown, and conversation tests**

Run: `node --test tests/assistantPanel.test.js tests/assistantComposer.test.js tests/assistantMarkdown.test.js tests/assistantConversations.test.js`

Expected: all tests pass.

- [ ] **Step 6: Commit the assistant presentation**

```powershell
git add frontend/src/components/AssistantPanel.vue frontend/src/style.css frontend/tests/assistantPanel.test.js
git commit -m "feat: display assistant retrieval progress and sources"
```

---

### Task 7: Complete and index the technical documentation

**Files:**
- Create: `docs/ai-assistant.md`
- Create: `backend/tests/test_assistant_documentation.py`
- Modify: `docs/assistant-knowledge.json`
- Modify: `README.md`
- Modify: `docs/usage.md`
- Modify: `docs/architecture.md`
- Modify: `docs/development.md`
- Modify: `docs/deployment.md`

**Interfaces:**
- Consumes: the manifest and `DocumentKnowledge` from Task 1.
- Produces: a user-facing overview plus searchable technical facts with stable Markdown headings.

- [ ] **Step 1: Write failing documentation consistency and retrieval-quality tests**

```python
def test_every_manifest_document_exists_has_a_heading_and_is_searchable(self):
    knowledge = document_knowledge.DocumentKnowledge(PROJECT_ROOT)
    status = knowledge.status()
    self.assertTrue(status["configured"], status.get("error"))
    self.assertEqual(status["documentCount"], 6)

def test_key_questions_retrieve_the_expected_technical_document(self):
    cases = {
        "页面锁 revision 保存冲突": "system-architecture",
        "DeepSeek tool_calls SSE": "ai-assistant-guide",
        "Docker Worker 环境变量": "deployment-guide",
        "如何复制粘贴标识": "usage-guide",
    }
    for query, expected_id in cases.items():
        self.assertEqual(knowledge.search(query)[0].source.document_id, expected_id)
```

Add an assertion that README has no headings named `技术架构`, `接口实现`, or `数据表结构`, while it contains the user-visible AI document-answering feature and links to `docs/ai-assistant.md`.

- [ ] **Step 2: Run documentation tests and confirm they fail**

Run: `python -m unittest backend.tests.test_assistant_documentation -v`

Expected: failure because `docs/ai-assistant.md` and the new technical sections do not exist.

- [ ] **Step 3: Write the dedicated AI assistant technical guide**

Create `docs/ai-assistant.md` with concrete sections and current names:

- `组件与请求链路`: `AssistantPanel.vue` → `api.chatStream` → `/api/ai/chat/stream` → `ai_assistant.py` → `assistant_agent.py` → DeepSeek.
- `配置与模型`: provider precedence, `.env` loading, API URL/model defaults, and server-only key handling.
- `系统提示词与界面上下文`: accepted roles, 30-message/4,000-character/40,000-total limits, allowed context fields, and no action execution.
- `文档知识库`: manifest schema, allowed files, cache invalidation, Markdown sectioning, Chinese lexical ranking, limits, and source metadata.
- `工具调用循环`: tool schemas, fragmented SSE assembly, maximum 3 rounds, tool/result message pairing, and final streaming.
- `SSE 协议`: exact `status`, `source`, `delta`, `done`, and `error` payload examples.
- `安全与隐私`: cloud transmission boundary, path controls, prompt injection, source validation, excluded data, and future task-document prerequisites.
- `故障与排查`: configuration failure, invalid manifest, no match, malformed tool call, proxy buffering, timeout, and client disconnect.
- `扩充知识文档`: edit a listed document or add a stable manifest entry, then run the documentation tests.

Add the completed guide to `docs/assistant-knowledge.json` in the same step:

```json
{"id": "ai-assistant-guide", "path": "docs/ai-assistant.md", "title": "AI 助手与本地文档检索"}
```

- [ ] **Step 4: Expand existing documents with verified system details**

Update `docs/architecture.md` with the AI retrieval data flow and module boundaries. Update `docs/development.md` with tool event contracts, knowledge maintenance, focused tests, and full verification commands. Update `docs/deployment.md` with knowledge manifest startup checks, outbound DeepSeek traffic, proxy SSE configuration, and the rule that only approved system-document excerpts leave the server. Update `docs/usage.md` with retrieval statuses, source interpretation, no-result behavior, and the cloud-processing notice.

In README, add one feature bullet stating that the assistant can consult approved system documents and show sources, plus a `docs/ai-assistant.md` link under detailed documents. Do not add implementation prose to README.

- [ ] **Step 5: Run documentation and retrieval-quality tests to green**

Run: `python -m unittest backend.tests.test_assistant_documentation backend.tests.test_document_knowledge -v`

Expected: all manifest, content, and key-query assertions pass.

- [ ] **Step 6: Commit the completed knowledge content**

```powershell
git add README.md docs/assistant-knowledge.json docs/usage.md docs/architecture.md docs/development.md docs/deployment.md docs/ai-assistant.md backend/tests/test_assistant_documentation.py
git commit -m "docs: complete assistant knowledge base"
```

---

### Task 8: Full regression and production verification

**Files:**
- Modify only files required by failures caused by Tasks 1–7; do not repair unrelated pre-existing failures.

**Interfaces:**
- Verifies all backend, frontend, build, formatting, knowledge-safety, and user-visible acceptance criteria.

- [ ] **Step 1: Run the complete backend suite**

Run: `python -m unittest discover -s backend/tests -v`

Expected: all tests pass; environment-dependent skips remain explicitly reported as skips.

- [ ] **Step 2: Run the complete frontend suite**

Run from `frontend/`: `npm.cmd test`

Expected: all tests pass with zero failures.

- [ ] **Step 3: Produce the frontend build**

Run from `frontend/`: `npm.cmd run build`

Expected: Vite exits `0`; the existing chunk-size warning may remain, but there are no compile errors.

- [ ] **Step 4: Check whitespace and scope**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors. Review status against the pre-existing dirty worktree and ensure only the planned files were added to this feature's commits.

- [ ] **Step 5: Perform a controlled DeepSeek smoke test when outbound access is available**

Start the existing service with the configured `.env`, authenticate in the application, and ask: `保存并关闭失败后页面锁会怎样处理？`

Expected UI sequence: `正在分析问题` → document retrieval status → streamed Markdown answer → a source entry pointing to the relevant section in `docs/usage.md` or `docs/architecture.md`. Confirm the UI never displays an absolute path, API key, reasoning content, or retrieved raw document dump.

- [ ] **Step 6: Commit any verification-only corrections**

If Tasks 1–5 exposed a feature-scoped defect, stage only this feature's implementation, documentation, and regression-test paths:

```powershell
git add backend/document_knowledge.py backend/assistant_tools.py backend/assistant_agent.py backend/ai_assistant.py backend/api/views.py backend/tests/test_document_knowledge.py backend/tests/test_assistant_tools.py backend/tests/test_assistant_agent.py backend/tests/test_ai_assistant.py backend/tests/test_server.py backend/tests/test_assistant_documentation.py frontend/src/api.js frontend/src/assistantConversations.js frontend/src/components/AssistantPanel.vue frontend/src/style.css frontend/tests/api.test.js frontend/tests/assistantConversations.test.js frontend/tests/assistantPanel.test.js README.md docs/assistant-knowledge.json docs/ai-assistant.md docs/usage.md docs/architecture.md docs/development.md docs/deployment.md
git commit -m "fix: resolve assistant knowledge verification findings"
```

If no correction was required, record the exact test counts and build result in the final handoff without creating an empty commit.
