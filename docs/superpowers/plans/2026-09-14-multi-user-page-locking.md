# Multi-User Page Locking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add open account registration, authenticated sessions, task creator attribution, page-scoped persistence/versioning, and exclusive renewable page locks while removing timer-based autosave.

**Architecture:** Extend the existing SQLite `JobStore` with users, sessions, and page leases; keep analyzed page bodies in atomic `result.json` files and save one page at a time under the API process lock. Add a same-origin cookie authentication layer and a frontend collaboration composable that owns dirty state, lock acquisition/heartbeat/release, and save-before-navigation.

**Tech Stack:** Python 3.14 standard library (`hashlib.scrypt`, `secrets`, `sqlite3`, `http.cookies`), existing `ThreadingHTTPServer`, Vue 3 Composition API, Node test runner, SQLite WAL, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-14-multi-user-page-locking-design.md`

## Global Constraints

- Registration is open and every active account can access every task.
- Username length is 3-40 characters; password length is 10-128 characters.
- Sessions expire after seven days; the cookie is HttpOnly, SameSite=Lax, Path=/, and Secure when configured.
- Page leases last 90 seconds and heartbeat every 30 seconds.
- Page edits never use task-level `revision`; every page starts at `reviewRevision: 0`.
- Editing must not schedule IndexedDB or backend writes; only page navigation and explicit save persist edits.
- Same user in two browser tabs is treated as two clients and cannot edit the same page simultaneously.
- Existing jobs and drafts remain readable without a data rewrite.
- Do not overwrite unrelated dirty-worktree changes.

---

### Task 1: Account and Session Persistence

**Files:**
- Create: `backend/auth_store.py`
- Modify: `backend/job_store.py`
- Create: `backend/tests/test_auth_store.py`
- Modify: `backend/tests/test_job_store.py`

**Interfaces:**
- Produces `AuthStore(path)`, `register(username, password)`, `authenticate(username, password)`, `create_session(user_id)`, `resolve_session(token)`, and `delete_session(token)`.
- Extends `JobStore.create_job(..., creator_user_id=None, creator_username=None)` and job dictionaries with `createdBy`.

- [ ] **Step 1: Write failing account persistence tests**

```python
def test_register_hashes_password_and_authenticates(tmp_path):
    store = AuthStore(tmp_path / "jobs.db")
    store.initialize()
    user = store.register("alice", "correct horse battery")
    assert store.authenticate("Alice", "correct horse battery")["userId"] == user["userId"]
    assert store.authenticate("alice", "wrong password") is None

def test_session_round_trip_and_logout(tmp_path):
    store = initialized_store(tmp_path)
    user = store.register("alice", "correct horse battery")
    token = store.create_session(user["userId"])
    assert store.resolve_session(token)["username"] == "alice"
    store.delete_session(token)
    assert store.resolve_session(token) is None
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m unittest backend.tests.test_auth_store -v`

Expected: import failure for missing `backend.auth_store`.

- [ ] **Step 3: Implement account storage**

Create idempotent SQLite tables `users` and `user_sessions`, normalize usernames with `strip().casefold()`, hash passwords with per-user `secrets.token_bytes(16)` salt and `hashlib.scrypt(n=16384, r=8, p=1)`, compare with `hmac.compare_digest`, return JSON-safe user dictionaries, and store only SHA-256 session-token hashes.

- [ ] **Step 4: Write failing creator migration test**

```python
def test_job_creator_is_returned_after_schema_upgrade(self):
    store = self.store()
    store.create_job(
        job_id="a" * 32, analysis_signature="sig", job_folder=self.root / "job",
        original_target_name="drawing.pdf", algorithm_version="v1",
        creator_user_id="u1", creator_username="alice",
    )
    assert store.get_job("a" * 32)["createdBy"] == {"userId": "u1", "username": "alice"}
