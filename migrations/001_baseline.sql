-- Historical baseline. New installations are created by civics_app.main.init_db;
-- this marker makes schema state explicit and auditable.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
