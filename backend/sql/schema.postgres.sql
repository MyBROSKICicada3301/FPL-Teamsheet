-- Transfer Room — Postgres 16 schema.
-- Follows BACKEND.md §3. Nothing is overwritten; every observation carries the
-- date we learned it, which is what makes a leak-free training set possible.

CREATE TABLE IF NOT EXISTS club (
    club_id        SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    country        TEXT NOT NULL,
    league_tier    SMALLINT,
    source_ids     JSONB
);

CREATE TABLE IF NOT EXISTS player (
    player_id      SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    birth_date     DATE,
    primary_pos    TEXT,          -- GK/DF/MF/FW
    nationality    TEXT,
    source_ids     JSONB
);

CREATE TABLE IF NOT EXISTS transfer_window (
    window_id      SERIAL PRIMARY KEY,
    season         TEXT NOT NULL,     -- '2025-26'
    window_type    TEXT NOT NULL,     -- 'summer' | 'winter'
    opens_on       DATE NOT NULL,
    closes_on      DATE NOT NULL,
    UNIQUE (season, window_type)
);

CREATE TABLE IF NOT EXISTS transfer (
    transfer_id    SERIAL PRIMARY KEY,
    player_id      INT REFERENCES player,
    from_club_id   INT REFERENCES club,
    to_club_id     INT REFERENCES club,
    window_id      INT REFERENCES transfer_window,
    announced_on   DATE NOT NULL,
    fee_eur        BIGINT,            -- NULL = undisclosed, 0 = free
    transfer_type  TEXT NOT NULL,     -- permanent|loan|loan_with_option|free|end_of_contract
    source         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contract (
    player_id      INT REFERENCES player,
    club_id        INT REFERENCES club,
    starts_on      DATE,
    expires_on     DATE,
    observed_on    DATE NOT NULL,     -- when WE learned this. Critical.
    PRIMARY KEY (player_id, club_id, observed_on)
);

CREATE TABLE IF NOT EXISTS player_season_stats (
    player_id      INT REFERENCES player,
    club_id        INT REFERENCES club,
    season         TEXT,
    minutes        INT,
    apps           INT,
    starts         INT,
    goals          INT,
    assists        INT,
    xg             NUMERIC(6,2),
    xag            NUMERIC(6,2),
    prog_carries   INT,
    observed_on    DATE NOT NULL,
    PRIMARY KEY (player_id, season, club_id, observed_on)
);

CREATE TABLE IF NOT EXISTS rumour (
    rumour_id      SERIAL PRIMARY KEY,
    player_id      INT REFERENCES player,
    to_club_id     INT REFERENCES club,
    published_at   TIMESTAMPTZ NOT NULL,
    source_name    TEXT NOT NULL,
    source_tier    SMALLINT NOT NULL,  -- 1 = most reliable
    headline       TEXT,
    url            TEXT
);

-- BACKEND.md §4's training view reads squad_membership but §3 does not define
-- it. It is the spine of "who was at the club when the window opened", so it
-- needs the same valid-from/valid-to treatment as everything else.
CREATE TABLE IF NOT EXISTS squad_membership (
    player_id      INT REFERENCES player,
    club_id        INT REFERENCES club,
    valid_from     DATE NOT NULL,
    valid_to       DATE NOT NULL,     -- exclusive; '9999-12-31' for current
    shirt_number   SMALLINT,
    PRIMARY KEY (player_id, club_id, valid_from)
);

-- Club finance, for PSR headroom. Observed like everything else.
CREATE TABLE IF NOT EXISTS club_finance (
    club_id            INT REFERENCES club,
    season             TEXT NOT NULL,
    psr_headroom_eur   BIGINT,
    squad_cost_eur     BIGINT,
    manager_changed_on DATE,
    relegated          BOOLEAN DEFAULT FALSE,
    qualified_ucl      BOOLEAN DEFAULT FALSE,
    observed_on        DATE NOT NULL,
    PRIMARY KEY (club_id, season, observed_on)
);

-- Versioned features, so any prediction can be reproduced later (§5).
CREATE TABLE IF NOT EXISTS player_window_features (
    player_id       INT REFERENCES player,
    window_id       INT REFERENCES transfer_window,
    feature_version TEXT NOT NULL,
    club_id         INT REFERENCES club,
    features        JSONB NOT NULL,
    label_moved     BOOLEAN,           -- NULL for the live window
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, window_id, feature_version)
);

-- Model output. Append-only; never UPDATE a prediction.
CREATE TABLE IF NOT EXISTS prediction (
    prediction_id  BIGSERIAL PRIMARY KEY,
    player_id      INT REFERENCES player,
    window_id      INT REFERENCES transfer_window,
    p_exit         NUMERIC(5,4) NOT NULL,
    predicted_fee_eur BIGINT,
    factors        JSONB NOT NULL,     -- [{"label":"...","contribution":0.21}]
    model_version  TEXT NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Evaluation history, so /method serves measured numbers rather than claims.
CREATE TABLE IF NOT EXISTS model_run (
    model_version  TEXT PRIMARY KEY,
    trained_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    algorithm      TEXT NOT NULL,
    params         JSONB NOT NULL,
    train_through  TEXT,
    valid_season   TEXT,
    test_season    TEXT,
    metrics        JSONB NOT NULL      -- brier, precision@20, baseline, curve
);

CREATE INDEX IF NOT EXISTS prediction_window_p_idx ON prediction (window_id, p_exit DESC);
CREATE INDEX IF NOT EXISTS prediction_player_time_idx ON prediction (player_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS rumour_player_time_idx ON rumour (player_id, published_at DESC);
CREATE INDEX IF NOT EXISTS squad_membership_club_idx ON squad_membership (club_id, valid_from);

-- One row per player-window: the labelled training spine (§4).
-- Loans are excluded from the positive class; frees and expiries are positive.
CREATE MATERIALIZED VIEW IF NOT EXISTS training_row AS
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
JOIN player p USING (player_id)
JOIN transfer_window w
  ON s.valid_from <= w.opens_on AND s.valid_to > w.opens_on;
