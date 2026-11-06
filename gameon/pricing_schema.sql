-- Additive table in the existing application schema; no provider/session secrets.
CREATE TABLE IF NOT EXISTS court_prices (
    observation_id TEXT PRIMARY KEY,
    venue_id TEXT NOT NULL, category TEXT NOT NULL, court_name TEXT NOT NULL,
    starts_at TEXT NOT NULL, ends_at TEXT NOT NULL, duration_minutes INTEGER NOT NULL,
    provider_players INTEGER NOT NULL, total_minor INTEGER NOT NULL,
    currency TEXT, fees_minor INTEGER, tax_minor INTEGER,
    observed_at TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT NOT NULL,
    refresh_status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS court_prices_match ON court_prices(venue_id, starts_at, ends_at, provider_players);
