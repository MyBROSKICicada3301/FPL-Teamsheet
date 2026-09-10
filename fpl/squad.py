"""Squad legality, the best eleven out of fifteen, and the captaincy.

Two separate questions live here and it is worth keeping them apart. *Is this
squad legal* is a matter of the published rules — fifteen players, two/five/
five/three by position, at most three from any one club, inside the budget.
*Which eleven should start* is an optimisation over the projections, and the
answer changes every gameweek without the squad changing at all.

The eleven is found by enumeration rather than by a rule of thumb. There are
only a handful of legal shapes, so trying all of them and keeping the best is
both exact and faster than reasoning about which formation "suits" the squad.
"""

from dataclasses import dataclass, field
from itertools import product

from .rules import FORMATION, POSITION_NAME, SQUAD_QUOTA, Rules, money


@dataclass
class Squad:
    """Fifteen player ids, plus the money and transfers behind them."""

    player_ids: list[int]
    bank: int = 0
    free_transfers: int = 1
    #: What each player would sell for, in tenths. Falls back to current price.
    selling: dict[int, int] = field(default_factory=dict)
    #: The armband as it currently stands, so advice can be reported as a
    #: change or a confirmation rather than as a bare instruction.
    captain: int | None = None
    vice_captain: int | None = None

    def sale_value(self, players: dict) -> int:
        return sum(self.selling.get(pid, players[pid].cost) for pid in self.player_ids)

    def budget(self, players: dict) -> int:
        """Everything available if the whole squad were sold."""
        return self.bank + self.sale_value(players)


@dataclass
class Problem:
    """A validation failure, in a form the API can return verbatim."""

    code: str
    message: str


def validate(squad: Squad, players: dict, rules: Rules,
             budget: int | None = None) -> list[Problem]:
    """Every way this squad breaks the rules, not just the first.

    `budget` is what the squad has to be affordable *within*, in tenths. It is
    optional because the £100.0m limit applies at the moment of purchase, not
    to what a squad is worth later: a team that has risen to £100.9m has broken
    no rule, and flagging it would be wrong. Pass a budget only when building
    or changing a squad, where affordability is genuinely the question.
    """
    problems: list[Problem] = []

    missing = [pid for pid in squad.player_ids if pid not in players]
    if missing:
        problems.append(Problem(
            "unknown_player",
            f"{len(missing)} player id(s) are not in this season's game: "
            + ", ".join(str(m) for m in missing[:5]),
        ))
        return problems

    if len(squad.player_ids) != rules.squad_size:
        problems.append(Problem(
            "squad_size",
            f"A squad is {rules.squad_size} players; this one has {len(squad.player_ids)}.",
        ))

    if len(set(squad.player_ids)) != len(squad.player_ids):
        seen, dupes = set(), set()
        for pid in squad.player_ids:
            (dupes if pid in seen else seen).add(pid)
        problems.append(Problem(
            "duplicate_player",
            "The same player appears twice: "
            + ", ".join(players[p].name for p in sorted(dupes)),
        ))

    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    per_club: dict[int, int] = {}
    for pid in squad.player_ids:
        p = players[pid]
        counts[p.position] = counts.get(p.position, 0) + 1
        per_club[p.team] = per_club.get(p.team, 0) + 1

    for pos, want in SQUAD_QUOTA.items():
        if counts.get(pos, 0) != want:
            problems.append(Problem(
                "position_quota",
                f"A squad needs exactly {want} {POSITION_NAME[pos]}; "
                f"this one has {counts.get(pos, 0)}.",
            ))

    for team, n in sorted(per_club.items()):
        if n > rules.team_limit:
            name = next(players[p].team_name for p in squad.player_ids
                        if players[p].team == team)
            problems.append(Problem(
                "club_limit",
                f"At most {rules.team_limit} players from one club; "
                f"this squad has {n} from {name}.",
            ))

    if squad.bank < 0:
        problems.append(Problem("negative_bank", "The bank cannot be negative."))

    if budget is not None:
        cost = sum(players[pid].cost for pid in squad.player_ids)
        if cost > budget:
            problems.append(Problem(
                "over_budget",
                f"This squad costs {money(cost)}, which is "
                f"{money(cost - budget)} more than the {money(budget)} available.",
            ))

    return problems


