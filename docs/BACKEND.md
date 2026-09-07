# Transfer Room — Backend, Data & API

Everything behind the single page: where data comes from, how it becomes a
labelled training set, how the model is trained and evaluated, and what the
frontend calls.

---

## 1. Shape of the system

Predictions do not need to be computed on request. A transfer probability
changes on the order of hours, not milliseconds. So the system is a batch
pipeline that writes into a table, plus a thin read API in front of it.

```
  Sources                Pipeline                    Serving
  ───────                ────────                    ───────
  Stats feed  ─┐
  Transfers   ─┤                                      ┌─ GET /players
  Contracts   ─┼─→ ingest ─→ Postgres ─→ features ─┐  │
  News/rumour ─┤   (daily)      raw       (daily)  │  │
  Club finance─┘                                   ▼  │
                                                 train │
                                              (weekly) │
                                                   │   │
                                                   ▼   │
                                              predictions ─→ FastAPI ─→ Redis ─→ CDN
                                                 table                  60s
```

Three schedules, deliberately different:

| Job | Frequency | Why |
|---|---|---|
| Ingest stats & transfers | Daily 04:00 UTC | Sources update overnight |
| Ingest rumours | Every 30 min in-window | The only genuinely fast-moving input |
| Recompute features + predict | Daily, hourly in-window | Cheap; just a model forward pass |
| Retrain | Weekly, off-window | Retraining daily overfits to news noise |

---

## 2. Data sources

The single biggest determinant of whether this works. Ordered by how much they
matter.

### Transfer history — your labels

| Source | Cost | Notes |
|---|---|---|
| Wikipedia season transfer lists | Free | Freely licensed (CC BY-SA), surprisingly complete for English football. Correct starting point. |
| Sportmonks | ~€40–130/mo | Transfers, squads, and player images from one token. No sales call to start. |
| SportsDataIO | Quote | Free trial only covers Champions League; Premier League needs a paid plan. |
| Enetpulse | Quote | Transfers plus a separate rumour feed sourced from media outlets. |
| Transfermarkt | — | The canonical dataset, and **not usable**. Their terms prohibit scraping and commercial reuse; most of their data is licensed in from third parties. Fine for eyeballing, not for your database. |

### Player performance

FBref via the `soccerdata` Python package is the best free option — xG,
progressive carries, minutes, the lot. Two constraints that are not optional:

- **Rate limit.** Sports Reference blocks more than ten requests per minute to
  FBref and jails the session for up to a day. Sleep 7 seconds between
  requests, with jitter.
- **Terms.** FBref permits scraping for personal and educational use. It is
  not permitted for competing analytics services or public hosting. If your
  app goes public, this data has to come from a licensed feed instead.

StatsBomb open data is free and event-level but covers limited competitions.
Understat gives free xG for the big five leagues.

### Contract expiry dates

The single most predictive feature and the hardest to get free. No reliable
free source. Budget for a paid feed, or accept a materially worse model.

### Rumours

A weighted count of credible journalist mentions will outperform your
performance features. Options: Enetpulse's rumour feed, a general news API
filtered to football sources, or a curated list of reporters with a
reliability weight per source. Weighting matters more than volume — one tier-1
reporter is worth fifty aggregator posts.

---

## 3. Data model

Postgres. Every table is bitemporal in the sense that matters: nothing is
overwritten, everything is stamped with when it was true. Without this you
cannot build a leak-free training set later.

