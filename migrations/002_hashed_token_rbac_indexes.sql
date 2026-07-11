-- Applied idempotently by init_db for compatibility with existing SQLite files.
ALTER TABLE users ADD COLUMN api_token_hash TEXT;
ALTER TABLE users ADD COLUMN api_token_prefix TEXT;
ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_token_prefix ON users(api_token_prefix) WHERE api_token_prefix IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_matches_user ON bill_user_matches(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_interests_user ON user_interests(user_id, active);
