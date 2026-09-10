"""What to actually do: transfers, the hit arithmetic, and the wildcard call.

Every recommendation here is the same comparison — how many points does this
change buy me over the next few gameweeks, and what does it cost. The cost side
is the part people get wrong, so it is made explicit everywhere:

- A transfer beyond your free ones costs 4 points, *once*, immediately.
- The gain is spread over however long you keep the player. Judging a hit on
  one gameweek makes almost every hit look bad; judging it over a season makes
  almost every hit look good. The horizon below is four gameweeks by default,
  which is roughly how long a transfer's edge survives before form, fixtures
  and prices move on.
- Free transfers now roll over, up to five. An unused one is not wasted, so
  the bar for spending one is higher than it used to be.

The wildcard is the same sum with the hit set to zero, asked once: if I could
rebuild for free, how much better would I be, and is that gap bigger than what
I could reach through ordinary transfers anyway.
"""

from dataclasses import dataclass, field

from .rules import POINTS_PER_EXTRA_TRANSFER, Rules, money
from .squad import Squad, best_eleven, squad_score

#: Gameweeks a transfer is judged over.
DEFAULT_HORIZON = 4

#: Candidate pool per position, by projected points over the horizon. Beyond
#: this the extra names are players nobody would transfer in, and the search
#: gets slower for nothing.
POOL_PER_POSITION = 60


@dataclass
class Move:
    """One transfer: a player out, a player in, and what it is worth."""

    out_id: int
    in_id: int
    gain: float            # projected points over the horizon, before any hit
    cost: int              # change in bank, tenths; negative means money spent

    def as_dict(self, players) -> dict:
        out, inn = players[self.out_id], players[self.in_id]
        return {
            "out": {"id": out.id, "name": out.name, "team": out.team_short,
                    "position": out.position_name, "cost": out.cost},
            "in": {"id": inn.id, "name": inn.name, "team": inn.team_short,
                   "position": inn.position_name, "cost": inn.cost},
            "gain": round(self.gain, 2),
            "bank_change": self.cost,
        }


@dataclass
class Plan:
    """A set of transfers considered together, with the hit priced in."""

    moves: list[Move] = field(default_factory=list)
    free_transfers: int = 1

    @property
    def transfers(self) -> int:
        return len(self.moves)

    @property
    def hit(self) -> int:
        return POINTS_PER_EXTRA_TRANSFER * max(0, self.transfers - self.free_transfers)

    @property
    def gross_gain(self) -> float:
        return sum(m.gain for m in self.moves)

    @property
    def net_gain(self) -> float:
        return self.gross_gain - self.hit

    def as_dict(self, players) -> dict:
        return {
            "transfers": self.transfers,
            "hit": self.hit,
            "gross_gain": round(self.gross_gain, 2),
            "net_gain": round(self.net_gain, 2),
            "moves": [m.as_dict(players) for m in self.moves],
        }


def _club_counts(ids, players) -> dict[int, int]:
    counts: dict[int, int] = {}
    for pid in ids:
        counts[players[pid].team] = counts.get(players[pid].team, 0) + 1
    return counts


def _pool(projections, horizon, owned: set[int]) -> dict[int, list[int]]:
    """Candidate signings per position, best first, unavailable players dropped."""
    by_pos: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: []}
    for pid, proj in projections.items():
        p = proj.player
        if pid in owned or p.availability <= 0:
            continue
        by_pos[p.position].append(pid)
    for pos, ids in by_pos.items():
        ids.sort(key=lambda pid: -projections[pid].over(horizon))
        by_pos[pos] = ids[:POOL_PER_POSITION]
    return by_pos


#: How many of the best raw candidates get rescored properly. Scoring a move by
#: its effect on the whole squad is the correct measure but costs a squad
#: evaluation each time, so an obviously bad move is filtered on raw
#: projection first and never reaches that stage.
RESCORE_TOP = 40


