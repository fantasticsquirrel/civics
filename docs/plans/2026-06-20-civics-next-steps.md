# Civics Platform Next Steps Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Turn the current Civics Radar foundation into a production-ready legislative monitoring platform with reliable government data ingestion, real LLM auditing, user-ready notifications, state coverage, and admin operations.

**Architecture:** Keep the existing FastAPI + SQLite/systemd deployment working while extracting cohesive provider, audit, account, and notification modules behind tested interfaces. Ingestion creates immutable bill versions and audit jobs; a worker audits each bill text hash once; notifications fan out from stored audit flags to user interests.

**Tech Stack:** Python 3.11, FastAPI, SQLite for current deployment, pytest, Playwright, systemd services/timers, Congress.gov API, LegiScan API, future OpenAI/Codex auditing provider.

---

## Current baseline

- Repo: `/opt/civics`
- Public URL: `https://civics.multihost.ing/`
- Runtime service: `civics-api.service` on `127.0.0.1:8844`
- Federal sync timer: `civics-congress-sync.timer`
- Audit worker timer: `civics-audit-worker.timer`
- Current core file: `civics_app/main.py`
- Congress provider: `civics_app/congress.py`
- Audit worker CLI: `civics_app/sync_audits.py`
- Existing tests: `tests/test_congress.py`, `tests/test_platform_expansion.py`
- Existing e2e: `e2e/civics.spec.cjs`

## Non-negotiable implementation rules

1. Use TDD for behavior changes: write a failing test, verify red, implement, verify green.
2. Preserve source provenance on all bill/provider rows.
3. Do not leak API keys/tokens in logs, screenshots, commits, or final reports.
4. Audit each bill text hash once against the admin taxonomy, then fan out stored flags to users.
5. For live provider work, verify real provider endpoints and public dashboard behavior, not just unit tests.
6. Commit focused slices; avoid staging DB files, screenshots, caches, logs, or local secrets.

---

## Phase 1: Modularize the backend without behavior changes

### Task 1.1: Add characterization tests around current API behavior

**Objective:** Lock current behavior before splitting `main.py` into modules.

**Files:**
- Modify: `tests/test_platform_expansion.py`
- Possibly create: `tests/test_api_characterization.py`

**Step 1: Write failing/characterization tests**

Add tests for:
- `GET /api/health` returns counts.
- `GET /api/admin/provider-health` includes `congress_gov` and `legiscan`.
- `GET /api/dashboard` returns user/interests/matches/notifications.
- `GET /api/bills?search=library&jurisdiction=US` filters demo data.

**Step 2: Run tests**

```bash
pytest tests/test_api_characterization.py -q
```

Expected: pass if behavior already exists; if a test fails due to unclear expected shape, adjust the test only after confirming current live/API behavior.

**Step 3: Commit**

```bash
git add tests/test_api_characterization.py tests/test_platform_expansion.py
git commit -m "test: characterize civics API contracts"
```

### Task 1.2: Extract database/schema helpers

**Objective:** Move DB connection, schema creation, and row helpers out of `civics_app/main.py` without changing API behavior.

**Files:**
- Create: `civics_app/db.py`
- Modify: `civics_app/main.py`
- Modify tests if imports change.

**Implementation notes:**
- Move `DEFAULT_DB`, `db_path`, `connect`, `init_db`, `rows_to_dicts`, `decode_json_field`.
- Keep function names stable for imports or add compatibility imports in `main.py`.

**Verification:**

```bash
pytest -q
npx playwright test
```

**Commit:**

```bash
git add civics_app/db.py civics_app/main.py tests
git commit -m "refactor: extract database helpers"
```

### Task 1.3: Extract domain services

**Objective:** Split business logic into focused services.

**Files:**
- Create: `civics_app/services/accounts.py`
- Create: `civics_app/services/audits.py`
- Create: `civics_app/services/bills.py`
- Create: `civics_app/services/notifications.py`
- Create: `civics_app/services/providers.py`
- Modify: `civics_app/main.py`

