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

#: Half-life, in gameweeks, for how fast an old appearance stops counting.
#: Season totals say a dropped player is a starter for weeks after he stops
#: being one, and backtesting showed that is where the projection's optimism
#: comes from: conditioned on a player actually featuring the model is close
#: to unbiased, so the error is in predicting whether he features at all.
MINUTES_HALF_LIFE = 3.0

#: Gameweeks of recent evidence before the window is trusted over the season
#: total. Two, because three appearances is a thin sample and a rotated player
#: and a dropped one look identical inside it.
RECENT_PRIOR_GAMEWEEKS = 2.0

#: How many finished gameweeks of per-player history to read. Past six the
#: half-life has reduced the weight to under a quarter.
RECENT_GAMEWEEKS = 6

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
class Appearance:
    """What a player did in one gameweek his club actually played.

    Gameweeks the club blanked are absent rather than zero: a blank says
    nothing about whether the manager fancies him.
    """

    gw: int
    minutes: float
    started: bool


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
    #: Most recent gameweeks first or last, order does not matter. Empty when
    #: no per-gameweek history was supplied, in which case the minutes model
    #: falls back to season totals.
    recent: list[Appearance] = field(default_factory=list)

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


RATES = ("xg90", "xa90", "xgc90", "saves90", "dc90", "bps90")


