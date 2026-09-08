"""Expected points per player per gameweek.

The whole tool rests on one number: how many points is this player likely to
score in the gameweeks ahead. Everything else — which transfer is worth a hit,
whether the wildcard is worth playing — is arithmetic on top of it.

The model is deliberately transparent rather than clever. Each component below
is a term in the actual scoring system, estimated from that player's own rate
statistics and scaled by the fixture in front of them:

    xP  =  P(appears) x appearance points
         + expected goals x points per goal for the position
         + expected assists x 3
         + P(clean sheet) x clean sheet points for the position
         + saves / 3                        (goalkeepers)
         - expected goals conceded / 2      (goalkeepers and defenders)
         + expected bonus
         + P(defensive contribution) x 2

Two honest limits. Rate statistics early in a season are noisy — a striker with
one goal from 90 minutes reads as a 1.0 xG/90 player — so rates are shrunk
towards the positional average by a prior weighted in minutes, which is what
stops a single cameo from topping the board. And fixture difficulty is FPL's
own 1-5 rating, which is a coarse instrument: it knows Arsenal away is hard, it
does not know their centre-backs are suspended.

FPL publishes its own one-gameweek estimate as `ep_next`. Where that exists it
is blended in, because two mediocre estimates of the same quantity beat either
alone, and it catches news this model cannot see.
"""

import math
from dataclasses import dataclass, field

from .rules import POSITION_NAME

# Points for a goal, by position id.
GOAL_POINTS = {1: 10, 2: 6, 3: 5, 4: 4}
ASSIST_POINTS = 3
#: Clean sheet is only paid if the player completes 60 minutes.
CLEAN_SHEET_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}

#: Defensive contribution, introduced for 2025/26: 2 points for clearances,
#: blocks, interceptions and tackles over a positional threshold.
DEFENSIVE_CONTRIBUTION_POINTS = 2
DEFENSIVE_CONTRIBUTION_THRESHOLD = {1: None, 2: 10.0, 3: 12.0, 4: 12.0}

#: Clean sheet probability by fixture difficulty rating, before adjusting for
#: how leaky the side actually is. Calibrated to the long-run league rate of
#: roughly one clean sheet in three.
CLEAN_SHEET_BY_FDR = {1: 0.50, 2: 0.42, 3: 0.32, 4: 0.22, 5: 0.14}

#: Goals conceded by fixture difficulty, used for the -1 per 2 conceded term.
CONCEDED_BY_FDR = {1: 0.8, 2: 1.0, 3: 1.35, 4: 1.7, 5: 2.1}

#: Attacking output multiplier by fixture difficulty. A 3 is neutral.
ATTACK_BY_FDR = {1: 1.35, 2: 1.18, 3: 1.0, 4: 0.84, 5: 0.68}

#: Minutes of prior evidence before a player's own rate is trusted over the
#: positional average. Three full matches.
SHRINKAGE_MINUTES = 270.0

#: Weight given to FPL's own `ep_next` for the immediate gameweek.
EP_NEXT_WEIGHT = 0.35

#: Availability by status flag. `d` is doubtful and usually carries an explicit
#: percentage, which is preferred when present.
STATUS_AVAILABILITY = {
    "a": 1.0,     # available
    "d": 0.5,     # doubtful
    "i": 0.0,     # injured
    "s": 0.0,     # suspended
    "u": 0.0,     # unavailable
    "n": 0.0,     # not in squad / ineligible
}


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Fixture:
    """One match a team plays in a gameweek."""

    opponent: int
    home: bool
    difficulty: int
    kickoff: str | None = None


@dataclass
class Player:
    """A player, flattened to what the projection needs."""

    id: int
    name: str
    team: int
    position: int
    cost: int
    status: str
    availability: float
    minutes: int
    starts: int
    xg90: float
    xa90: float
    xgc90: float
    saves90: float
    dc90: float
    bps90: float
    ep_next: float
    form: float
    points_per_game: float
    total_points: int
    selected_by: float
    news: str = ""
    team_name: str = ""
    team_short: str = ""

    @property
    def position_name(self) -> str:
        return POSITION_NAME.get(self.position, "?")


@dataclass
class Projection:
    """Expected points for one player over one or more gameweeks."""

    player: Player
    per_gw: dict[int, float] = field(default_factory=dict)
    fixtures: dict[int, list[Fixture]] = field(default_factory=dict)

    def over(self, gws: list[int]) -> float:
        return sum(self.per_gw.get(gw, 0.0) for gw in gws)


