/* Expand-only: legacy Codex rows and quota ledger remain unchanged. */
CREATE TABLE IF NOT EXISTS community_provider_scores (
  github_id INTEGER NOT NULL REFERENCES github_users(github_id) ON DELETE CASCADE,
  provider TEXT NOT NULL CHECK (provider IN ('claude', 'all')),
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
  PRIMARY KEY (github_id, scope, provider)
);

CREATE INDEX IF NOT EXISTS community_provider_scores_board_idx
  ON community_provider_scores(provider, scope, agent_hours_per_day DESC, login ASC);
CREATE INDEX IF NOT EXISTS community_provider_scores_window_idx
  ON community_provider_scores(provider, scope, eligible_until);

