# Civics Radar

Civics Radar is a multi-tenant bill-monitoring application with authenticated feeds, official-source links, version-scoped structured audits, alerts, saved views, taxonomy administration, and operations views.

## Security model

Every private API uses `Authorization: Bearer <token>`. Tokens have a public lookup prefix and a 256-bit secret; only a salted PBKDF2-SHA256 hash is stored. Tokens are returned once when a system administrator creates an account. Email addresses are identifiers, never authentication credentials or API fallbacks.

Roles are `user`, tenant `admin`, and bootstrap `system_admin`. Tenant records are always selected and mutated through the authenticated user ID. Only the bootstrap system administrator can create accounts. Set `CIVICS_BOOTSTRAP_ADMIN_TOKEN` to a randomly generated secret of at least 32 bytes, use it only to issue account tokens, and rotate/remove it afterward where operationally practical.

The built-in limiter is appropriate for a single process. A multi-worker or horizontally scaled installation should enforce distributed rate limits at the reverse proxy or shared store.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export CIVICS_DB=/tmp/civics.db
export CIVICS_BOOTSTRAP_ADMIN_TOKEN="$(openssl rand -base64 48)"
uvicorn civics_app.main:app --host 127.0.0.1 --port 8844
```

Create an initial tenant without placing the token in a URL:

```bash
curl -sS -X POST http://127.0.0.1:8844/api/admin/accounts \
  -H "Authorization: Bearer $CIVICS_BOOTSTRAP_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"account_name":"My organization","email":"admin@example.org","role":"admin"}'
```

Save the returned `api_token` in a secret manager. The UI stores a supplied token in tab-scoped `sessionStorage`, never cookies, query strings, or server logs.

## Data and provenance

Congress.gov and LegiScan adapters preserve provider payloads in immutable bill versions, link separately to the official record and available bill text, retain provider action timelines, and create a new audit job for each text hash and active-taxonomy version. State ingestion runs are recorded separately (`LegiScan:MO`, `LegiScan:TX`, and so on).

Without `OPENAI_API_KEY`, the worker uses the explicitly labeled `keyword-deterministic` fallback against provider titles and summaries. With a key, it uses the Responses API with strict structured output and only `gpt-5.6-sol`; any other `CIVICS_AUDIT_MODEL` is refused. The versioned prompt is in `civics_app/prompts/bill_audit_v1.md`. Neither path claims to have verified full bill text unless the provider payload actually supplies it. Audit output is informational, can be incomplete, and is not legal advice.

Configure `CONGRESS_API_KEY` and/or `LEGISCAN_API_KEY`, then call the admin sync endpoints with a tenant-admin Bearer token. Missing keys and failures are recorded as ingestion runs without exposing credentials.

## Notification delivery

Users can select in-app, email, and Telegram channels. External notifications remain queued until `civics-notification-worker.timer` runs. Configure the `CIVICS_SMTP_*` variables and/or `CIVICS_TELEGRAM_BOT_TOKEN` in `/etc/civics.env`; without provider or user destinations the worker records `not_configured` rather than crashing. Stored failure messages contain only an exception type, never a credential-bearing URL or provider response.

## Schema migrations

`init_db()` applies idempotent compatibility migrations and records versions in `schema_migrations`. Human-readable SQL lives in `migrations/`. Back up the SQLite database before upgrading. The legacy cleartext token column is nulled during migration; legacy tokens must be reissued because their old unindexed format cannot safely become indexed tokens.

## Tests

```bash
pytest -q
npm install
npx playwright install chromium
npm run e2e
```

The suite covers token hashing and rejection, RBAC, tenant isolation, validation, headers, pagination/sorting, official provenance, provider adapters, audit idempotency, responsive navigation, read states, saved views, and detail dialogs.

## Hardened deployment templates

The files in `deploy/` run as the dedicated `civics` user with a restrictive umask, empty Linux capability set, and read-only system paths. Create the service account and private data/backup directories during provisioning:

```bash
sudo useradd --system --home /opt/civics --shell /usr/sbin/nologin civics
sudo install -d -o civics -g civics -m 0700 /opt/civics/data
sudo install -d -o civics -g civics -m 0700 /opt/civics/backups
```

Enable the API and relevant sync/audit/notification timers. `civics-db-backup.timer` performs a consistent SQLite online backup, verifies it, and retains 14 daily copies by default. Keep `/etc/civics.env` root-owned and mode `0600`. Terminate TLS at a hardened reverse proxy; HSTS is emitted for HTTPS requests. Review templates for the target distribution before enabling them. No live configuration is modified by this repository.

The admin operations view reports provider runs, per-state status, audit and notification queues, delivery readiness, and feedback volume. Host service/timer state remains intentionally outside the web process and should be inspected through systemd.
