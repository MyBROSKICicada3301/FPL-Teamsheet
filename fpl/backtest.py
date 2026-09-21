"""Score the projection against gameweeks that have already been played.

Every constant in fpl/projection.py -- the shrinkage window, the fixture
multipliers, the bonus curve, the weight given to FPL's own estimate -- was set
by hand and has never been checked against an outcome. This module is what
turns them from opinions into measurements.

The one rule is that a projection for gameweek G may see only what was known
before G kicked off. `event/{gw}/live/` reports every player's stats for a
single gameweek in one request, so a player's state going into G is the sum of
gameweeks 1..G-1 and nothing else. That is what makes this a walk-forward test
rather than a demonstration that the model can fit a season it already knows
the answer to.

Three inputs cannot be reconstructed after the fact, and the numbers should be
read with them in mind.

- **FPL's own `ep_next`** is a live field with no history, so the blend in
  `projection.build` is off here. What is measured is the model's own
  arithmetic, unassisted, which is the part worth tuning.
- **Price** is today's rather than the price at the time. Prices drift by
  tenths over a season and cost only feeds the prior, so the effect is small,
  but it is a real thread of hindsight.
- **Availability** is a live field too. Everyone is treated as fit, so the
  appearance term is flattered for players who were in fact out. The `played`
  subset reports the same measures over players who did take the field, where
  that particular bias is absent.

Read the subsets rather than one headline number:

    all      every player whose team had a fixture that gameweek
    pool     those with 180+ minutes behind them, the realistic options
    played   those who actually got minutes, so availability cannot flatter it

Spearman matters more than MAE. Picking a squad is a ranking problem: it does
not matter much that every projection is half a point high, it matters whether
the right players are at the top.
"""

import math
from dataclasses import dataclass, field

from . import data
from .projection import (
    RECENT_GAMEWEEKS,
    Player,
    Priors,
    TeamStrength,
    appearances_from_live,
    fixtures_by_team,
    project_one,
    team_xg_from_live,
)
from .rules import POSITION_NAME

#: Minutes behind a player before he counts as a realistic pick. Matches the
#: threshold Priors uses to decide whose rates are worth fitting a line to.
POOL_MINUTES = 180.0

#: Finished gameweeks never change, so their live data can be held for a long
#: time. This is the difference between a backtest costing one request per
#: gameweek and one that hammers somebody else's server on every run.
LIVE_TTL = 30 * 86400


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------- the data


def finished_gameweeks(bootstrap: dict) -> list[int]:
    """Gameweek ids that have been played out, in order."""
    return sorted(e["id"] for e in bootstrap.get("events", []) if e.get("finished"))


def live(gw: int) -> dict[int, dict]:
    """Every player's stats for one gameweek, keyed by player id."""
    payload = data.live(gw, max_age=LIVE_TTL)
    elements = payload["elements"] if isinstance(payload, dict) else payload
    return {e["id"]: (e.get("stats") or {}) for e in elements}


@dataclass
class Totals:
    """A player's running totals going into some gameweek."""

    minutes: float = 0.0
    starts: float = 0.0
    xg: float = 0.0
    xa: float = 0.0
    xgc: float = 0.0
    saves: float = 0.0
    dc: float = 0.0
    bps: float = 0.0

    def add(self, s: dict) -> None:
        self.minutes += _f(s.get("minutes"))
        self.starts += _f(s.get("starts"))
        self.xg += _f(s.get("expected_goals"))
        self.xa += _f(s.get("expected_assists"))
        self.xgc += _f(s.get("expected_goals_conceded"))
        self.saves += _f(s.get("saves"))
        self.dc += _f(s.get("defensive_contribution"))
        self.bps += _f(s.get("bps"))

    def per90(self, total: float) -> float:
        if self.minutes <= 0:
            return 0.0
        return total / (self.minutes / 90.0)