def find_transfers(squad: Squad, projections: dict, players: dict, rules: Rules,
                   horizon: list[int], max_transfers: int = 3) -> list[Plan]:
    """Best plan at each number of transfers, from doing nothing upwards.

    The search is greedy and sequential: take the best single move, apply it,
    then look again. That is not guaranteed optimal — two mediocre moves can
    combine to fund one great one — but it matches how transfers are actually
    made, and it never proposes a plan whose intermediate state is illegal.

    A move is judged on what it does to the *squad*, not to the player slot.
    The difference is not academic: upgrading a second goalkeeper who never
    starts looks like a large gain on raw projections and is worth almost
    nothing in points, and ranking on the player alone recommends exactly that
    kind of move. Candidates are shortlisted on the raw difference — cheap, and
    a move that loses on raw terms cannot win on squad terms — then the
    shortlist is rescored by rebuilding the eleven around it.
    """
    plans = [Plan(moves=[], free_transfers=squad.free_transfers)]

    current = list(squad.player_ids)
    bank = squad.bank
    used_out: set[int] = set()
    moves: list[Move] = []

    for _ in range(max_transfers):
        owned = set(current)
        pool = _pool(projections, horizon, owned)
        counts = _club_counts(current, players)

        shortlist: list[tuple[float, int, int, int]] = []
        for out_id in current:
            if out_id in used_out:
                continue                      # never sell the same player twice
            out = players[out_id]
            out_value = squad.selling.get(out_id, out.cost)
            out_xp = projections[out_id].over(horizon)

            for in_id in pool[out.position]:
                inn = players[in_id]
                if inn.cost > bank + out_value:
                    continue
                if inn.team != out.team:
                    if counts.get(inn.team, 0) + 1 > rules.team_limit:
                        continue
                raw = projections[in_id].over(horizon) - out_xp
                if raw > 0:
                    shortlist.append((raw, out_id, in_id, out_value - inn.cost))

        shortlist.sort(reverse=True)
        base = squad_score(current, projections, horizon, players)

        best: Move | None = None
        for _raw, out_id, in_id, bank_change in shortlist[:RESCORE_TOP]:
            trial = [pid for pid in current if pid != out_id] + [in_id]
            gain = squad_score(trial, projections, horizon, players) - base
            if best is None or gain > best.gain:
                best = Move(out_id, in_id, gain, bank_change)

        if best is None or best.gain <= 0:
            break

        current.remove(best.out_id)
        current.append(best.in_id)
        bank += best.cost
        used_out.add(best.out_id)
        moves.append(best)
        plans.append(Plan(moves=list(moves), free_transfers=squad.free_transfers))

    return plans


#: A plan that takes a points hit must beat the best hit-free plan by at least
#: this much before it is recommended.
HIT_MARGIN = 2.0


def best_plan(plans: list[Plan], margin: float = HIT_MARGIN) -> Plan:
    """The plan with the most net points, with a thumb on the scale against hits.

    Taking a hit is a *certain* loss of four points bought with an *estimated*
    gain, and the estimate is nowhere near precise enough to justify the trade
    on a hair. Left to bare arithmetic this recommended a hit that won by 0.08
    points — a margin far inside the model's own error, and a recommendation no
    one should act on.

    So a plan carrying a hit has to clear the best hit-free plan by a real
    margin. Doing nothing is always in the list, so a squad with no worthwhile
    move is told to hold rather than pushed into one.
    """
    free = [p for p in plans if p.hit == 0]
    best_free = max(free, key=lambda p: (round(p.net_gain, 3), -p.transfers))

    paid = [p for p in plans if p.hit > 0
            and p.net_gain >= best_free.net_gain + margin]
    if not paid:
        return best_free
    return max(paid, key=lambda p: (round(p.net_gain, 3), -p.transfers))


# ------------------------------------------------------------------ wildcard


def optimal_squad(projections: dict, players: dict, rules: Rules,
                  horizon: list[int], budget: int,
                  passes: int = 6) -> list[int]:
    """The best fifteen that money can buy, by greedy fill then local search.

    Choosing fifteen players under a budget, a per-club cap and position quotas
    is a knapsack problem, and the exact answer needs an integer program. This
    gets close by a route that needs no solver: fill greedily on points per
    million, then repeatedly try every single swap and keep the ones that help.
    Local search on a problem this shaped lands within a point or two of
    optimal, and it can be read.
    """
    horizon_xp = {pid: proj.over(horizon) for pid, proj in projections.items()}
    candidates = [
        pid for pid, proj in projections.items()
        if proj.player.availability > 0 and horizon_xp[pid] > 0
    ]

    # --- greedy fill, on points per million ---------------------------------
    by_pos: dict[int, list[int]] = {1: [], 2: [], 3: [], 4: []}
    for pid in candidates:
        by_pos[players[pid].position].append(pid)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda pid: -horizon_xp[pid] / max(1, players[pid].cost))

    from .rules import SQUAD_QUOTA

    picked: list[int] = []
    spend = 0
    counts: dict[int, int] = {}
    for pos, want in SQUAD_QUOTA.items():
        taken = 0
        for pid in by_pos[pos]:
            if taken == want:
                break
            p = players[pid]
            if counts.get(p.team, 0) >= rules.team_limit:
                continue
            if spend + p.cost > budget:
                continue
            picked.append(pid)
            spend += p.cost
            counts[p.team] = counts.get(p.team, 0) + 1
            taken += 1
        if taken < want:
            # Budget ran out mid-position. Fill with the cheapest legal bodies
            # so the squad is at least complete and the swap phase can work.
            for pid in sorted(by_pos[pos], key=lambda x: players[x].cost):
                if taken == want:
                    break
                if pid in picked or counts.get(players[pid].team, 0) >= rules.team_limit:
                    continue
                picked.append(pid)
                spend += players[pid].cost
                counts[players[pid].team] = counts.get(players[pid].team, 0) + 1
                taken += 1

    # --- local search --------------------------------------------------------
    score = squad_score(picked, projections, horizon, players)
    for _ in range(passes):
        improved = False
        for i, out_id in enumerate(list(picked)):
            out = players[out_id]
            counts = _club_counts(picked, players)
            spend = sum(players[pid].cost for pid in picked)

            for in_id in by_pos[out.position]:
                if in_id in picked:
                    continue
                inn = players[in_id]
                if spend - out.cost + inn.cost > budget:
                    continue
                if inn.team != out.team and counts.get(inn.team, 0) + 1 > rules.team_limit:
                    continue

                trial = list(picked)
                trial[i] = in_id
                trial_score = squad_score(trial, projections, horizon, players)
                if trial_score > score + 1e-9:
                    picked, score, improved = trial, trial_score, True
                    break
        if not improved:
            break

    return picked