```

- [ ] **Step 5: Run creator test and verify RED**

Run: `python -m unittest backend.tests.test_job_store -v`

Expected: `create_job()` rejects creator keyword arguments.

- [ ] **Step 6: Implement creator columns and mapping**

Use `PRAGMA table_info(jobs)` to add nullable `creator_user_id` and `creator_username` on existing databases. Update inserts and row serialization without changing active-job deduplication.

- [ ] **Step 7: Run persistence tests and verify GREEN**

Run: `python -m unittest backend.tests.test_auth_store backend.tests.test_job_store -v`

- [ ] **Step 8: Commit**

```powershell
git add backend/auth_store.py backend/job_store.py backend/tests/test_auth_store.py backend/tests/test_job_store.py
git commit -m "feat: add account and session persistence"
```

### Task 2: Authentication HTTP Boundary

**Files:**
- Create: `backend/api/auth.py`
- Modify: `backend/api/views.py`
- Modify: `backend/config.py`
- Modify: `backend/tests/test_server.py`

**Interfaces:**
- Produces `current_user(handler) -> dict | None`, `require_user(handler) -> dict`, session cookie helpers, and handlers for `/api/auth/register`, `/api/auth/login`, `/api/auth/logout`, `/api/auth/me`.
- Consumes `AuthStore` from Task 1.

- [ ] **Step 1: Write failing endpoint tests**

```python
def test_register_sets_http_only_cookie_and_me_returns_user(self):
    status, payload, headers = self.post_json("/api/auth/register", {
        "username": "alice", "password": "correct horse battery",
    })
    self.assertEqual(status, 201)
    self.assertEqual(payload["user"]["username"], "alice")
    self.assertIn("HttpOnly", headers["Set-Cookie"])
    self.assertIn("SameSite=Lax", headers["Set-Cookie"])

def test_job_api_requires_authentication(self):
    status, payload, _ = self.get_json("/api/jobs/recent-batch")
    self.assertEqual(status, 401)
```

- [ ] **Step 2: Run endpoint tests and verify RED**

Run: `python -m unittest backend.tests.test_server -v`

Expected: auth route returns 404 and protected route is still accessible.

- [ ] **Step 3: Implement auth helpers and routes**

Parse the session cookie with `SimpleCookie`, return 401 JSON for missing/expired sessions, set and clear `drawing_marker_session`, validate username/password lengths, and require same-origin `Origin` on state-changing authenticated requests. Exempt `/api/health`, auth register/login, and static files.

- [ ] **Step 4: Add login, duplicate registration, logout, expiry, and malformed-origin tests**

Assert duplicate usernames return 409, invalid login returns 401 without identifying which credential failed, logout clears the cookie, expired sessions return 401, and cross-origin writes return 403.

- [ ] **Step 5: Run server tests and verify GREEN**

Run: `python -m unittest backend.tests.test_server -v`

- [ ] **Step 6: Commit**

```powershell
git add backend/api/auth.py backend/api/views.py backend/config.py backend/tests/test_server.py
git commit -m "feat: add registration and authenticated sessions"
```

### Task 3: Attribute New Tasks to Their Creator

**Files:**
- Modify: `backend/api/views.py`
- Modify: `backend/job_runner.py`
- Modify: `backend/tests/test_server.py`
- Modify: `backend/tests/test_job_store.py`

**Interfaces:**
- Consumes authenticated `request_user` attached by Task 2.
- Produces `createdBy: {userId, username} | null` in job creation, queue, recent batch, and workspace responses.

- [ ] **Step 1: Write failing creator propagation test**

Create a job request authenticated as Alice and assert the SQLite job row, `job.json`, initial `result.json`, and returned workspace all contain Alice's immutable creator snapshot.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest backend.tests.test_server.ServerTests.test_create_job_records_authenticated_creator -v`

Expected: `createdBy` is absent.

- [ ] **Step 3: Implement creator propagation**

Pass the authenticated user into `JobStore.create_job`, write `createdBy` to job metadata and initial results, preserve it when the Worker publishes the final result, and map missing historical values to `None`.

- [ ] **Step 4: Verify creator propagation**

Run: `python -m unittest backend.tests.test_server backend.tests.test_job_store -v`

- [ ] **Step 5: Commit**

```powershell
git add backend/api/views.py backend/job_runner.py backend/tests/test_server.py backend/tests/test_job_store.py
git commit -m "feat: record drawing task creators"
```

### Task 4: Persistent Page Lease Store

**Files:**
- Create: `backend/page_locks.py`
- Create: `backend/tests/test_page_locks.py`
- Modify: `backend/api/views.py`
- Modify: `backend/tests/test_server.py`

**Interfaces:**
- Produces `PageLockStore.acquire(job_id, page, user, client_id)`, `renew(...)`, `release(...)`, `list_active(job_id)`, `validate(...)`, and `acquire_batch(...)`.
- HTTP routes return 423 with `{error, lock: {page, owner, expiresAt}}` for contention.

- [ ] **Step 1: Write failing lease-store concurrency tests**

```python
def test_two_clients_cannot_acquire_same_page(tmp_path):
    store = PageLockStore(tmp_path / "jobs.db", lease_seconds=90)
    store.initialize()
    first = store.acquire("job", 15, USER_ALICE, "tab-a")
    with pytest.raises(PageLocked) as conflict:
        store.acquire("job", 15, USER_BOB, "tab-b")
    assert conflict.value.lock["owner"]["username"] == "alice"
    assert first["lockToken"]

def test_different_pages_can_be_locked_concurrently(tmp_path):
    store.acquire("job", 3, USER_ALICE, "tab-a")
    store.acquire("job", 8, USER_BOB, "tab-b")
    assert {lock["page"] for lock in store.list_active("job")} == {3, 8}
```