def accumulate(history: dict[int, dict[int, dict]], upto: list[int]) -> dict[int, Totals]:
    """Running totals per player over the given gameweeks."""
    out: dict[int, Totals] = {}
    for gw in upto:
        for pid, stats in history.get(gw, {}).items():
            out.setdefault(pid, Totals()).add(stats)
    return out


def meta_from(bootstrap: dict) -> dict[int, dict]:
    """The fields that do not come from match stats: name, position, price."""
    teams = {t["id"]: t for t in bootstrap["teams"]}
    out = {}
    for raw in bootstrap["elements"]:
        team = teams.get(raw["team"], {})
        out[raw["id"]] = {
            "name": raw["web_name"],
            "team": raw["team"],
            "position": raw["element_type"],
            "cost": raw["now_cost"],
            "team_name": team.get("name", ""),
            "team_short": team.get("short_name", ""),
        }
    return out


def state_before(
    gw: int,
    history: dict[int, dict[int, dict]],
    meta: dict[int, dict],
    fixtures_raw: list,
) -> list[Player]:
    """Every player as the model would have seen them before gameweek `gw`.

    Only gameweeks strictly earlier than `gw` are summed or weighted, which is
    the whole point of the exercise. The recency window is the same one
    production reads, so what is scored here is what would have shipped.
    """
    earlier = [g for g in sorted(history) if g < gw]
    totals = accumulate(history, earlier)

    window = {g: history[g] for g in earlier[-RECENT_GAMEWEEKS:]}
    team_of = {pid: info["team"] for pid, info in meta.items()}
    recent = appearances_from_live(window, fixtures_raw, team_of)

    players = []

    for pid, info in meta.items():
        t = totals.get(pid, Totals())
        players.append(Player(
            id=pid,
            name=info["name"],
            team=info["team"],
            position=info["position"],
            cost=info["cost"],
            status="a",
            availability=1.0,          # not reconstructable; see the docstring
            minutes=int(t.minutes),
            starts=int(t.starts),
            xg90=t.per90(t.xg),
            xa90=t.per90(t.xa),
            xgc90=t.per90(t.xgc),
            saves90=t.per90(t.saves),
            dc90=t.per90(t.dc),
            bps90=t.per90(t.bps),
            ep_next=0.0,               # no history, so the blend stays off
            form=0.0,
            points_per_game=0.0,
            total_points=0,
            selected_by=0.0,
            team_name=info.get("team_name", ""),
            team_short=info.get("team_short", ""),
            recent=recent.get(pid, []),
        ))
    return players


# --------------------------------------------------------------- the measures


def _ranks(values: list[float]) -> list[float]:
    """Ranks, averaging ties. Needed because xP produces a lot of exact zeros."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-12 or dy < 1e-12:
        return 0.0
    return num / (dx * dy)


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation: does the model put the right players at the top?"""
    return _pearson(_ranks(xs), _ranks(ys))


@dataclass
class Score:
    """How one set of predictions did."""

    n: int = 0
    mae: float = 0.0
    rmse: float = 0.0
    bias: float = 0.0
    spearman: float = 0.0

    @classmethod
    def of(cls, pairs: list[tuple[float, float]]) -> "Score":
        if not pairs:
            return cls()
        n = len(pairs)
        errors = [p - a for p, a in pairs]
        return cls(
            n=n,
            mae=sum(abs(e) for e in errors) / n,
            rmse=math.sqrt(sum(e * e for e in errors) / n),
            bias=sum(errors) / n,
            spearman=spearman([p for p, _ in pairs], [a for _, a in pairs]),
        )


@dataclass
class Result:
    """One gameweek, replayed."""

    gw: int
    pairs: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    subsets: dict[str, Score] = field(default_factory=dict)
    #: Pool pairs split by position id, kept raw so several gameweeks can be
    #: pooled before scoring. Averaging per-gameweek scores would weight a
    #: thin gameweek the same as a full one.
    position_pairs: dict[int, list[tuple[float, float]]] = field(default_factory=dict)
    top_overlap: int = 0
    top_n: int = 20


