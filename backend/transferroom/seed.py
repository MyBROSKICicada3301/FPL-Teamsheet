"""Generate a synthetic but structurally honest dataset.

Why synthetic. BACKEND.md §2 and §10 are clear that the datasets that would
make this real — Transfermarkt, FBref for public hosting, contract expiry dates
— are either licensed or off-limits, and DESIGN.md §4 says the same about
photography. So the development dataset is fabricated: fictional clubs,
fictional players, no claims attached to any real person. Everything about the
*shape* of it is real — the tables, the observation dates, the leak rules, the
class balance — so the pipeline that runs on it is the pipeline that would run
on a licensed feed.

The simulation gives each player-window a latent propensity built from the same
quantities the feature builder later rediscovers from the raw tables. That is
what makes the trained model score better than chance instead of learning noise,
and it means precision@20 and the calibration curve on the method section are
measured numbers rather than decoration.
"""

import datetime as dt
import math
import random

from . import config, db

SEED = 20260907

# Seasons we hold statistics for. Windows start two seasons later so every
# player-window has a prior season and a season before that to difference.
SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2016, 2027)]
WINDOW_SEASONS = SEASONS[2:]

SQUAD_SIZE = 25
POSITIONS = ["GK"] * 3 + ["DF"] * 8 + ["MF"] * 8 + ["FW"] * 6
PEAK_AGE = {"GK": 29.0, "DF": 27.0, "MF": 26.5, "FW": 26.0}

CLUB_NAMES = [
    "Manchester United", "Chelsea", "Arsenal", "Manchester City",
]

# Share of departures that go outside these four clubs. Those transfers are
# recorded with a NULL destination rather than inventing a club to hold them:
# the label is "left this club", which does not depend on where they went. It
# also keeps the population stable — without it every departure would land back
# in the same four squads and they would inflate season on season.
ABROAD_RATE = 0.55

# Dummy names. Crossing the two lists gives 100 combinations, which covers the
# four opening squads exactly; past that names repeat.
FIRST_NAMES = [
    "James", "Robert", "William", "David", "Thomas",
    "Daniel", "Matthew", "Anthony", "Mark", "Paul",
]

LAST_NAMES = [
    "Miller", "Davis", "Wilson", "Taylor", "Anderson",
    "Thomas", "Jackson", "White", "Harris", "Martin",
]

SOURCES = [
    ("The Wire Report", 1), ("Broadsheet Sport", 1), ("Continental Desk", 2),
    ("Matchday Live", 2), ("Terrace Talk", 3), ("Window Watch", 3),
]


def season_start(season: str) -> int:
    return int(season.split("-")[0])


def window_dates(season: str, kind: str) -> tuple[dt.date, dt.date]:
    y = season_start(season)
    if kind == "summer":
        return dt.date(y, 7, 1), dt.date(y, 9, 1)
    return dt.date(y + 1, 1, 1), dt.date(y + 1, 2, 1)


def season_end(season: str) -> dt.date:
    return dt.date(season_start(season) + 1, 6, 30)


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class _Player:
    """Mutable simulation state for one player."""

    __slots__ = (
        "pid", "name", "pos", "birth", "club", "contract_expires",
        "minutes_share", "value_trend", "depth", "agent_churn", "loaned_last",
        "left", "joined_on",
    )

    def __init__(self, pid, name, pos, birth, club, rng):
        self.pid = pid
        self.name = name
        self.pos = pos
        self.birth = birth
        self.club = club
        self.contract_expires = None
        self.minutes_share = rng.betavariate(2.4, 2.0)
        self.value_trend = rng.gauss(0.0, 0.35)
        self.depth = rng.randint(1, 4)
        self.agent_churn = rng.betavariate(1.6, 4.0)
        self.loaned_last = False
        self.left = False
        self.joined_on = None

    def age_on(self, when: dt.date) -> float:
        return (when - self.birth).days / 365.25


def _propensity(p: _Player, as_of: dt.date, club_state: dict, rumour_score: float) -> float:
    """Latent log-odds that this player leaves in this window.

    Weights follow the ranking in BACKEND.md §5: contract length dominates,
    then minutes trend, then age against the positional peak, then club
    circumstance. The feature builder recovers all of these from the raw
    tables, so a model fitted on those features can find this signal.
    """
    months_left = max(0.0, (p.contract_expires - as_of).days / 30.44)
    age_gap = p.age_on(as_of) - PEAK_AGE[p.pos]

    # Intercept is tuned so the summer positive rate lands near the 15–20%
    # BACKEND.md §4 expects; winter is shifted down further at the call site.
    z = -3.55
    z += 2.35 * math.exp(-months_left / 11.0)          # cliff under ~12 months
    z += -1.65 * (p.minutes_share - 0.5)                # falling minutes -> exit
    z += 0.16 * max(0.0, age_gap)                       # past peak, sell
    z += -0.10 * max(0.0, -age_gap)                     # pre-peak, keep
    z += 0.55 * club_state["manager_changed"]
    z += 0.85 if club_state["psr_headroom"] < 0 else 0.0
    z += 1.05 * club_state["relegated"]
    z += -0.45 * club_state["qualified_ucl"]
    z += -0.30 * p.value_trend
    z += 0.22 * (p.depth - 1)
    z += 0.34 * rumour_score
    z += 0.40 * p.loaned_last
    z += 1.30 * p.agent_churn
    return z