def _fit_line(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares intercept and slope. Returns (mean, 0) if x never varies."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var < 1e-9:
        return my, 0.0
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    return my - slope * mx, slope


class Priors:
    """What we expect of a player before seeing this season's minutes.

    The obvious prior is the positional average, and it is wrong in a way that
    matters. Shrinking towards "an average forward" is right for a mid-table
    striker with three noisy games; it is a systematic penalty on the players
    people actually captain, because it pulls a genuine outlier towards a
    number we already know is false about him. Early in a season, when every
    rate is thin, that is exactly when the error is largest.

    So the prior is conditioned on price. A player's cost is FPL's own encoding
    of what it expects from him, set from last season before a ball was kicked
    this one, and it is available for every player at no extra request. Within
    each position the rate is regressed on cost, and a player is shrunk towards
    the fitted value at *his* price rather than towards the middle of his
    position. A £15.5m forward is compared against what £15.5m forwards do.

    Fitted values are clamped to the range actually observed in the position,
    because a straight line extrapolated to the most expensive player in the
    game is not evidence.
    """

    def __init__(self, players: list[Player]):
        self._fits: dict[int, dict[str, tuple[float, float]]] = {}
        self._bounds: dict[int, dict[str, tuple[float, float]]] = {}
        self._means: dict[int, dict[str, float]] = {}

        for pos in (1, 2, 3, 4):
            # Players with real minutes only: the hundreds of squad fillers on
            # zero minutes would drag every fit towards nothing.
            pool = [p for p in players if p.position == pos and p.minutes >= 180]
            if len(pool) < 6:
                pool = [p for p in players if p.position == pos] or players

            costs = [float(p.cost) for p in pool]
            self._fits[pos], self._bounds[pos], self._means[pos] = {}, {}, {}
            for rate in RATES:
                values = [getattr(p, rate) for p in pool]
                self._fits[pos][rate] = _fit_line(costs, values)
                self._bounds[pos][rate] = (min(values), max(values))
                self._means[pos][rate] = sum(values) / len(values)

    def for_player(self, p: Player) -> dict[str, float]:
        out = {}
        for rate in RATES:
            intercept, slope = self._fits[p.position][rate]
            low, high = self._bounds[p.position][rate]
            out[rate] = max(low, min(high, intercept + slope * p.cost))
        return out

    def mean(self, position: int, rate: str) -> float:
        """The plain positional average, still needed to judge a leaky defence."""
        return self._means[position][rate]


def _shrink(rate: float, prior: float, minutes: float) -> float:
    """Pull a thin sample towards the positional average.

    With 270 minutes of evidence the player's own rate and the prior weigh
    equally; by a full season the prior is nearly irrelevant. This is a
    James-Stein style estimator, and it is the difference between a model that
    recommends a striker on one lucky finish and one that does not.
    """
    w = minutes / (minutes + SHRINKAGE_MINUTES)
    return w * rate + (1 - w) * prior


def _season_profile(p: Player) -> tuple[float, float, float]:
    """(P(any), P(60+), expected minutes) from season totals alone.

    The fallback, for a player with no per-gameweek history behind him. Start
    rate is the honest basis: a player who starts is very likely to reach 60
    minutes, a substitute rarely is.

    Note what this cannot do. `p.minutes / 90` counts 90-minute equivalents
    rather than matches, so it cannot tell a player who started six times and
    was always withdrawn at 60 from one who started four times and finished
    them. That is the whole reason recent appearances are worth fetching.
    """
    if p.minutes == 0:
        # No evidence at all. Treat as a fringe player rather than assuming
        # either extreme; the availability multiplier still applies.
        return 0.25, 0.10, 90.0 * 0.10 + 25.0 * 0.15

    nineties = max(1.0, p.minutes / 90.0)
    start_rate = min(1.0, p.starts / nineties) if p.starts else 0.0
    p_any = min(1.0, 0.35 + 0.65 * start_rate)
    p_60 = min(0.97, start_rate)
    return p_any, p_60, 90.0 * p_60 + 25.0 * max(0.0, p_any - p_60)


def _recent_profile(p: Player) -> tuple[float, float, float, float]:
    """(P(any), P(60+), expected minutes, gameweeks of evidence).

    Read straight off what happened rather than inferred from a start rate,
    and weighted towards the present on a half-life. The fourth value is the
    summed weight: the effective number of gameweeks the estimate rests on.
    """
    if not p.recent:
        return 0.0, 0.0, 0.0, 0.0

    newest = max(a.gw for a in p.recent)
    total = played = sixty = minutes = 0.0
    for a in p.recent:
        w = 0.5 ** ((newest - a.gw) / MINUTES_HALF_LIFE)
        total += w
        if a.minutes > 0:
            played += w
        if a.minutes >= 60:
            sixty += w
        minutes += w * a.minutes

    if total <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return played / total, sixty / total, minutes / total, total


def _minutes_profile(p: Player) -> tuple[float, float, float]:
    """(P(any minutes), P(60+ minutes), expected minutes).

    Whether a player features at all is the largest single term in his score,
    and season totals describe a player who may no longer exist: someone who
    lost his place a month ago still reads as a starter for weeks afterwards.

    So recent gameweeks are weighted towards the present, then shrunk back
    towards the season total only as far as the thinness of the window
    demands. With no history supplied this is exactly the old estimate, so the
    model degrades rather than breaks.

    Everything is conditioned on being available in the first place.
    """
    s_any, s_60, s_minutes = _season_profile(p)
    r_any, r_60, r_minutes, evidence = _recent_profile(p)

    if evidence > 0:
        w = evidence / (evidence + RECENT_PRIOR_GAMEWEEKS)
        p_any = w * r_any + (1 - w) * s_any
        p_60 = w * r_60 + (1 - w) * s_60
        expected_minutes = w * r_minutes + (1 - w) * s_minutes
    else:
        p_any, p_60, expected_minutes = s_any, s_60, s_minutes

    p_any *= p.availability
    p_60 *= p.availability
    expected_minutes *= p.availability
    # Sixty minutes is a subset of any minutes; blending two estimates can
    # otherwise cross them over for a player who is always either benched or
    # played in full.
    return p_any, min(p_any, p_60), expected_minutes


def _expected_bonus(bps90: float, minutes: float) -> float:
    """Bonus points are the top three BPS scores in a match.

    There is no clean closed form, so this is a saturating curve fitted to the
    shape of the relationship: a player averaging ~30 BPS/90 picks up roughly a
    point of bonus a game, and it tails off rather than growing without bound.
    """
    per90 = max(0.0, bps90)
    return (minutes / 90.0) * 1.8 * (per90 ** 2) / (per90 ** 2 + 26.0 ** 2)


def project_one(p: Player, fixtures: list[Fixture], priors: "Priors") -> float:
    """Expected points for one player across the fixtures of one gameweek.

    A blank gameweek is an empty list and scores zero. A double is two entries
    and the terms simply add, which is the whole reason doubles are worth
    planning around.
    """
    if not fixtures or p.availability <= 0:
        return 0.0

    prior = priors.for_player(p)
    xg90 = _shrink(p.xg90, prior["xg90"], p.minutes)
    xa90 = _shrink(p.xa90, prior["xa90"], p.minutes)
    xgc90 = _shrink(p.xgc90, prior["xgc90"], p.minutes)
    saves90 = _shrink(p.saves90, prior["saves90"], p.minutes)
    dc90 = _shrink(p.dc90, prior["dc90"], p.minutes)
    bps90 = _shrink(p.bps90, prior["bps90"], p.minutes)

    # "Leaky" is measured against the position as a whole, not against what a
    # side at this price should concede — the question is whether this defence
    # keeps clean sheets, not whether it is good value.
    league_xgc = priors.mean(p.position, "xgc90")
    leak = xgc90 / max(0.3, league_xgc)

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
            p_cs = max(0.02, min(0.75, base * (2.0 - leak)))
            total += p_cs * CLEAN_SHEET_POINTS[p.position] * p_60

        if p.position == 1:
            total += (saves90 * share) / 3.0

        if p.position in (1, 2):
            conceded = CONCEDED_BY_FDR[fdr] * max(0.4, leak)
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


def fixtures_by_team(
    fixtures_raw: list, horizon: list[int], skip_finished: bool = True
) -> dict[tuple[int, int], list[Fixture]]:
    """Fixtures indexed by (gameweek, team).

    A double gameweek is simply a list of length two and a blank is an absent
    key, which is the whole reason doubles are worth planning around.

    Projecting forward skips matches already played; replaying a past
    gameweek in fpl/backtest.py wants exactly those, hence `skip_finished`.
    """
    by_team: dict[tuple[int, int], list[Fixture]] = {}
    for fx in fixtures_raw:
        gw = fx.get("event")
        if gw is None or gw not in horizon:
            continue
        if skip_finished and fx.get("finished"):
            continue
        by_team.setdefault((gw, fx["team_h"]), []).append(
            Fixture(fx["team_a"], True, fx.get("team_h_difficulty", 3), fx.get("kickoff_time"))
        )
        by_team.setdefault((gw, fx["team_a"]), []).append(
            Fixture(fx["team_h"], False, fx.get("team_a_difficulty", 3), fx.get("kickoff_time"))
        )
    return by_team


def appearances_from_live(
    history: dict[int, dict[int, dict]],
    fixtures_raw: list,
    team_of: dict[int, int],
) -> dict[int, list[Appearance]]:
    """Turn per-gameweek live stats into per-player appearance lists.

    A gameweek in which a player's club did not play is left out rather than
    recorded as a benching, because a blank is not evidence about selection.
    That distinction is the difference between reading a blank gameweek as
    rotation and reading it as what it is.
    """
    playing: dict[int, set[int]] = {}
    for fx in fixtures_raw:
        gw = fx.get("event")
        if gw is None:
            continue
        playing.setdefault(gw, set()).update((fx["team_h"], fx["team_a"]))

    out: dict[int, list[Appearance]] = {}
    for gw, by_player in history.items():
        had_a_match = playing.get(gw, set())
        for pid, stats in by_player.items():
            if team_of.get(pid) not in had_a_match:
                continue
            out.setdefault(pid, []).append(Appearance(
                gw=gw,
                minutes=_f(stats.get("minutes")),
                started=bool(_f(stats.get("starts"))),
            ))
    return out


def build(
    bootstrap: dict,
    fixtures_raw: list,
    horizon: list[int],
    recent: dict[int, list[Appearance]] | None = None,
) -> dict[int, Projection]:
    """Project every player over each gameweek in `horizon`."""
    teams = {t["id"]: t for t in bootstrap["teams"]}
    players = [player_from(r, teams) for r in bootstrap["elements"]]
    if recent:
        for p in players:
            p.recent = recent.get(p.id, [])
    priors = Priors(players)

    by_team = fixtures_by_team(fixtures_raw, horizon)

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