def replay(gw: int, history, meta, fixtures_raw) -> Result:
    """Project one gameweek from what came before it, and score the result."""
    players = state_before(gw, history, meta, fixtures_raw)
    priors = Priors(players)

    # Club strength from matches already played, which for gameweek G means
    # strictly those before it. Rating a fixture using the result of the
    # fixture would make every number here meaningless.
    earlier = {g: v for g, v in history.items() if g < gw}
    team_of = {pid: info["team"] for pid, info in meta.items()}
    strength = TeamStrength(team_xg_from_live(earlier, fixtures_raw, team_of))
    by_team = fixtures_by_team(fixtures_raw, [gw], skip_finished=False)
    outcome = history.get(gw, {})

    everyone: list[tuple[float, float]] = []
    pool: list[tuple[float, float]] = []
    played: list[tuple[float, float]] = []
    by_position: dict[int, list[tuple[float, float]]] = {}
    ranked: list[tuple[float, float, int]] = []

    for p in players:
        fixtures = by_team.get((gw, p.team), [])
        if not fixtures:
            continue                      # blank gameweek: nothing to predict
        stats = outcome.get(p.id)
        if stats is None:
            continue

        predicted = project_one(p, fixtures, priors, strength)
        actual = _f(stats.get("total_points"))

        everyone.append((predicted, actual))
        if p.minutes >= POOL_MINUTES:
            pool.append((predicted, actual))
            by_position.setdefault(p.position, []).append((predicted, actual))
            ranked.append((predicted, actual, p.id))
        if _f(stats.get("minutes")) > 0:
            played.append((predicted, actual))

    result = Result(gw=gw)
    result.pairs = {"all": everyone, "pool": pool, "played": played}
    result.subsets = {name: Score.of(v) for name, v in result.pairs.items()}
    result.position_pairs = by_position

    # Of the players the model rated highest, how many were actually among the
    # highest scorers? That is the question a squad pick really asks.
    top_n = min(result.top_n, len(ranked))
    if top_n:
        predicted_top = {pid for _, _, pid in sorted(ranked, key=lambda r: -r[0])[:top_n]}
        actual_top = {pid for _, _, pid in sorted(ranked, key=lambda r: -r[1])[:top_n]}
        result.top_overlap = len(predicted_top & actual_top)
        result.top_n = top_n
    return result