**Service boundaries:**
- `accounts.py`: account creation, token lookup, interest updates.
- `audits.py`: audit job creation/processing, audit run/flag writes.
- `bills.py`: bill upsert, bill versions, bill search/detail.
- `notifications.py`: match generation, preferences, digest preview.
- `providers.py`: provider health, Congress/LegiScan sync orchestration.

**Verification:**

```bash
pytest -q
npx playwright test
```

**Commit:**

```bash
git add civics_app/services civics_app/main.py tests
git commit -m "refactor: split civics domain services"
```

---

## Phase 2: Real OpenAI/Codex audit provider

### Task 2.1: Define structured audit schema

**Objective:** Create a strict JSON contract for LLM audit output.

**Files:**
- Create: `civics_app/audit_schema.py`
- Create: `tests/test_audit_schema.py`

**Schema requirements:**
Each category result must include:
- `category_slug`
- `flag_state`: `yes`, `possible`, or `no`
- `severity`: `low`, `medium`, or `high`
- `confidence`: float from `0.0` to `1.0`
- `rationale`
- `citation`
- `user_summary`
- `affected_groups`: list of strings
- `concerns`: list of strings

**Test cases:**
- Valid output parses.
- Unknown category slug fails validation.
- Invalid severity fails validation.
- Missing citation fails validation.

**Verification:**

```bash
pytest tests/test_audit_schema.py -q
```

**Commit:**

```bash
git add civics_app/audit_schema.py tests/test_audit_schema.py
git commit -m "feat: add structured audit result schema"
```

### Task 2.2: Add OpenAI audit provider interface

**Objective:** Add provider abstraction with deterministic fallback and real OpenAI path when configured.

**Files:**
- Create: `civics_app/audit_providers.py`
- Modify: `civics_app/services/audits.py` or current audit code.
- Modify: `.env.example`
- Create: `tests/test_audit_providers.py`

**Implementation notes:**
- Keep deterministic fallback for tests and no-key operation.
- If `OPENAI_API_KEY` is present, use the selected model from env, e.g. `CIVICS_AUDIT_MODEL`.
- Validate returned JSON with `audit_schema.py` before writing DB rows.
- Store provider/model/prompt version in audit metadata.
- Never log full prompt with secrets or account data.

**Verification:**

```bash
pytest tests/test_audit_providers.py tests/test_platform_expansion.py -q
```

**Commit:**

```bash
git add civics_app/audit_providers.py civics_app/audit_schema.py civics_app/services/audits.py .env.example tests
git commit -m "feat: add pluggable LLM audit provider"
```

### Task 2.3: Add prompt/version management

**Objective:** Make audit prompts auditable and versioned.

**Files:**
- Create: `civics_app/prompts/bill_audit_v1.md`
- Modify: audit provider code.
- Create: `tests/test_audit_prompt_contract.py`

**Prompt must include:**
- Neutral civic language.
- Instruction to cite bill text/summary excerpts.
- Instruction to avoid party/political persuasion language.
- JSON-only output requirement.
- Category taxonomy embedded as structured data.

**Verification:**

```bash
pytest tests/test_audit_prompt_contract.py -q
```

**Commit:**

```bash
git add civics_app/prompts tests/test_audit_prompt_contract.py civics_app/audit_providers.py
git commit -m "feat: version bill audit prompt"
```

---

## Phase 3: Live LegiScan state ingestion

### Task 3.1: Implement LegiScan client

**Objective:** Fetch recent state bills through LegiScan when `LEGISCAN_API_KEY` is configured.

**Files:**
- Create: `civics_app/legiscan.py`
- Modify: provider orchestration service/current LegiScan stub.
- Create: `tests/test_legiscan.py`

**Client behavior:**
- `LegiScanClient.ready` is false without key.
- `status()` returns safe `missing_api_key` when absent.
- `fetch_recent_bills(state, limit)` calls LegiScan API with timeout/retry.
- Normalize bills to the internal bill dict shape.
- Preserve LegiScan bill ID/session/state URL/source fields.

**Verification:**

```bash
pytest tests/test_legiscan.py -q
```

**Commit:**