@dataclass
class Wildcard:
    """The wildcard verdict, with the arithmetic that produced it."""

    available: bool
    reason: str
    current_score: float
    wildcard_score: float
    plan_score: float
    transfers_needed: int
    recommend: bool
    squad: list[int] = field(default_factory=list)

    @property
    def gain_over_plan(self) -> float:
        return round(self.wildcard_score - self.plan_score, 2)


def wildcard_verdict(squad: Squad, projections: dict, players: dict, rules: Rules,
                     horizon: list[int], plan: Plan, available: bool,
                     reason: str = "", threshold: float = 8.0) -> Wildcard:
    """Is rebuilding from scratch worth more than the best ordinary plan?

    The comparison is against the *best plan*, not against standing still. A
    wildcard that beats doing nothing proves little — almost any wildcard does.
    The question is whether it beats what free transfers and a sensible hit
    would have achieved anyway, by enough to be worth spending the chip.
    """
    budget = squad.budget(players)
    current = squad_score(squad.player_ids, projections, horizon, players)

    after_plan = list(squad.player_ids)
    for m in plan.moves:
        after_plan.remove(m.out_id)
        after_plan.append(m.in_id)
    plan_score = squad_score(after_plan, projections, horizon, players) - plan.hit

    if not available:
        return Wildcard(False, reason or "No wildcard available in this half of the season.",
                        round(current, 2), 0.0, round(plan_score, 2), 0, False)

    draft = optimal_squad(projections, players, rules, horizon, budget)
    wildcard = squad_score(draft, projections, horizon, players)
    changed = len(set(draft) - set(squad.player_ids))

    return Wildcard(
        available=True,
        reason=reason,
        current_score=round(current, 2),
        wildcard_score=round(wildcard, 2),
        plan_score=round(plan_score, 2),
        transfers_needed=changed,
        # A wildcard is a one-shot resource, so a marginal gain is not a reason
        # to burn it. The threshold is the points it must beat the best
        # ordinary plan by before it is worth spending.
        recommend=(wildcard - plan_score) >= threshold,
        squad=draft,
    )


# --------------------------------------------------------- free transfers


def free_transfers_from_history(hist: dict, next_gw: int, rules: Rules) -> int:
    """Work out how many free transfers are banked going into `next_gw`.

    FPL does not publish this directly, so it is reconstructed from the rule:
    you get one a gameweek, unused ones roll over up to the cap, and playing a
    wildcard or free hit resets you to one. Anyone who has been transferring
    every week will be on one either way; the reconstruction matters for
    managers who have been saving.
    """
    events = {e["event"]: e for e in hist.get("current", [])}
    chips = {c["event"]: c["name"] for c in hist.get("chips", [])}
    if not events:
        return 1

    free = 1
    for gw in sorted(events):
        if gw >= next_gw:
            break
        made = events[gw].get("event_transfers", 0)
        if chips.get(gw) in ("wildcard", "freehit"):
            free = 1
            continue
        free = min(rules.max_free_transfers, max(1, free - made + 1))
    return max(1, min(rules.max_free_transfers, free))


def wildcard_available(bootstrap: dict, hist: dict, next_gw: int) -> tuple[bool, str]:
    """Whether a wildcard can be played in `next_gw`, and why not if it cannot.

    The chip comes in two halves with their own windows, both published in
    `bootstrap-static`. A wildcard already played in this half is gone.
    """
    windows = [c for c in bootstrap.get("chips", []) if c["name"] == "wildcard"]
    window = next((c for c in windows
                   if c["start_event"] <= next_gw <= c["stop_event"]), None)
    if window is None:
        return False, f"No wildcard window covers gameweek {next_gw}."

    used = [c for c in hist.get("chips", []) if c["name"] == "wildcard"
            and window["start_event"] <= c["event"] <= window["stop_event"]]
    if used:
        return False, (f"Wildcard already played in gameweek {used[0]['event']}, "
                       f"which is inside this window "
                       f"({window['start_event']}-{window['stop_event']}).")

    return True, (f"Available, window covers gameweeks "
                  f"{window['start_event']}-{window['stop_event']}.")