def _availability(raw: dict) -> float:
    """How likely the player is to feature at all.

    An explicit `chance_of_playing_next_round` is the club's own statement and
    beats the coarse status letter, so it wins whenever it is set.
    """
    chance = raw.get("chance_of_playing_next_round")
    if chance is not None:
        return max(0.0, min(1.0, _f(chance) / 100.0))
    return STATUS_AVAILABILITY.get(raw.get("status", "a"), 1.0)


def player_from(raw: dict, teams: dict[int, dict]) -> Player:
    team = teams.get(raw["team"], {})
    return Player(
        id=raw["id"],
        name=raw["web_name"],
        team=raw["team"],
        position=raw["element_type"],
        cost=raw["now_cost"],
        status=raw.get("status", "a"),
        availability=_availability(raw),
        minutes=int(raw.get("minutes") or 0),
        starts=int(raw.get("starts") or 0),
        xg90=_f(raw.get("expected_goals_per_90")),
        xa90=_f(raw.get("expected_assists_per_90")),
        xgc90=_f(raw.get("expected_goals_conceded_per_90")),
        saves90=_f(raw.get("saves_per_90")),
        dc90=_f(raw.get("defensive_contribution_per_90")),
        bps90=_f(raw.get("bps")) / max(1.0, _f(raw.get("minutes")) / 90.0),
        ep_next=_f(raw.get("ep_next")),
        form=_f(raw.get("form")),
        points_per_game=_f(raw.get("points_per_game")),
        total_points=int(raw.get("total_points") or 0),
        selected_by=_f(raw.get("selected_by_percent")),
        news=raw.get("news") or "",
        team_name=team.get("name", ""),
        team_short=team.get("short_name", ""),
    )


def _positional_means(players: list[Player]) -> dict[int, dict[str, float]]:
    """Average per-90 rates by position, over players with real minutes.

    These are the prior that thin samples are shrunk towards. Restricting the
    average to players who have actually played stops the hundreds of squad
    fillers on zero minutes from dragging every prior to nothing.
    """
    out = {}
    for pos in (1, 2, 3, 4):
        pool = [p for p in players if p.position == pos and p.minutes >= 180]
        if not pool:
            pool = [p for p in players if p.position == pos] or players
        n = len(pool)
        out[pos] = {
            "xg90": sum(p.xg90 for p in pool) / n,
            "xa90": sum(p.xa90 for p in pool) / n,
            "xgc90": sum(p.xgc90 for p in pool) / n,
            "saves90": sum(p.saves90 for p in pool) / n,
            "dc90": sum(p.dc90 for p in pool) / n,
            "bps90": sum(p.bps90 for p in pool) / n,
        }
    return out


def _shrink(rate: float, prior: float, minutes: float) -> float:
    """Pull a thin sample towards the positional average.

    With 270 minutes of evidence the player's own rate and the prior weigh
    equally; by a full season the prior is nearly irrelevant. This is a
    James-Stein style estimator, and it is the difference between a model that
    recommends a striker on one lucky finish and one that does not.
    """
    w = minutes / (minutes + SHRINKAGE_MINUTES)
    return w * rate + (1 - w) * prior


def _minutes_profile(p: Player) -> tuple[float, float, float]:
    """(P(any minutes), P(60+ minutes), expected minutes).

    Start rate is the honest basis: a player who starts is very likely to reach
    60 minutes, a substitute rarely is. Both are conditioned on being available
    in the first place.
    """
    appearances = max(1.0, p.minutes / 90.0)
    start_rate = min(1.0, p.starts / appearances) if p.starts else 0.0

    if p.minutes == 0:
        # No evidence at all. Treat as a fringe player rather than assuming
        # either extreme; the availability multiplier still applies.
        p_any, p_60 = 0.25, 0.10
    else:
        minutes_per_appearance = p.minutes / appearances
        p_any = min(1.0, 0.35 + 0.65 * start_rate)
        p_60 = min(0.97, start_rate * min(1.0, minutes_per_appearance / 75.0))

    p_any *= p.availability
    p_60 *= p.availability
    expected_minutes = 90.0 * p_60 + 25.0 * max(0.0, p_any - p_60)
    return p_any, p_60, expected_minutes


def _expected_bonus(bps90: float, minutes: float) -> float:
    """Bonus points are the top three BPS scores in a match.

    There is no clean closed form, so this is a saturating curve fitted to the
    shape of the relationship: a player averaging ~30 BPS/90 picks up roughly a
    point of bonus a game, and it tails off rather than growing without bound.
    """
    per90 = max(0.0, bps90)
    return (minutes / 90.0) * 1.8 * (per90 ** 2) / (per90 ** 2 + 26.0 ** 2)