def _legal_shapes() -> list[tuple[int, int, int, int]]:
    """Every (GKP, DEF, MID, FWD) starting eleven the rules allow."""
    shapes = []
    for gk, df, md, fw in product(
        range(FORMATION[1][0], FORMATION[1][1] + 1),
        range(FORMATION[2][0], FORMATION[2][1] + 1),
        range(FORMATION[3][0], FORMATION[3][1] + 1),
        range(FORMATION[4][0], FORMATION[4][1] + 1),
    ):
        if gk + df + md + fw == 11:
            shapes.append((gk, df, md, fw))
    return shapes


SHAPES = _legal_shapes()


@dataclass
class Eleven:
    """A chosen starting eleven for one gameweek."""

    gameweek: int
    shape: tuple[int, int, int, int]
    starters: list[int]
    bench: list[int]
    captain: int
    vice_captain: int
    expected_points: float

    @property
    def formation(self) -> str:
        _, d, m, f = self.shape
        return f"{d}-{m}-{f}"


def best_eleven(player_ids: list[int], xp: dict[int, float], gw: int,
                players: dict) -> Eleven:
    """The highest-scoring legal eleven, with the captaincy on top of it.

    The captain's points are doubled, so the captain is simply the highest
    projected starter — but the armband is chosen *after* the eleven, because a
    player who does not make the eleven cannot wear it.
    """
    by_pos: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: []}
    for pid in player_ids:
        by_pos[players[pid].position].append(pid)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda pid: -xp.get(pid, 0.0))

    best, best_total, best_shape = None, -1.0, SHAPES[0]
    for shape in SHAPES:
        if any(len(by_pos[pos]) < n for pos, n in zip((1, 2, 3, 4), shape)):
            continue
        picked = [pid for pos, n in zip((1, 2, 3, 4), shape) for pid in by_pos[pos][:n]]
        total = sum(xp.get(pid, 0.0) for pid in picked)
        if total > best_total:
            best, best_total, best_shape = picked, total, shape

    if best is None:                       # squad too broken to field an eleven
        best, best_total, best_shape = player_ids[:11], 0.0, (1, 4, 4, 2)

    starters = sorted(best, key=lambda pid: (players[pid].position, -xp.get(pid, 0.0)))
    ranked = sorted(best, key=lambda pid: -xp.get(pid, 0.0))
    captain = ranked[0]
    vice = ranked[1] if len(ranked) > 1 else captain

    # Bench order matters: automatic substitutions run down it in order.
    bench = sorted(
        (pid for pid in player_ids if pid not in set(best)),
        key=lambda pid: (players[pid].position == 1, -xp.get(pid, 0.0)),
    )

    return Eleven(
        gameweek=gw,
        shape=best_shape,
        starters=starters,
        bench=bench,
        captain=captain,
        vice_captain=vice,
        expected_points=round(best_total + xp.get(captain, 0.0), 2),
    )


#: How much a bench place is worth against a starting place. Not zero, because
#: the bench covers blanks, injuries and rotation, and not one, because it does
#: not play. Named rather than buried in a default so /api/health can report
#: the figure the scores were produced with.
BENCH_WEIGHT = 0.12


def squad_score(player_ids: list[int], projections: dict, horizon: list[int],
                players: dict, bench_weight: float = BENCH_WEIGHT) -> float:
    """What a squad is worth over the horizon.

    The bench is not worthless — it covers blanks, injuries and rotation — but
    it is worth much less than a starter, so it carries a small weight rather
    than none. Without it the optimiser fills the bench with four players on
    zero minutes and calls that efficient.
    """
    total = 0.0
    for gw in horizon:
        xp = {pid: projections[pid].per_gw.get(gw, 0.0)
              for pid in player_ids if pid in projections}
        eleven = best_eleven(player_ids, xp, gw, players)
        total += eleven.expected_points
        total += bench_weight * sum(xp.get(pid, 0.0) for pid in eleven.bench)
    return total
