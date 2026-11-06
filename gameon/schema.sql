-- Version 1. Additive and repeatable; never drops existing records.
-- ISO-8601 timestamps are TEXT to preserve the existing SQLite/API contract.
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY, title TEXT NOT NULL,
    min_players INTEGER NOT NULL CHECK (min_players BETWEEN 2 AND 12),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slots (
    id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    starts_at TEXT NOT NULL, ends_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS players (
    id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    name TEXT NOT NULL, suburb TEXT NOT NULL, token TEXT NOT NULL,
    joined_order BIGINT GENERATED ALWAYS AS IDENTITY
);
CREATE TABLE IF NOT EXISTS votes (
    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
    PRIMARY KEY (player_id, slot_id)
);
CREATE TABLE IF NOT EXISTS vote_submissions (
    player_id TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    submitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_slots_game ON slots(game_id);
CREATE INDEX IF NOT EXISTS idx_players_game ON players(game_id);
CREATE TABLE IF NOT EXISTS rate_limits (
    key TEXT PRIMARY KEY, hits INTEGER NOT NULL, expires_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_expiry ON rate_limits(expires_at);

-- V0.4.1: historical applications created 90-minute slots. Do not rewrite slots/votes.
ALTER TABLE games ADD COLUMN IF NOT EXISTS duration_minutes INTEGER NOT NULL DEFAULT 90
    CHECK (duration_minutes IN (60,90,120));
