-- Additive only. Run explicitly in the verified game-on-dev application schema.
-- Never touch neon_auth or delete legacy capability records.
CREATE TABLE IF NOT EXISTS account_profiles (
    id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
    name TEXT NOT NULL, suburb TEXT NOT NULL,
    avatar_id TEXT NOT NULL DEFAULT 'auto', favourites TEXT NOT NULL DEFAULT '[]',
    UNIQUE(issuer,subject)
);
CREATE TABLE IF NOT EXISTS account_memberships (
    account_id TEXT NOT NULL REFERENCES account_profiles(id),
    membership_id TEXT NOT NULL UNIQUE REFERENCES memberships(id),
    group_id TEXT NOT NULL REFERENCES groups(id),
    PRIMARY KEY(account_id,group_id)
);
