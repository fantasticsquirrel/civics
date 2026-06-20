# Civics

MVP civics bill-monitoring dashboard for `civics.multihost.ing`.

## Features in this MVP

- Admin-defined categories/taxonomy.
- Federal + state demo bill ingestion with official source links.
- Idempotent audit pipeline: each bill is audited once against all active categories.
- User interests and reusable match generation without per-user LLM rework.
- In-app notifications.
- Bill detail pages with reasons/citations and representative-finder links.
- Health and seed/sync endpoints for deployment smoke tests.
- Provider health/status page data for Congress.gov and LegiScan readiness.
- Resilient Congress.gov requests with retry/backoff.
- Bill-version tracking plus queued audit jobs so each text hash is audited once.
- Account creation, API-token auth, role field, user jurisdiction/category interests.
- Notification preferences, digest preview, and saved views.
- Search/filter APIs and UI controls for bill discovery.
- LegiScan state-ingestion foundation that reports missing/ready key status safely.

## Run locally

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
CIVICS_DB=/tmp/civics.db uvicorn civics_app.main:app --host 127.0.0.1 --port 8844
```

Open http://127.0.0.1:8844/

## Test

```bash
pytest -q
```

## Congress.gov ingestion

Live federal ingestion is implemented through `civics_app.congress.CongressGovClient` and the admin endpoint:

```bash
curl -X POST 'http://127.0.0.1:8844/api/admin/sync-congress?limit=25'
```

The Congress.gov API requires a key. On the production host, put it in `/etc/civics.env` as `CONGRESS_API_KEY=...` and restart `civics-api.service`. The scheduled unit `civics-congress-sync.timer` runs the sync periodically; when the key is missing it records a `missing_api_key` ingestion run without failing the service.

## State ingestion

The platform includes a LegiScan provider readiness endpoint and safe sync stub:

```bash
curl -X POST 'http://127.0.0.1:8844/api/admin/sync-legiscan?state=MO&limit=25'
```

Set `LEGISCAN_API_KEY=...` in `/etc/civics.env` before enabling live state ingestion.

## Audit worker

Bill upserts create `bill_versions` and queued `audit_jobs` keyed by bill text hash. Process jobs manually with:

```bash
python -m civics_app.sync_audits --limit 50
```

Production installs `civics-audit-worker.timer` to process queued audit jobs every 10 minutes.