```bash
git add civics_app/legiscan.py tests/test_legiscan.py civics_app/main.py
git commit -m "feat: add LegiScan state bill client"
```

### Task 3.2: Add per-state sync status and scheduling

**Objective:** Track each state sync separately and optionally schedule important states.

**Files:**
- Modify: schema in `civics_app/db.py` or current `init_db`.
- Create: `deploy/civics-legiscan-sync@.service`
- Create: optional timer examples in `deploy/`.
- Modify: README.
- Create: `tests/test_legiscan_sync_status.py`

**Behavior:**
- `ingestion_runs.source_name` should distinguish `LegiScan:MO`, `LegiScan:TX`, etc., or add `jurisdiction_code` column.
- UI/provider health should show per-state last success/failure.

**Verification:**

```bash
pytest tests/test_legiscan_sync_status.py -q
sudo systemctl daemon-reload
```

**Commit:**

```bash
git add deploy README.md civics_app tests
git commit -m "feat: add per-state LegiScan sync status"
```

---

## Phase 4: Production authentication and admin UX

### Task 4.1: Replace raw API token creation with hashed tokens

**Objective:** Stop storing plaintext API tokens.

**Files:**
- Modify: users schema migration.
- Modify: account service.
- Create: `tests/test_auth_tokens.py`

**Behavior:**
- New accounts receive a one-time visible token.
- DB stores only a hash.
- `X-API-Token` lookup hashes candidate token and compares to stored hash.
- Existing demo token migration path is explicit and documented.

**Verification:**

```bash
pytest tests/test_auth_tokens.py tests/test_platform_expansion.py -q
```

**Commit:**

```bash
git add civics_app tests README.md
git commit -m "feat: store hashed account API tokens"
```

### Task 4.2: Add admin category editor UI

**Objective:** Let admins manage taxonomy from the dashboard.

**Files:**
- Modify: HTML/JS in `civics_app/main.py` or extracted frontend file.
- Modify: category APIs as needed.
- Create: Playwright test in `e2e/civics.spec.cjs` or `e2e/admin-categories.spec.cjs`.

**Behavior:**
- Admin can add a category.
- Admin can deactivate/reactivate a category.
- Taxonomy version changes when active taxonomy changes.
- New bill versions get queued for the new taxonomy version.

**Verification:**

```bash
pytest -q
npx playwright test e2e/admin-categories.spec.cjs
```

**Commit:**

```bash
git add civics_app/main.py e2e tests
git commit -m "feat: add admin taxonomy editor"
```

### Task 4.3: Add user settings UI

**Objective:** Let users manage interests, jurisdictions, severity thresholds, saved views, and notification preferences.

**Files:**
- Modify frontend HTML/JS.
- Modify APIs if needed.
- Create/modify Playwright tests.

**Verification:**

```bash
pytest -q
npx playwright test
```

**Commit:**

```bash
git add civics_app/main.py e2e tests
git commit -m "feat: add user settings UI"
```

---

## Phase 5: Notification delivery

### Task 5.1: Add email delivery provider

**Objective:** Deliver digest/instant notifications through a configured SMTP or provider API.

**Files:**
- Create: `civics_app/notification_delivery.py`
- Modify: `.env.example`
- Create: `tests/test_notification_delivery.py`
- Create: `deploy/civics-notification-worker.service`
- Create: `deploy/civics-notification-worker.timer`

**Behavior:**
- Without credentials, delivery status is `not_configured`, not a crash.
- With credentials, queued email notifications are sent and marked delivered.
- Failed sends record safe error messages.

**Verification:**

```bash
pytest tests/test_notification_delivery.py -q
```

**Commit:**

```bash
git add civics_app deploy .env.example tests
git commit -m "feat: add notification delivery worker"
```

### Task 5.2: Add Telegram/admin delivery option

**Objective:** Support operational notifications to the admin/home channel.

**Files:**
- Modify: notification delivery provider.
- Create tests for delivery routing with mocked sender.

**Verification:**

```bash
pytest tests/test_notification_delivery.py -q
```

**Commit:**

