CREATE TABLE IF NOT EXISTS github_users (
  github_id INTEGER PRIMARY KEY,
  login TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  github_id INTEGER NOT NULL REFERENCES github_users(github_id) ON DELETE CASCADE,
  csrf_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  revoked_at INTEGER
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(github_id);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS oauth_states (
  state_hash TEXT PRIMARY KEY,
  verifier_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER
);

CREATE INDEX IF NOT EXISTS oauth_states_expiry_idx ON oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS community_scores (
  github_id INTEGER NOT NULL REFERENCES github_users(github_id) ON DELETE CASCADE,
  scope TEXT NOT NULL CHECK (scope IN ('interactive-only', 'including-exec')),
  login TEXT NOT NULL,
  score_json TEXT NOT NULL,
  score_digest TEXT NOT NULL,
  window_end TEXT NOT NULL,
  window_timezone TEXT NOT NULL,
  eligible_until INTEGER NOT NULL,
  agent_hours_per_day REAL NOT NULL,
  updated_at TEXT NOT NULL,
  request_id TEXT NOT NULL UNIQUE,
  PRIMARY KEY (github_id, scope)
);

CREATE INDEX IF NOT EXISTS community_scores_board_idx
  ON community_scores(scope, agent_hours_per_day DESC, login ASC);
CREATE INDEX IF NOT EXISTS community_scores_window_idx
  ON community_scores(scope, eligible_until);

CREATE TABLE IF NOT EXISTS quota_events (
  request_id TEXT PRIMARY KEY,
  github_id INTEGER NOT NULL REFERENCES github_users(github_id) ON DELETE CASCADE,
  scope TEXT NOT NULL CHECK (scope IN ('interactive-only', 'including-exec')),
  score_digest TEXT NOT NULL,
  accepted_at INTEGER NOT NULL CHECK (accepted_at >= 0)
);

CREATE INDEX IF NOT EXISTS quota_events_user_time_idx
  ON quota_events(github_id, accepted_at);

CREATE TRIGGER IF NOT EXISTS quota_cap_before_insert
BEFORE INSERT ON quota_events
WHEN (
  SELECT COUNT(*)
  FROM quota_events
  WHERE github_id = NEW.github_id
    AND accepted_at > NEW.accepted_at - 2592000000
) >= 5
BEGIN
  SELECT RAISE(ABORT, 'quota_exceeded');
END;

CREATE TABLE IF NOT EXISTS rate_buckets (
  bucket_hash TEXT PRIMARY KEY,
  bucket_start INTEGER NOT NULL,
  attempts INTEGER NOT NULL CHECK (attempts >= 0 AND attempts <= 10)
);

CREATE INDEX IF NOT EXISTS rate_buckets_expiry_idx ON rate_buckets(bucket_start);