- [ ] **Step 2: Run lease tests and verify RED**

Run: `python -m unittest backend.tests.test_page_locks -v`

Expected: missing `backend.page_locks`.

- [ ] **Step 3: Implement transactional leases**

Create the `page_locks` table, use `BEGIN IMMEDIATE`, delete/replace expired rows atomically, generate a 32-byte URL-safe lock token and store only its SHA-256, bind ownership to user ID plus client instance ID, and make batch acquisition all-or-nothing.

- [ ] **Step 4: Add renew, release, expiry, bad-token, same-account/different-tab, and all-or-nothing batch tests**

Use an injected clock so tests advance beyond 90 seconds without sleeping.

- [ ] **Step 5: Run lease-store tests and verify GREEN**

Run: `python -m unittest backend.tests.test_page_locks -v`

- [ ] **Step 6: Write failing lock API tests**

Cover list, acquire, renew, release, nonexistent page, unauthorized request, and HTTP 423 contention payload.

- [ ] **Step 7: Implement lock routes and verify GREEN**

Run: `python -m unittest backend.tests.test_server -v`

- [ ] **Step 8: Commit**

```powershell
git add backend/page_locks.py backend/api/views.py backend/tests/test_page_locks.py backend/tests/test_server.py
git commit -m "feat: add renewable exclusive page locks"
```

### Task 5: Page-Scoped Save and Revision Control

**Files:**
- Create: `backend/page_reviews.py`
- Modify: `backend/api/views.py`
- Modify: `backend/tests/test_server.py`

**Interfaces:**
- Produces `save_review_page(result_path, page_number, incoming_page, base_revision, user) -> dict`.
- `PUT /api/jobs/{jobId}/pages/{page}` requires `page`, `basePageRevision`, `clientInstanceId`, and `lockToken`.

- [ ] **Step 1: Write failing page-save tests**

```python
def test_different_pages_save_from_revision_zero_without_conflict(self):
    alice_lock = self.acquire_page(1, self.alice)
    bob_lock = self.acquire_page(2, self.bob)
    self.save_page(1, revision=0, lock=alice_lock, number="A1")
    self.save_page(2, revision=0, lock=bob_lock, number="B1")
    result = self.read_result()
    self.assertEqual(result["pages"][0]["reviewRevision"], 1)
    self.assertEqual(result["pages"][1]["reviewRevision"], 1)

def test_stale_same_page_save_is_rejected_without_overwrite(self):
    lock = self.acquire_page(1, self.alice)
    self.save_page(1, revision=0, lock=lock, number="A1")
    status = self.save_page(1, revision=0, lock=lock, number="STALE").status
    self.assertEqual(status, 409)
    self.assertEqual(self.read_number(1), "A1")
```

- [ ] **Step 2: Run page-save tests and verify RED**

Run: `python -m unittest backend.tests.test_server -v`

Expected: PUT page route does not exist or ignores page revision.

- [ ] **Step 3: Implement page-scoped save**

Validate the active lease before entering the result-file critical section, reread and revalidate it inside the critical section, treat missing `reviewRevision` as 0, reuse `_validated_pages` with a one-element array, replace only the target page, add `reviewedBy`/`reviewedAt`, atomically dump, and return the updated page metadata.

- [ ] **Step 4: Disable legacy whole-job editing**

Keep the old PUT route only as a 409 migration response for ordinary completed jobs. Preserve tutorial-only behavior through its dedicated endpoints.

- [ ] **Step 5: Add no-lock, expired-lock, wrong-token, wrong-page, and historical-page tests**

Assert each rejection leaves `result.json` byte-for-byte unchanged.

- [ ] **Step 6: Run backend tests and verify GREEN**

Run: `python -m unittest discover -s backend/tests -v`

- [ ] **Step 7: Commit**

```powershell
git add backend/page_reviews.py backend/api/views.py backend/tests/test_server.py
git commit -m "feat: save reviewed drawings by page revision"
```

### Task 6: Frontend Authentication Shell

