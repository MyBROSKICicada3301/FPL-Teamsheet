-- Transfer Room — SQLite translation of schema.postgres.sql, for local dev.
-- Same tables, same column names, same semantics. Types are narrowed to what
-- SQLite has: SERIAL -> INTEGER PRIMARY KEY, JSONB/TIMESTAMPTZ/DATE -> TEXT,
-- BOOLEAN -> INTEGER. Dates are stored ISO-8601 so string ordering is date
-- ordering, which is what every query below relies on.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS club (
    club_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    country        TEXT NOT NULL,
    league_tier    INTEGER,
    source_ids     TEXT
);

CREATE TABLE IF NOT EXISTS player (
    player_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    birth_date     TEXT,
    primary_pos    TEXT,
    nationality    TEXT,
    source_ids     TEXT
);

CREATE TABLE IF NOT EXISTS transfer_window (
    window_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    season         TEXT NOT NULL,
    window_type    TEXT NOT NULL,
    opens_on       TEXT NOT NULL,
    closes_on      TEXT NOT NULL,
    UNIQUE (season, window_type)
);

CREATE TABLE IF NOT EXISTS transfer (
    transfer_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id      INTEGER REFERENCES player,
    from_club_id   INTEGER REFERENCES club,
    to_club_id     INTEGER REFERENCES club,
    window_id      INTEGER REFERENCES transfer_window,
    announced_on   TEXT NOT NULL,
    fee_eur        INTEGER,
    transfer_type  TEXT NOT NULL,
    source         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract (
    player_id      INTEGER REFERENCES player,
    club_id        INTEGER REFERENCES club,
    starts_on      TEXT,
    expires_on     TEXT,
    observed_on    TEXT NOT NULL,
    PRIMARY KEY (player_id, club_id, observed_on)
);

CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id      INTEGER REFERENCES player,
    club_id        INTEGER REFERENCES club,
    season         TEXT,
    minutes        INTEGER,
    apps           INTEGER,
    starts         INTEGER,
    goals          INTEGER,
    assists        INTEGER,
    xg             REAL,
    xag            REAL,
    prog_carries   INTEGER,
    observed_on    TEXT NOT NULL,
    PRIMARY KEY (player_id, season, club_id, observed_on)
);

CREATE TABLE IF NOT EXISTS rumour (
    rumour_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id      INTEGER REFERENCES player,
    to_club_id     INTEGER REFERENCES club,
    published_at   TEXT NOT NULL,
    source_name    TEXT NOT NULL,
    source_tier    INTEGER NOT NULL,
    headline       TEXT,
    url            TEXT
);

CREATE TABLE IF NOT EXISTS squad_membership (
    player_id      INTEGER REFERENCES player,
    club_id        INTEGER REFERENCES club,
    valid_from     TEXT NOT NULL,
    valid_to       TEXT NOT NULL,
    shirt_number   INTEGER,
    PRIMARY KEY (player_id, club_id, valid_from)
);

CREATE TABLE IF NOT EXISTS club_finance (
    club_id            INTEGER REFERENCES club,
    season             TEXT NOT NULL,
    psr_headroom_eur   INTEGER,
    squad_cost_eur     INTEGER,
    manager_changed_on TEXT,
    relegated          INTEGER DEFAULT 0,
    qualified_ucl      INTEGER DEFAULT 0,
    observed_on        TEXT NOT NULL,
    PRIMARY KEY (club_id, season, observed_on)
);

CREATE TABLE IF NOT EXISTS player_window_features (
    player_id       INTEGER REFERENCES player,
    window_id       INTEGER REFERENCES transfer_window,
    feature_version TEXT NOT NULL,
    club_id         INTEGER REFERENCES club,
    features        TEXT NOT NULL,
    label_moved     INTEGER,
    computed_at     TEXT NOT NULL,
    PRIMARY KEY (player_id, window_id, feature_version)
);

CREATE TABLE IF NOT EXISTS prediction (
    prediction_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id      INTEGER REFERENCES player,
    window_id      INTEGER REFERENCES transfer_window,
    p_exit         REAL NOT NULL,
    predicted_fee_eur INTEGER,
    factors        TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    computed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_run (
    model_version  TEXT PRIMARY KEY,
    trained_at     TEXT NOT NULL,
    algorithm      TEXT NOT NULL,
    params         TEXT NOT NULL,
    train_through  TEXT,
    valid_season   TEXT,
    test_season    TEXT,
    metrics        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS prediction_window_p_idx ON prediction (window_id, p_exit DESC);
CREATE INDEX IF NOT EXISTS prediction_player_time_idx ON prediction (player_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS rumour_player_time_idx ON rumour (player_id, published_at DESC);
CREATE INDEX IF NOT EXISTS squad_membership_club_idx ON squad_membership (club_id, valid_from);

-- SQLite has no materialized views; a plain view is equivalent here because
-- the dev dataset is small enough that the join costs nothing.
CREATE VIEW IF NOT EXISTS training_row AS
SELECT
    p.player_id,
    w.window_id,
    w.opens_on AS as_of,
    s.club_id,
    EXISTS (
        SELECT 1 FROM transfer t
        WHERE t.player_id = p.player_id
          AND t.window_id = w.window_id
          AND t.from_club_id = s.club_id
          AND t.transfer_type IN ('permanent','free','end_of_contract')
    ) AS moved
FROM squad_membership s
JOIN player p ON p.player_id = s.player_id
JOIN transfer_window w
  ON s.valid_from <= w.opens_on AND s.valid_to > w.opens_on;
