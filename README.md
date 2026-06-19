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
