"""Schema additions shared by the catalog and web processes."""

from __future__ import annotations


WEB_SCHEMA_VERSION = 14

SHARES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shares (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE,
  label TEXT,
  token_sha256 TEXT UNIQUE NOT NULL,
  scope_mode TEXT NOT NULL DEFAULT 'live'
    CHECK(scope_mode IN ('live','frozen')),
  scope_json TEXT NOT NULL DEFAULT '[]',
  expires_at TEXT,
  max_uses INTEGER,
  use_count INTEGER DEFAULT 0,
  revoked_at TEXT,
  created_at TEXT,
  include_stems INTEGER NOT NULL DEFAULT 0
    CHECK(include_stems IN (0,1)),
  allow_downloads INTEGER NOT NULL DEFAULT 0
    CHECK(allow_downloads IN (0,1)),
  landing_collection_id INTEGER REFERENCES collections(id),
  note TEXT
);
"""

WEB_SCHEMA_SQL = (
    """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE,
  display TEXT,
  role TEXT CHECK(role IN ('owner','band')),
  password_hash TEXT,
  created_at TEXT
);
"""
    + SHARES_TABLE_SQL
    + """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  sid_sha256 TEXT UNIQUE,
  share_id INTEGER,
  user_id INTEGER,
  guest_name TEXT,
  created_at TEXT,
  last_seen TEXT
);
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_items (
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  bounce_ulid TEXT NOT NULL,
  position INTEGER NOT NULL,
  PRIMARY KEY(collection_id, bounce_ulid),
  UNIQUE(collection_id, position)
);
CREATE TABLE IF NOT EXISTS reactions (
  id INTEGER PRIMARY KEY,
  bounce_ulid TEXT NOT NULL,
  song_id INTEGER,
  actor TEXT NOT NULL,
  kind TEXT CHECK(kind IN ('heart','chip','verdict','note')),
  dim TEXT,
  value TEXT,
  timecode_s REAL,
  created_at TEXT,
  deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS listen_progress (
  share_id INTEGER,
  bounce_ulid TEXT,
  actor TEXT,
  state TEXT CHECK(state IN ('unheard','heard','skipped')),
  heard_s REAL,
  updated_at TEXT,
  PRIMARY KEY(share_id, bounce_ulid, actor)
);
CREATE TABLE IF NOT EXISTS playback_events (
  id INTEGER PRIMARY KEY,
  share_id INTEGER NOT NULL,
  bounce_ulid TEXT NOT NULL,
  actor TEXT NOT NULL,
  started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
  username TEXT PRIMARY KEY,
  bounce_ulid TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS undo_entries (
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  undone_at TEXT
);
CREATE TABLE IF NOT EXISTS app_alerts (
  id INTEGER PRIMARY KEY,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
  kind TEXT NOT NULL,
  share_id INTEGER,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  acknowledged_at TEXT
);
CREATE TABLE IF NOT EXISTS stem_runs (
  id INTEGER PRIMARY KEY,
  bounce_id INTEGER NOT NULL REFERENCES bounces(id),
  recipe TEXT NOT NULL,
  model_a TEXT NOT NULL,
  model_b TEXT,
  pass_a_done INTEGER NOT NULL DEFAULT 0,
  pass_b_done INTEGER NOT NULL DEFAULT 0,
  src_relpath TEXT NOT NULL,
  src_sha256 TEXT NOT NULL,
  separator_version TEXT NOT NULL,
  started_at TEXT, finished_at TEXT,
  ok INTEGER NOT NULL DEFAULT 0,
  UNIQUE(bounce_id, recipe)
);
CREATE TABLE IF NOT EXISTS stems (
  id INTEGER PRIMARY KEY,
  public_id TEXT UNIQUE NOT NULL,
  run_id INTEGER NOT NULL REFERENCES stem_runs(id),
  bounce_id INTEGER NOT NULL REFERENCES bounces(id),
  kind TEXT NOT NULL
    CHECK(kind IN ('vocals','instrumental','drums','bass','other')),
  archive_relpath TEXT UNIQUE NOT NULL,
  archive_sha256 TEXT NOT NULL,
  mirror_relpath TEXT,
  duration_s REAL, built_at TEXT,
  UNIQUE(run_id, kind)
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('stems')),
  target_id INTEGER NOT NULL,
  payload TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK(state IN ('queued','running','done','failed','cancelled')),
  priority INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  lease_owner TEXT, lease_until TEXT,
  progress TEXT,
  error TEXT,
  requested_by TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  label TEXT,
  role TEXT NOT NULL DEFAULT 'band' CHECK(role IN ('owner','band')),
  token_sha256 TEXT UNIQUE NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  max_uses INTEGER,
  use_count INTEGER NOT NULL DEFAULT 0,
  revoked_at TEXT,
  claimed_by TEXT,
  bounce_ulid TEXT
);

-- Homework: one person puts a track on another person's plate.
-- pending ──listen past the threshold──▶ heard ──an explicit tap──▶ done
--    └────────────────── dismiss ──────────────────────────────▶ dismissed
-- Listening never completes an assignment on its own; a scrub is not a listen.
CREATE TABLE IF NOT EXISTS listen_assignments (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  bounce_ulid TEXT NOT NULL,
  song_id INTEGER REFERENCES songs(id),
  assigned_to TEXT NOT NULL,
  assigned_by TEXT NOT NULL,
  note TEXT,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending','heard','done','dismissed')),
  created_at TEXT NOT NULL,
  heard_at TEXT,
  done_at TEXT
);

CREATE TABLE IF NOT EXISTS api_tokens (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  label TEXT,
  kind TEXT NOT NULL DEFAULT 'upload' CHECK(kind IN ('upload')),
  token_sha256 TEXT UNIQUE NOT NULL,
  username TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT,
  max_uses INTEGER,
  use_count INTEGER NOT NULL DEFAULT 0,
  revoked_at TEXT,
  last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS uploads (
  id INTEGER PRIMARY KEY,
  ulid TEXT UNIQUE NOT NULL,
  filename TEXT NOT NULL,
  relpath TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  uploaded_by TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'browser'
    CHECK(source IN ('browser','watcher')),
  created_at TEXT NOT NULL,
  file_id INTEGER REFERENCES files(id),
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending','ingested','needs_review','rejected')),
  detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_share_user
  ON sessions(share_id, user_id, guest_name);
CREATE INDEX IF NOT EXISTS idx_invites_open
  ON invites(revoked_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_assignments_inbox
  ON listen_assignments(assigned_to, state, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assignments_open_unique
  ON listen_assignments(assigned_to, bounce_ulid)
  WHERE state IN ('pending','heard');
CREATE INDEX IF NOT EXISTS idx_api_tokens_user
  ON api_tokens(username, revoked_at);
CREATE INDEX IF NOT EXISTS idx_uploads_recent
  ON uploads(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_uploads_sha
  ON uploads(sha256);
CREATE INDEX IF NOT EXISTS idx_reactions_bounce_actor
  ON reactions(bounce_ulid, actor, kind, deleted_at);
CREATE INDEX IF NOT EXISTS idx_reactions_created
  ON reactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_progress_share_actor
  ON listen_progress(share_id, actor, updated_at);
CREATE INDEX IF NOT EXISTS idx_playback_actor_track
  ON playback_events(share_id, actor, bounce_ulid, started_at);
CREATE INDEX IF NOT EXISTS idx_undo_session_stack
  ON undo_entries(session_id, undone_at, id DESC);
CREATE INDEX IF NOT EXISTS idx_shares_created
  ON shares(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_collection_items_position
  ON collection_items(collection_id, position);
CREATE INDEX IF NOT EXISTS idx_alerts_open
  ON app_alerts(acknowledged_at, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active
  ON jobs(kind, target_id) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_jobs_claim
  ON jobs(state, priority DESC, id);
CREATE INDEX IF NOT EXISTS idx_stems_bounce ON stems(bounce_id, kind);

-- Digging through a crate is not a database query: you half-remember a word,
-- you spell it wrong, you know it had Henry on it. The trigram tokenizer
-- matches anywhere inside a word, so "ridge" finds "bridges redo" and a typo
-- still lands. It cannot be an external-content table because the text we want
-- to search lives across songs, song_tags and song_aliases, so this table owns
-- its own copy and the triggers below rebuild a song's row whenever any part
-- of it changes.
--
-- Trigram matches nothing shorter than three characters. Queries below that
-- length take a LIKE path instead - see queries.library_songs.
CREATE VIRTUAL TABLE IF NOT EXISTS songs_search USING fts5(
  title,
  slug,
  aliases,
  tags,
  notes,
  tokenize='trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS songs_fts USING fts5(
  title,
  slug,
  content='songs',
  content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS songs_fts_ai AFTER INSERT ON songs BEGIN
  INSERT INTO songs_fts(rowid, title, slug)
  VALUES (new.id, new.title, new.slug);
END;
CREATE TRIGGER IF NOT EXISTS songs_fts_ad AFTER DELETE ON songs BEGIN
  INSERT INTO songs_fts(songs_fts, rowid, title, slug)
  VALUES ('delete', old.id, old.title, old.slug);
END;
CREATE TRIGGER IF NOT EXISTS songs_fts_au AFTER UPDATE OF title, slug ON songs BEGIN
  INSERT INTO songs_fts(songs_fts, rowid, title, slug)
  VALUES ('delete', old.id, old.title, old.slug);
  INSERT INTO songs_fts(rowid, title, slug)
  VALUES (new.id, new.title, new.slug);
END;
"""
)