**Files:**
- Create: `frontend/src/composables/useAuthSession.js`
- Create: `frontend/src/components/AuthGate.vue`
- Create: `frontend/tests/authSession.test.js`
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/AppHeader.vue`

**Interfaces:**
- Produces `useAuthSession({ api })` with `status`, `user`, `login`, `register`, `logout`, and `restore`.
- Adds `api.me`, `api.register`, `api.login`, and `api.logout`; every request uses same-origin cookies.

- [ ] **Step 1: Write failing auth-session tests**

```javascript
test('restore gates the workspace until authentication resolves', async () => {
  const auth = useAuthSession({ api: { me: async () => ({ user: { username: 'alice' } }) } })
  assert.equal(auth.status.value, 'loading')
  await auth.restore()
  assert.equal(auth.status.value, 'authenticated')
  assert.equal(auth.user.value.username, 'alice')
})
```

- [ ] **Step 2: Run test and verify RED**

Run: `npm test -- authSession.test.js`

Expected: missing composable.

- [ ] **Step 3: Implement auth state and API methods**

Keep credentials in HttpOnly cookies only; do not use localStorage for session tokens. Normalize API 401 errors into an `AuthenticationRequired` error carrying status.

- [ ] **Step 4: Add AuthGate and Header integration**

Provide login/register tabs, validation messages, submit loading state, current username, and logout. Mount the existing workspace only after `restore()` resolves authenticated.

- [ ] **Step 5: Run frontend tests and build**

Run: `npm test`

Run: `npm run build`

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/composables/useAuthSession.js frontend/src/components/AuthGate.vue frontend/tests/authSession.test.js frontend/src/api.js frontend/src/App.vue frontend/src/components/AppHeader.vue
git commit -m "feat: add account registration and login UI"
```

### Task 7: Frontend Page Collaboration State

**Files:**
- Create: `frontend/src/composables/usePageCollaboration.js`
- Create: `frontend/tests/pageCollaboration.test.js`
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/composables/useWorkspacePersistence.js`
- Modify: `frontend/src/composables/useProjectWorkspace.js`
- Modify: `frontend/src/composables/useUploadCloseGuard.js`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/composables/usePageNavigation.js`

**Interfaces:**
- Produces `dirtyPages`, `activeLock`, `lockSummaries`, `markDirty(page)`, `enterPage(page)`, `leavePage()`, `saveCurrentPage()`, `heartbeat()`, and `dispose()`.
- Consumes page lock/save API methods and existing draft `saveDraft` only at save boundaries.

- [ ] **Step 1: Write failing no-timer-autosave test**

```javascript
test('editing marks the page dirty without scheduling persistence', async () => {
  const calls = []
  const collaboration = createHarness({ savePage: () => calls.push('save') })
  collaboration.markDirty(3)
  await new Promise(resolve => setTimeout(resolve, 2100))
  assert.equal(collaboration.dirtyPages.value.has(3), true)
  assert.deepEqual(calls, [])
})
```

- [ ] **Step 2: Run test and verify RED**

Run: `npm test -- pageCollaboration.test.js`

Expected: missing collaboration composable.

- [ ] **Step 3: Implement client identity and lock lifecycle**

Generate/read `drawing-marker.client-instance-id` in `sessionStorage`, acquire before page display, store the raw token only in memory, heartbeat every 30 seconds, release using normal fetch on navigation and keepalive fetch on unload, and discard stale async navigation results by generation.

- [ ] **Step 4: Write failing save-before-navigation tests**

Assert dirty navigation calls `draft -> save -> release -> acquire -> render`; clean navigation omits draft/save; failed save keeps the old page, old lock, and dirty flag; locked target leaves the current page unchanged.

- [ ] **Step 5: Implement page save and navigation orchestration**

Replace `useWorkspacePersistence` timers with explicit `markDirty` and current-page save. Update deep watchers to mark the current page only. Make project switching and logout call the same leave-page path.

- [ ] **Step 6: Extend close guard tests and implementation**

Warn only when dirty, saving, or lease state is uncertain. Do not warn merely because a clean page lock exists. Add best-effort release after the user confirms browser unload.

- [ ] **Step 7: Add lock-state page buttons and loss-of-lock behavior**

Show owner names for 423 responses, style locked page buttons, refresh summaries after acquire/release and periodically while a task is open, and make the current page read-only when a heartbeat confirms lock loss.

- [ ] **Step 8: Run frontend tests and build**

Run: `npm test`

Run: `npm run build`

- [ ] **Step 9: Commit**

```powershell
git add frontend/src/composables/usePageCollaboration.js frontend/tests/pageCollaboration.test.js frontend/src/api.js frontend/src/composables/useWorkspacePersistence.js frontend/src/composables/useProjectWorkspace.js frontend/src/composables/useUploadCloseGuard.js frontend/src/App.vue frontend/src/composables/usePageNavigation.js
git commit -m "feat: coordinate page editing with renewable locks"
```