```bash
git add civics_app tests
git commit -m "feat: add Telegram notification route"
```

---

## Phase 6: Bill detail and trust UX

### Task 6.1: Add timeline and version history

**Objective:** Show bill actions and audit history on bill detail pages.

**Files:**
- Modify schema to store action timeline if provider exposes it.
- Modify bill detail API.
- Modify UI detail panel.
- Create tests.

**Behavior:**
- Bill detail returns `versions`, `audit_runs`, and `timeline` arrays.
- UI shows last action, prior versions, and audit timestamp/model.

**Verification:**

```bash
pytest -q
npx playwright test
```

**Commit:**

```bash
git add civics_app tests e2e
git commit -m "feat: show bill version and audit history"
```

### Task 6.2: Add trust/transparency panel

**Objective:** Make AI audit results understandable and auditable.

**Files:**
- Modify bill detail API/UI.
- Create Playwright coverage.

**UI must show:**
- AI audit provider/model.
- Prompt/taxonomy version.
- Confidence score.
- Citation/excerpt.
- Disclaimer: informational, not legal advice.
- Feedback controls: wrong tag, severity too high/low, not relevant.

**Commit:**

```bash
git add civics_app e2e tests
git commit -m "feat: add audit transparency panel"
```

---

## Phase 7: Operations and observability

### Task 7.1: Add admin operations dashboard

**Objective:** Give operators one place to inspect ingestion, worker, provider, and notification health.

**Files:**
- Modify provider-health endpoint or create `GET /api/admin/ops`.
- Modify UI.
- Add tests.

**Dashboard sections:**
- Provider status by source/jurisdiction.
- Recent ingestion runs.
- Audit queue depth by status.
- Notification queue depth by status.
- Last successful Congress.gov sync.
- Last successful state sync.
- Service/timer health if safe to expose locally.

**Verification:**

```bash
pytest -q
npx playwright test
```

**Commit:**

```bash
git add civics_app tests e2e
git commit -m "feat: add admin operations dashboard"
```

### Task 7.2: Add backup/export path

**Objective:** Protect SQLite data before larger migrations.

**Files:**
- Create: `scripts/backup_civics_db.sh`
- Create: `deploy/civics-db-backup.service`
- Create: `deploy/civics-db-backup.timer`
- Modify README.

**Behavior:**
- Backups go outside git, e.g. `/opt/civics/backups/`.
- Keep retention policy, e.g. latest 14 daily backups.
- Never stage backup DBs.

**Verification:**

```bash
bash scripts/backup_civics_db.sh
ls -lh /opt/civics/backups/
git status --short
```

**Commit:**

```bash
git add scripts deploy README.md .gitignore
git commit -m "chore: add civics database backup timer"
```

---

## Phase 8: Deployment checklist for every future slice

Run before reporting done:

```bash
git status --short --branch
git diff --check
pytest -q
npx playwright test
sudo systemctl restart civics-api.service
sudo systemctl is-active civics-api.service
sudo systemctl is-active civics-congress-sync.timer
sudo systemctl is-active civics-audit-worker.timer
curl -fsS https://civics.multihost.ing/api/health
curl -fsS https://civics.multihost.ing/api/admin/provider-health
```

For UI changes, capture a 1920x1080 screenshot:

```bash
node - <<'JS'
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: '+e.message));
  page.on('console', msg => { if (['error','warning'].includes(msg.type())) errors.push(msg.type()+': '+msg.text()); });
  await page.goto('https://civics.multihost.ing/', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'artifacts/civics-dashboard-live.png', fullPage: false });
  console.log(JSON.stringify({ errors }, null, 2));
  await browser.close();
})();
JS
```

Commit and push only intended source/docs/config files:

```bash
git add <intended files only>
git commit -m "type: concise description"
git push origin main
```

---

## Suggested next immediate slice

Start with **Phase 1 modularization** before adding more feature depth. The current single-file backend works, but future state ingestion, LLM auditing, and notification delivery will be much safer after extracting `db`, `services/accounts`, `services/audits`, `services/bills`, `services/notifications`, and `services/providers` with characterization tests.
