-- Applied idempotently by civics_app.db.init_db. This file documents the
-- production-overhaul additions; compatibility ALTERs are performed in Python.
CREATE TABLE IF NOT EXISTS bill_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bill_id INTEGER NOT NULL REFERENCES bills(id),
  action_date TEXT NOT NULL,
  description TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  UNIQUE(bill_id, action_date, description)
);
CREATE TABLE IF NOT EXISTS audit_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  audit_flag_id INTEGER NOT NULL REFERENCES audit_flags(id),
  feedback_type TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(user_id, audit_flag_id, feedback_type)
);
CREATE INDEX IF NOT EXISTS idx_notifications_delivery ON notifications(status, channel, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_jobs_status ON audit_jobs(status, created_at);