def project_one(p: Player, fixtures: list[Fixture], priors: dict) -> float:
    """Expected points for one player across the fixtures of one gameweek.

    A blank gameweek is an empty list and scores zero. A double is two entries
    and the terms simply add, which is the whole reason doubles are worth
    planning around.
    """
    if not fixtures or p.availability <= 0:
        return 0.0

    prior = priors[p.position]
    xg90 = _shrink(p.xg90, prior["xg90"], p.minutes)
    xa90 = _shrink(p.xa90, prior["xa90"], p.minutes)
    xgc90 = _shrink(p.xgc90, prior["xgc90"], p.minutes)
    saves90 = _shrink(p.saves90, prior["saves90"], p.minutes)
    dc90 = _shrink(p.dc90, prior["dc90"], p.minutes)
    bps90 = _shrink(p.bps90, prior["bps90"], p.minutes)

    p_any, p_60, minutes = _minutes_profile(p)
    total = 0.0

    for fx in fixtures:
        fdr = max(1, min(5, fx.difficulty))
        # Home advantage, applied on top of a rating that already leans on it.
        attack = ATTACK_BY_FDR[fdr] * (1.06 if fx.home else 0.94)
        share = minutes / 90.0

        total += p_any * 1.0 + p_60 * 1.0                      # appearance
        total += xg90 * share * attack * GOAL_POINTS[p.position]
        total += xa90 * share * attack * ASSIST_POINTS
        total += _expected_bonus(bps90, minutes)

        if CLEAN_SHEET_POINTS[p.position]:
            base = CLEAN_SHEET_BY_FDR[fdr] * (1.10 if fx.home else 0.90)
            # A side that concedes more than the league average keeps fewer
            # clean sheets than its fixture alone suggests.
            leak = xgc90 / max(0.3, prior["xgc90"])
            p_cs = max(0.02, min(0.75, base * (2.0 - leak)))
            total += p_cs * CLEAN_SHEET_POINTS[p.position] * p_60

        if p.position == 1:
            total += (saves90 * share) / 3.0

        if p.position in (1, 2):
            conceded = CONCEDED_BY_FDR[fdr] * max(0.4, leak if CLEAN_SHEET_POINTS[p.position] else 1.0)
            total -= 0.5 * conceded * p_60

        threshold = DEFENSIVE_CONTRIBUTION_THRESHOLD[p.position]
        if threshold:
            # Probability of clearing the threshold in a match, modelled as a
            # Poisson count at the player's per-90 rate over expected minutes.
            expected = dc90 * share
            total += _poisson_at_least(threshold, expected) * DEFENSIVE_CONTRIBUTION_POINTS

    return max(0.0, total)


def _poisson_at_least(k: float, mean: float) -> float:
    """P(X >= k) for X ~ Poisson(mean), k rounded up to an integer count."""
    if mean <= 0:
        return 0.0
    k = int(math.ceil(k))
    # Sum the tail's complement; k is small (10-12) so this is cheap and exact.
    cumulative, term = 0.0, math.exp(-mean)
    for i in range(k):
        cumulative += term
        term *= mean / (i + 1)
    return max(0.0, 1.0 - cumulative)


def build(bootstrap: dict, fixtures_raw: list, horizon: list[int]) -> dict[int, Projection]:
    """Project every player over each gameweek in `horizon`."""
    teams = {t["id"]: t for t in bootstrap["teams"]}
    players = [player_from(r, teams) for r in bootstrap["elements"]]
    priors = _positional_means(players)

    # Fixtures indexed by (gameweek, team), so a double gameweek is simply a
    # list of length two and a blank is an absent key.
    by_team: dict[tuple[int, int], list[Fixture]] = {}
    for fx in fixtures_raw:
        gw = fx.get("event")
        if gw is None or gw not in horizon or fx.get("finished"):
            continue
        by_team.setdefault((gw, fx["team_h"]), []).append(
            Fixture(fx["team_a"], True, fx.get("team_h_difficulty", 3), fx.get("kickoff_time"))
        )
        by_team.setdefault((gw, fx["team_a"]), []).append(
            Fixture(fx["team_h"], False, fx.get("team_a_difficulty", 3), fx.get("kickoff_time"))
        )

    out = {}
    first = horizon[0] if horizon else None
    for p in players:
        proj = Projection(player=p)
        for gw in horizon:
            fxs = by_team.get((gw, p.team), [])
            proj.fixtures[gw] = fxs
            value = project_one(p, fxs, priors)
            # FPL's own estimate covers the immediate gameweek only, and it
            # sees team news this model does not.
            if gw == first and p.ep_next > 0 and fxs:
                value = (1 - EP_NEXT_WEIGHT) * value + EP_NEXT_WEIGHT * p.ep_next
            proj.per_gw[gw] = round(value, 3)
        out[p.id] = proj
    return out