```sql
CREATE TABLE club (
    club_id        SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    country        TEXT NOT NULL,
    league_tier    SMALLINT,
    source_ids     JSONB   -- {"fbref":"18bb7c10","sportmonks":"14"}
);

CREATE TABLE player (
    player_id      SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    birth_date     DATE,
    primary_pos    TEXT,          -- GK/DF/MF/FW
    nationality    TEXT,
    source_ids     JSONB
);

-- One row per transfer window per player. This is the spine.
CREATE TABLE transfer_window (
    window_id      SERIAL PRIMARY KEY,
    season         TEXT NOT NULL,     -- '2025-26'
    window_type    TEXT NOT NULL,     -- 'summer' | 'winter'
    opens_on       DATE NOT NULL,
    closes_on      DATE NOT NULL,
    UNIQUE (season, window_type)
);

CREATE TABLE transfer (
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

CREATE TABLE contract (
    player_id      INT REFERENCES player,
    club_id        INT REFERENCES club,
    starts_on      DATE,
    expires_on     DATE,
    observed_on    DATE NOT NULL,     -- when WE learned this. Critical.
    PRIMARY KEY (player_id, club_id, observed_on)
);

CREATE TABLE player_season_stats (
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

CREATE TABLE rumour (
    rumour_id      SERIAL PRIMARY KEY,
    player_id      INT REFERENCES player,
    to_club_id     INT REFERENCES club,
    published_at   TIMESTAMPTZ NOT NULL,
    source_name    TEXT NOT NULL,
    source_tier    SMALLINT NOT NULL,  -- 1 = most reliable
    headline       TEXT,
    url            TEXT
);

-- Model output. Append-only; never UPDATE a prediction.
CREATE TABLE prediction (
    prediction_id  BIGSERIAL PRIMARY KEY,
    player_id      INT REFERENCES player,
    window_id      INT REFERENCES transfer_window,
    p_exit         NUMERIC(5,4) NOT NULL,
    predicted_fee_eur BIGINT,
    factors        JSONB NOT NULL,     -- [{"label":"...","contribution":0.21}]
    model_version  TEXT NOT NULL,
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON prediction (window_id, p_exit DESC);
CREATE INDEX ON prediction (player_id, computed_at DESC);
CREATE INDEX ON rumour (player_id, published_at DESC);
```

**Identity resolution** is the unglamorous problem that will eat a week. The
same player is `Bruno Fernandes` in one feed and `Bruno Miguel Borges
Fernandes` in another. Match on normalised name plus birth date, keep a manual
override table for the rest, and log every unmatched record rather than
silently dropping it.

---

## 4. Building the labelled dataset

One row per **player-window**. This is where most projects stall, so it gets
its own section.

```sql
-- Every PL squad member at the moment each window opened
CREATE MATERIALIZED VIEW training_row AS
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
```

Decisions you have to make explicitly, because the data will not make them for
you:

- **Loans.** A loan is not a transfer for most purposes. Exclude from the
  positive class, but keep as a feature — a player loaned out last season is
  far more likely to leave permanently.
- **Free agents / expiring contracts.** A player leaving on a free is still
  leaving. Include as positive.
- **Mid-window moves.** Label on the window, not the date.
- **Retirements and released players.** Separate class, or exclude. Do not let
  them contaminate "moved".
- **Everything is computed as of `opens_on`.** No feature may use data with
  `observed_on > w.opens_on`. This is the single rule that keeps the model
  honest, and it is why `observed_on` exists on every source table.

Expect roughly 500–600 rows per window across the Premier League, so around
1,200/year. Ten seasons of the big five leagues gets you to a usable 30–50k
rows with a positive rate near 15–20%.

---

## 5. Features

Ranked by expected contribution:

| Feature | Type | Notes |
|---|---|---|
| `contract_months_remaining` | int | Strongest single signal. Clubs sell at 12–18 months to avoid a free exit. |
| `minutes_share_delta` | float | Share of available minutes vs. prior season. Falling minutes → exit. |
| `age_vs_positional_peak` | float | Age minus peak sale age for the position (~26 outfield, ~29 GK). |
| `manager_changed_180d` | bool | New manager clears out the previous squad. |
| `club_psr_headroom_eur` | int | Profit & Sustainability pressure drives a lot of modern English sales. |
| `relegated` / `qualified_ucl` | bool | Relegation triggers mass exits. |
| `market_value_trend_2y` | float | Direction matters more than level. |
| `positional_depth_rank` | int | 1st choice vs. 4th choice at the position. |
| `rumour_score_30d` | float | `Σ 1/source_tier` over 30 days. Weight tier 1 heavily. |
| `league_pathway_prior` | float | Historical rate of moves along this league pair. |
| `was_loaned_last_season` | bool | |
| `agent_churn_rate` | float | Some agents move clients on a cycle. |

