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