### Task 8: Shared-Task Appearance and Bulk Operations

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/numbering.js`
- Modify: `frontend/tests/numbering.test.js`
- Modify: `frontend/src/api.js`
- Modify: `backend/api/views.py`
- Modify: `backend/tests/test_server.py`

**Interfaces:**
- Consumes atomic batch lock acquisition from Task 4.
- Produces a bulk-renumber transaction that acquires all affected pages before mutation and releases them after all page saves.

- [ ] **Step 1: Write failing bulk-lock frontend and backend tests**

Assert a conflict on one page returns all conflicting page/owner summaries, acquires no new locks, and leaves every candidate number unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest backend.tests.test_server -v`

Run: `npm test -- numbering.test.js`

- [ ] **Step 3: Implement batch endpoint and bulk-renumber orchestration**

Acquire the complete analyzed page range in one SQLite transaction. Only after success mutate numbering, save each changed page with its page revision and lock token, then release locks obtained by the batch while retaining the originally active page lock.

- [ ] **Step 4: Make default marker appearance client-local**

Persist default weld/component styles in localStorage per browser account namespace. Continue storing candidate-specific `markerStyle` inside page data so exports remain deterministic and page-scoped.

- [ ] **Step 5: Protect destructive task operations**

Before delete or reanalysis, reject when `list_active(job_id)` is non-empty and return page/owner summaries. Keep export and read-only downloads lock-free.

- [ ] **Step 6: Run backend/frontend tests and build**

Run: `python -m unittest discover -s backend/tests -v`

Run: `npm test`

Run: `npm run build`

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/App.vue frontend/src/numbering.js frontend/tests/numbering.test.js frontend/src/api.js backend/api/views.py backend/tests/test_server.py
git commit -m "feat: make bulk review operations lock aware"
```

### Task 9: Deployment, Documentation, and Migration Verification

**Files:**
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `backend/config.py`
- Create: `backend/tests/test_multi_user_workflow.py`

**Interfaces:**
- Adds `DRAWING_MARK_RECOGNITION_SESSION_SECURE` and documented reverse-proxy/TLS requirements.
- Produces an end-to-end two-user workflow regression test.

- [ ] **Step 1: Write failing end-to-end workflow test**

Register Alice and Bob, create a two-page task as Alice, acquire/save page 1 as Alice, acquire/save page 2 as Bob, verify Bob cannot acquire page 1 until release/expiry, and assert both saved values plus creator attribution survive a fresh server/store instance.

- [ ] **Step 2: Run workflow test and verify RED if integration is incomplete**

Run: `python -m unittest backend.tests.test_multi_user_workflow -v`

- [ ] **Step 3: Complete configuration and documentation**

Document open-registration exposure, TLS, Secure cookie setting, seven-day sessions, 90/30-second lease behavior, page-level conflict recovery, single-API-instance result-file constraint, and backup of the shared data volume.

- [ ] **Step 4: Run workflow test and verify GREEN**

Run: `python -m unittest backend.tests.test_multi_user_workflow -v`

- [ ] **Step 5: Run full verification**

Run: `python -m unittest discover -s backend/tests -v`

Run: `npm test` from `frontend`

Run: `npm run build` from `frontend`

Run: `git diff --check`

- [ ] **Step 6: Commit**

```powershell
git add .env.example compose.yaml README.md backend/config.py backend/tests/test_multi_user_workflow.py
git commit -m "docs: document secure multi-user review deployment"
```

### Task 10: Final Compatibility and Security Audit

**Files:**
- Modify only files implicated by failing audit tests.

**Interfaces:**
- Verifies the complete spec; produces no new public API.

- [ ] **Step 1: Verify secrets never appear in responses or logs**

Search serialized users, audit files, error payloads, and test captures for `password`, raw session tokens, and raw lock tokens outside their one-time creation responses.

- [ ] **Step 2: Verify historical data paths**

Load an existing job without creator/page revisions, restore a v2 browser draft, acquire its current page, save once, and confirm unrelated pages and topology fields are unchanged.

- [ ] **Step 3: Verify concurrency invariants**

Run repeated concurrent acquire calls for one page and concurrent saves for distinct pages; assert exactly one same-page owner and both distinct-page edits survive.

- [ ] **Step 4: Run final fresh verification**

Run: `python -m unittest discover -s backend/tests -v`

Run: `npm test` from `frontend`

Run: `npm run build` from `frontend`

Run: `git diff --check`

- [ ] **Step 5: Review scoped diff**

Run: `git status --short` and `git diff --stat`; confirm every changed file belongs to this plan and preserve all pre-existing user changes.
