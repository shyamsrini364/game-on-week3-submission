-- Additive V0.4 schema; shared by SQLite and PostgreSQL.
CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    invite_hash TEXT, invite_expires BIGINT NOT NULL,
    favourites TEXT NOT NULL DEFAULT '[]', restrict_venues INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    name TEXT NOT NULL, suburb TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner','member')),
    token_hash TEXT NOT NULL UNIQUE, favourites TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_memberships_group ON memberships(group_id);
-- Additive V2 preference; old memberships retain their generated avatar.
CREATE TABLE IF NOT EXISTS membership_avatars (
    membership_id TEXT PRIMARY KEY REFERENCES memberships(id) ON DELETE CASCADE,
    avatar_id TEXT NOT NULL CHECK(avatar_id IN ('rally','ace','sunny','river','sky','blaze'))
);
CREATE TABLE IF NOT EXISTS group_games (
    game_id TEXT PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_group_games_group ON group_games(group_id);
CREATE TABLE IF NOT EXISTS member_players (
    membership_id TEXT NOT NULL REFERENCES memberships(id) ON DELETE CASCADE,
    game_id TEXT NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    player_id TEXT NOT NULL UNIQUE REFERENCES players(id) ON DELETE CASCADE,
    PRIMARY KEY(membership_id, game_id)
);