Store features as a versioned table keyed on `(player_id, window_id,
feature_version)` so a prediction can always be reproduced.

---

## 6. Model

Gradient boosting on tabular data of this size, not deep learning. LightGBM
also gives per-prediction SHAP values, which the frontend needs for the "Why
68%?" section.

```python
import lightgbm as lgb

params = dict(
    objective="binary",
    metric="binary_logloss",
    learning_rate=0.03,
    num_leaves=31,
    min_data_in_leaf=40,      # small dataset; guard against tiny leaves
    feature_fraction=0.8,
    bagging_fraction=0.8,
    bagging_freq=1,
    lambda_l2=1.0,
    is_unbalance=True,
)

model = lgb.train(
    params, train_set,
    valid_sets=[valid_set],
    num_boost_round=2000,
    callbacks=[lgb.early_stopping(100)],
)
```

**Calibrate afterwards.** Raw boosted probabilities are not well calibrated,
and the whole product is a probability. Fit isotonic regression on the
validation fold:

```python
from sklearn.isotonic import IsotonicRegression
calib = IsotonicRegression(out_of_bounds="clip").fit(valid_raw, valid_y)
p_exit = calib.transform(model.predict(X_test))
```

### Evaluation — the part that decides if this is real

About 15–20% of players move in a window, so predicting "nobody moves" scores
~82% accuracy. **Accuracy is meaningless here.** Do not put it on the page.

- **Temporal splits only.** Train through 2023, validate 2024, test 2025.
  A random split leaks the future and produces a model that looks brilliant
  and performs terribly.
- **Precision@20** — of your top 20 predicted exits, how many happened? This
  is the number the product actually lives or dies on.
- **Brier score** and a **calibration curve** — if you say 30%, it should
  happen about 30% of the time.
- **Beat the baseline.** `contract_months_remaining < 12 → moved` is a strong
  naive rule. Report your lift over it. If you can't beat it, the extra
  features aren't earning their complexity.

Log every run — features, params, metrics, seed — to MLflow or a plain
Postgres table. `model_version` on each prediction points back to it.

---

## 7. API

FastAPI. Read-only, cached, no auth for public endpoints.

```
GET  /api/v1/board?window=current&limit=25
GET  /api/v1/players/{player_id}
GET  /api/v1/clubs/{club_id}/movements
GET  /api/v1/method/calibration
GET  /healthz
```

### `GET /api/v1/board`

Powers the horizontally scrolling section.

```json
{
  "window": { "season": "2026-27", "type": "winter", "closes_on": "2027-02-01" },
  "computed_at": "2026-09-07T09:14:22Z",
  "model_version": "lgbm-2026.08.3",
  "players": [
    {
      "player_id": 4412,
      "name": "Alexander Isak",
      "club": { "id": 34, "name": "Newcastle United" },
      "position": "FW",
      "p_exit": 0.68,
      "band": "hot",
      "image": {
        "cutout": "https://cdn.example.com/p/4412/cutout.avif",
        "square": "https://cdn.example.com/p/4412/400.avif"
      }
    }
  ]
}
```

`band` is computed server-side (`cold` <0.2, `warm` <0.5, `hot` <0.85,
`done`) so the colour ramp thresholds live in one place, not in both codebases.

### `GET /api/v1/players/{id}`

Adds the factor breakdown for the explanation section.

```json
{
  "player_id": 4412,
  "name": "Alexander Isak",
  "p_exit": 0.68,
  "predicted_fee_eur": 82000000,
  "factors": [
    { "label": "Contract runs out in 11 months", "contribution":  0.21 },
    { "label": "Playing 40% fewer minutes",       "contribution":  0.14 },
    { "label": "Club needs to sell by June 30",   "contribution":  0.09 },
    { "label": "Age 26 — peak resale value",      "contribution":  0.04 },
    { "label": "No credible bids reported",       "contribution": -0.06 }
  ],
  "recent_rumours": [
    { "source": "The Athletic", "tier": 1, "published_at": "2026-09-05T18:02:00Z" }
  ]
}
```