def calibration(
    pairs: list[tuple[float, float]], buckets: int = 5
) -> list[tuple[float, float, int]]:
    """Mean predicted against mean actual, by predicted quantile.

    A calibrated model tracks the diagonal. Drifting above it in the top bucket
    means the projection is generous exactly where the decisions are made.
    """
    if not pairs:
        return []
    ordered = sorted(pairs, key=lambda pa: pa[0])
    size = max(1, len(ordered) // buckets)
    out = []
    for i in range(0, len(ordered), size):
        chunk = ordered[i:i + size]
        if len(chunk) < max(2, size // 4) and out:
            break                          # trailing remainder, not a bucket
        out.append((
            sum(p for p, _ in chunk) / len(chunk),
            sum(a for _, a in chunk) / len(chunk),
            len(chunk),
        ))
    return out


def run(gameweeks: list[int] | None = None) -> tuple[list[Result], dict]:
    """Replay every finished gameweek there is evidence for."""
    bootstrap = data.bootstrap()
    fixtures_raw = data.fixtures()
    meta = meta_from(bootstrap)

    done = finished_gameweeks(bootstrap)
    if not done:
        return [], {"finished": [], "evaluated": []}

    history = {gw: live(gw) for gw in done}

    # The first gameweek has nothing behind it to predict from, so the evidence
    # starts at the second.
    candidates = [g for g in done if g > done[0]]
    if gameweeks:
        candidates = [g for g in candidates if g in gameweeks]

    results = [replay(gw, history, meta, fixtures_raw) for gw in candidates]
    return results, {"finished": done, "evaluated": candidates}


# ----------------------------------------------------------------- reporting


BAR = "-" * 74


def _line(label: str, s: Score) -> str:
    return (f"  {label:<9}n={s.n:<5}  MAE {s.mae:5.2f}   RMSE {s.rmse:5.2f}   "
            f"bias {s.bias:+5.2f}   rank r {s.spearman:+.3f}")


def report(results: list[Result], info: dict) -> str:
    """The whole backtest as text."""
    if not results:
        return ("Nothing to score yet: a backtest needs at least two finished "
                "gameweeks.\nFinished so far: "
                f"{info.get('finished') or 'none'}")

    out = [BAR,
           f"  BACKTEST  gameweeks {results[0].gw}-{results[-1].gw}"
           f"  ({len(results)} scored, walk-forward)",
           BAR, ""]

    out.append("PER GAMEWEEK  (pool: 180+ minutes behind them)")
    out.append(f"  {'GW':<4}{'n':<7}{'MAE':>7}{'RMSE':>8}{'bias':>8}"
               f"{'rank r':>9}{'top20':>8}")
    for r in results:
        s = r.subsets["pool"]
        if not s.n:
            # Early gameweeks cannot fill the pool: nobody has 180 minutes
            # behind them yet. Saying so beats printing a row of zeroes.
            out.append(f"  {r.gw:<4}{'-':<7}{'too early for a 180-minute pool':>39}")
            continue
        out.append(f"  {r.gw:<4}{s.n:<7}{s.mae:7.2f}{s.rmse:8.2f}{s.bias:+8.2f}"
                   f"{s.spearman:+9.3f}{str(r.top_overlap) + '/' + str(r.top_n):>8}")

    merged = {name: [] for name in ("all", "pool", "played")}
    for r in results:
        for name, pairs in r.pairs.items():
            merged[name].extend(pairs)

    out += ["", "OVER EVERY GAMEWEEK SCORED"]
    for name in ("all", "pool", "played"):
        out.append(_line(name, Score.of(merged[name])))

    out += ["", "BY POSITION  (pool)"]
    by_pos: dict[int, list[tuple[float, float]]] = {}
    for r in results:
        for pos, pairs in r.position_pairs.items():
            by_pos.setdefault(pos, []).extend(pairs)
    for pos in sorted(by_pos):
        out.append(_line(POSITION_NAME.get(pos, str(pos)), Score.of(by_pos[pos])))

    out += ["", "CALIBRATION  (pool, by predicted quintile)",
            f"  {'predicted':>11}{'actual':>10}{'n':>8}   {'':<20}"]
    for predicted, actual, n in calibration(merged["pool"]):
        gap = actual - predicted
        flag = "" if abs(gap) < 0.30 else ("  under" if gap > 0 else "  over")
        out.append(f"  {predicted:11.2f}{actual:10.2f}{n:8}{flag}")

    out += ["",
            "Rank r is what matters for picking a squad; MAE only says whether",
            "the scale is right. ep_next blending is off here, so this is the",
            "model's own arithmetic. See the module docstring for the rest.",
            ""]
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse
    import os
    import sys

    exe = os.path.splitext(os.path.basename(sys.executable))[0] or "python3"
    ap = argparse.ArgumentParser(
        prog=f"{exe} -m fpl.backtest",
        description="Score the projection against gameweeks already played.",
    )
    ap.add_argument("--gw", type=int, nargs="*", metavar="N",
                    help="only these gameweeks (default: every one there is)")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output instead of the table")
    args = ap.parse_args(argv)

    try:
        results, info = run(args.gw or None)
    except data.FPLError as e:
        print(f"error [{e.code}]: {e}", file=sys.stderr)
        return 1

    if args.json:
        import json
        print(json.dumps({
            "evaluated": info.get("evaluated", []),
            "gameweeks": [
                {
                    "gw": r.gw,
                    "top_overlap": r.top_overlap,
                    "top_n": r.top_n,
                    "subsets": {k: vars(v) for k, v in r.subsets.items()},
                }
                for r in results
            ],
        }, indent=2))
        return 0

    print(report(results, info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