def _destination(rng, from_club):
    """Where a player is heading: another of the four clubs, or out of them.

    None means "somewhere outside this dataset", stored as a NULL destination.
    """
    if rng.random() < ABROAD_RATE:
        return None
    return rng.choice([c for c in CLUB_NAMES if c != from_club])


def _name_pool(rng):
    """Yield dummy names indefinitely.

    The two lists cross to 100 combinations, which is exactly the four opening
    squads, so those are all distinct. After that names repeat rather than
    growing a numeric suffix: `player_id` is the identity, duplicate names are
    what real feeds actually look like, and "Paul Jackson 2" is not a name.
    """
    pairs = [f"{f} {l}" for f in FIRST_NAMES for l in LAST_NAMES]
    rng.shuffle(pairs)
    yield from pairs
    while True:
        yield rng.choice(pairs)


def build(verbose: bool = True) -> dict:
    """Wipe the dev database and regenerate the whole dataset."""
    rng = random.Random(SEED)
    db.reset()

    clubs: dict[str, int] = {}
    players: list[_Player] = []
    rows = {k: [] for k in (
        "transfer", "contract", "stats", "rumour", "squad", "finance",
    )}

    with db.connect() as conn:
        for name in CLUB_NAMES:
            db.execute(
                conn,
                "INSERT INTO club (name, country, league_tier, source_ids) VALUES (?,?,?,?)",
                (name, "England", 1, db.to_json({"synthetic": name.lower().replace(" ", "-")})),
            )
        for row in db.query(conn, "SELECT club_id, name FROM club"):
            clubs[row["name"]] = row["club_id"]

        # Only the seasons the simulation actually plays out get a window. A
        # window with no simulated outcomes would look like a window in which
        # nobody moved, and every row in it would train as a false negative.
        for season in WINDOW_SEASONS:
            for kind in ("summer", "winter"):
                opens, closes = window_dates(season, kind)
                db.execute(
                    conn,
                    "INSERT INTO transfer_window (season, window_type, opens_on, closes_on)"
                    " VALUES (?,?,?,?)",
                    (season, kind, opens.isoformat(), closes.isoformat()),
                )
        windows = {
            (r["season"], r["window_type"]): r
            for r in db.query(conn, "SELECT * FROM transfer_window")
        }

        # --- squads -----------------------------------------------------
        start = dt.date(season_start(SEASONS[0]), 7, 1)
        names = _name_pool(rng)
        next_pid = 1

        for club_name in CLUB_NAMES:
            for pos in POSITIONS:
                age = rng.uniform(18.5, 33.0)
                birth = start - dt.timedelta(days=int(age * 365.25))
                p = _Player(next_pid, next(names), pos, birth, club_name, rng)
                # Expiries run 3–6 years out from the simulation's first day so
                # that by the time the first window opens two seasons later the
                # spread of remaining contract length is already in steady
                # state. Starting them nearer expiry front-loads the whole
                # dataset with departures that no club ever had a chance to
                # renew, and the first window ends up an outlier.
                p.contract_expires = dt.date(
                    start.year + rng.randint(3, 6), 6, 30
                )
                p.joined_on = start
                players.append(p)
                next_pid += 1

        def insert_players(batch):
            db.execute_many(
                conn,
                "INSERT INTO player (player_id, name, birth_date, primary_pos, nationality,"
                " source_ids) VALUES (?,?,?,?,?,?)",
                [
                    (p.pid, p.name, p.birth.isoformat(), p.pos, "England",
                     db.to_json({"synthetic": p.pid}))
                    for p in batch
                ],
            )

        insert_players(players)

        club_history: dict[tuple[str, str], dict] = {}

        # --- simulate season by season ----------------------------------
        for si, season in enumerate(SEASONS):
            observed = season_end(season)

            for club_name in CLUB_NAMES:
                squad = [p for p in players if p.club == club_name and not p.left]
                relegated = 1 if rng.random() < 0.15 else 0
                state = {
                    "manager_changed": 1 if rng.random() < 0.30 else 0,
                    "psr_headroom": int(rng.gauss(35_000_000, 55_000_000)),
                    "relegated": relegated,
                    "qualified_ucl": 0 if relegated else (1 if rng.random() < 0.22 else 0),
                }
                club_history[(club_name, season)] = state
                rows["finance"].append((
                    clubs[club_name], season, state["psr_headroom"],
                    int(rng.uniform(120, 480) * 1_000_000),
                    (observed - dt.timedelta(days=rng.randint(20, 300))).isoformat()
                    if state["manager_changed"] else None,
                    state["relegated"], state["qualified_ucl"], observed.isoformat(),
                ))

                # Minutes are shared out by depth rank with noise, then written
                # as a season stat line observed the day the season ends.
                for p in squad:
                    drift = rng.gauss(0, 0.14) - 0.045 * max(0.0, p.age_on(observed) - PEAK_AGE[p.pos])
                    p.minutes_share = min(0.98, max(0.02, p.minutes_share + drift))
                    minutes = int(p.minutes_share * 3420)
                    apps = min(38, int(minutes / 78) + rng.randint(0, 3))
                    starts = max(0, int(apps * p.minutes_share))
                    goal_rate = {"GK": 0.0, "DF": 0.06, "MF": 0.14, "FW": 0.42}[p.pos]
                    goals = int(rng.gauss(goal_rate * minutes / 90, 1.6))
                    assists = int(rng.gauss(goal_rate * minutes / 140, 1.3))
                    rows["stats"].append((
                        p.pid, clubs[club_name], season, minutes, apps, starts,
                        max(0, goals), max(0, assists),
                        round(max(0.0, rng.gauss(goal_rate * minutes / 88, 1.4)), 2),
                        round(max(0.0, rng.gauss(goal_rate * minutes / 150, 1.1)), 2),
                        int(max(0, rng.gauss(minutes / 22, 30))),
                        observed.isoformat(),
                    ))
                    p.value_trend = 0.6 * p.value_trend + rng.gauss(0, 0.3)

                by_pos: dict[str, list[_Player]] = {}
                for p in squad:
                    by_pos.setdefault(p.pos, []).append(p)
                for group in by_pos.values():
                    group.sort(key=lambda q: -q.minutes_share)
                    for rank, p in enumerate(group, start=1):
                        p.depth = rank

            if season not in WINDOW_SEASONS:
                continue

            for kind in ("summer", "winter"):
                w = windows[(season, kind)]
                opens = dt.date.fromisoformat(w["opens_on"])
                closes = dt.date.fromisoformat(w["closes_on"])
                is_live = season == SEASONS[-1] and kind == "winter"

                for p in players:
                    if p.left:
                        continue
                    # Contract state as we knew it a week before the window.
                    rows["contract"].append((
                        p.pid, clubs[p.club],
                        (p.contract_expires - dt.timedelta(days=365 * 3)).isoformat(),
                        p.contract_expires.isoformat(),
                        (opens - dt.timedelta(days=7)).isoformat(),
                    ))
                    rows["squad"].append((
                        p.pid, clubs[p.club],
                        (p.joined_on or start).isoformat(),
                        "9999-12-31",
                    ))

                    state = club_history[(p.club, season)]
                    months_left = max(0.0, (p.contract_expires - opens).days / 30.44)

                    # Rumours cluster on players who are actually close to
                    # moving, which is why the feature carries signal.
                    heat = _logistic(
                        1.4 * math.exp(-months_left / 11.0)
                        - 1.2 * (p.minutes_share - 0.5) - 1.9
                    )
                    score = 0.0
                    for _ in range(rng.randint(0, 4)):
                        if rng.random() > heat * 2.2:
                            continue
                        src, tier = rng.choice(SOURCES)
                        dest = _destination(rng, p.club)
                        published = opens - dt.timedelta(days=rng.randint(1, 29))
                        rows["rumour"].append((
                            p.pid, clubs[dest] if dest else None,
                            dt.datetime.combine(published, dt.time(rng.randint(7, 22))).isoformat() + "Z",
                            src, tier,
                            f"{p.name} linked with {dest or 'a move abroad'}",
                            None,
                        ))
                        score += 1.0 / tier

                    if is_live:
                        continue  # the live window has no outcome yet

                    z = _propensity(p, opens, state, score)
                    if kind == "winter":
                        z -= 0.85  # far fewer permanent moves in January
                    moved = rng.random() < _logistic(z)
                    if not moved:
                        p.loaned_last = rng.random() < 0.06
                        # Clubs renew players they intend to keep. Without this
                        # the contract feature saturates: everyone who never
                        # moves ends up permanently at zero months remaining.
                        if months_left < 14 and rng.random() < 0.42:
                            p.contract_expires = dt.date(
                                opens.year + rng.randint(2, 4), 6, 30
                            )
                        continue

                    if months_left <= 0.5:
                        ttype, fee = "end_of_contract", 0
                    elif months_left < 7 and rng.random() < 0.45:
                        ttype, fee = "free", 0
                    elif rng.random() < 0.18:
                        ttype, fee = "loan", None
                    else:
                        base = 4_000_000 * math.exp(2.6 * p.minutes_share)
                        base *= max(0.25, 1.45 - 0.05 * max(0.0, p.age_on(opens) - PEAK_AGE[p.pos]))
                        ttype, fee = "permanent", int(base * rng.uniform(0.6, 1.9))

                    dest = _destination(rng, p.club)
                    announced = opens + dt.timedelta(days=rng.randint(0, (closes - opens).days))
                    rows["transfer"].append((
                        p.pid, clubs[p.club], clubs[dest] if dest else None, w["window_id"],
                        announced.isoformat(), fee, ttype, "synthetic-sim",
                    ))

                    if ttype == "loan":
                        p.loaned_last = True
                        continue

                    # Close the membership row at the window it ended in, then
                    # move the player on. Nothing is overwritten (§9).
                    rows["squad"][-1] = (
                        p.pid, clubs[p.club], (p.joined_on or start).isoformat(),
                        announced.isoformat(),
                    )
                    if dest:
                        p.club = dest
                        p.joined_on = announced
                        p.contract_expires = dt.date(announced.year + rng.randint(3, 5), 6, 30)
                        p.minutes_share = min(0.95, p.minutes_share + rng.uniform(0.0, 0.15))
                    else:
                        p.left = True

                # Refill squads that dropped below strength.
                fresh: list[_Player] = []
                for club_name in CLUB_NAMES:
                    squad = [p for p in players if p.club == club_name and not p.left]
                    shortfall = SQUAD_SIZE - len(squad)
                    for _ in range(max(0, shortfall)):
                        pos = rng.choice(POSITIONS)
                        age = rng.uniform(18.0, 30.0)
                        p = _Player(
                            next_pid, next(names), pos,
                            opens - dt.timedelta(days=int(age * 365.25)), club_name, rng,
                        )
                        p.contract_expires = dt.date(opens.year + rng.randint(2, 5), 6, 30)
                        p.joined_on = opens + dt.timedelta(days=rng.randint(0, 30))
                        players.append(p)
                        fresh.append(p)
                        next_pid += 1
                if fresh:
                    insert_players(fresh)

        if verbose:
            print(f"  players {len(players)}  transfers {len(rows['transfer'])}")

        db.execute_many(
            conn,
            "INSERT INTO transfer (player_id, from_club_id, to_club_id, window_id,"
            " announced_on, fee_eur, transfer_type, source) VALUES (?,?,?,?,?,?,?,?)",
            rows["transfer"],
        )
        db.execute_many(
            conn,
            "INSERT OR REPLACE INTO contract (player_id, club_id, starts_on, expires_on,"
            " observed_on) VALUES (?,?,?,?,?)",
            rows["contract"],
        )
        db.execute_many(
            conn,
            "INSERT OR REPLACE INTO player_season_stats (player_id, club_id, season, minutes,"
            " apps, starts, goals, assists, xg, xag, prog_carries, observed_on)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows["stats"],
        )
        db.execute_many(
            conn,
            "INSERT INTO rumour (player_id, to_club_id, published_at, source_name,"
            " source_tier, headline, url) VALUES (?,?,?,?,?,?,?)",
            rows["rumour"],
        )
        db.execute_many(
            conn,
            "INSERT OR REPLACE INTO squad_membership (player_id, club_id, valid_from, valid_to)"
            " VALUES (?,?,?,?)",
            rows["squad"],
        )
        db.execute_many(
            conn,
            "INSERT OR REPLACE INTO club_finance (club_id, season, psr_headroom_eur,"
            " squad_cost_eur, manager_changed_on, relegated, qualified_ucl, observed_on)"
            " VALUES (?,?,?,?,?,?,?,?)",
            rows["finance"],
        )

        counts = {
            t: db.query_one(conn, f"SELECT COUNT(*) AS n FROM {t}")["n"]
            for t in ("club", "player", "transfer", "contract", "player_season_stats",
                      "rumour", "squad_membership", "transfer_window")
        }
    return counts


if __name__ == "__main__":
    print("Seeding synthetic dataset…")
    for table, n in build().items():
        print(f"  {table:22} {n:>7,}")