Translate SHAP values into these plain-language labels **server-side**, in a
mapping table keyed on feature name. The frontend should never see a raw
feature name or a SHAP number. Cap at five factors and round contributions to
two decimals.

**Round `p_exit` to two decimals in the response.** A model trained on a few
thousand transfers cannot support `0.6783`, and showing it invites a precision
the model doesn't have.

### Caching and rate limits

```python
@app.get("/api/v1/board")
@cache(expire=60)                      # fastapi-cache2 + Redis
async def board(window: str = "current", limit: int = Query(25, le=100)):
    ...
```

`Cache-Control: public, max-age=60, stale-while-revalidate=300` and put
Cloudflare in front. Traffic to something like this is extremely spiky —
deadline day is 100× a normal Tuesday — and the CDN is what absorbs it rather
than your database. Rate limit at 60 req/min per IP via slowapi.

---

## 8. Stack and deployment

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Where the football data ecosystem lives |
| API | FastAPI + Uvicorn | Async, typed, auto OpenAPI docs |
| DB | Postgres 16 (Neon or Supabase) | Managed; you have no reason to run this yourself |
| Cache | Redis (Upstash) | |
| Scheduler | Prefect Cloud, or GitHub Actions cron | Actions cron is genuinely fine at this scale |
| ML | LightGBM, scikit-learn, SHAP | |
| Tracking | MLflow, or a Postgres table | |
| Images | Cloudflare R2 + Images | No egress fees; resizing built in |
| Host | Railway or Fly.io | |
| CDN | Cloudflare | The spike absorber |

Rough monthly cost at low traffic: Postgres $0–25, Redis $0–10, host $5–20,
R2 ~$5, data feed €40–130. Call it **€60–190/month**, dominated by the data
licence.

### Build order

| Week | Deliverable |
|---|---|
| 1–2 | Ingest one league, five seasons, into `player_season_stats` + `transfer`. Just get a clean table. |
| 3 | Build `training_row`. Handle loans, frees, mid-window moves. This is the fiddly week. |
| 4 | LightGBM baseline, temporal split, precision@20. Expect mediocre. That's normal. |
| 5 | Add contract data and rumour features. This is where the lift comes from. |
| 6 | FastAPI + Redis + the four endpoints. |
| 7+ | Frontend, once there's something worth showing. |

---

## 9. Operational rules

- **Never `UPDATE` a prediction.** Insert a new row. You need the history to
  audit calibration later and to show "we said 40% three weeks ago".
- **Never overwrite an observation.** New `observed_on`, new row. Overwriting
  destroys your ability to build a leak-free training set.
- **Log every unmatched entity.** Silent drops in identity resolution are how
  a squad quietly loses eleven players.
- **Alert on staleness, not errors.** The failure mode that hurts is a
  pipeline that succeeded but ingested nothing. Alert if `max(computed_at)` is
  older than 6 hours during a window.
- **Freeze the model during deadline week.** Retraining on a spike of
  deadline-day noise produces a model that thinks everyone is leaving.

---

## 10. Legal

Three things to settle before this is public, not after:

1. **Data licensing.** Transfermarkt's terms rule out commercial reuse.
   FBref's rule out public hosting and competing analytics services. If this
   becomes a real product, licensing a feed is not optional — it's the thing
   that makes it legal.
2. **Images.** Press photography of players is agency-owned and not free to
   use. Covered in the design document; the practical answer is a feed that
   bundles cleared headshots, or commissioned illustration.
3. **Gambling regulation.** Presenting calibrated probabilities as analysis
   and entertainment is fine. Positioning them as wagering advice pulls you
   into gambling regulation in the UK and EU, which is not a light-touch
   regime. Keep the footer disclaimer, and don't accept affiliate deals from
   bookmakers without taking advice first.

I'm not a lawyer and this isn't legal advice — for anything commercial, get a
proper opinion on the data licences specifically.
