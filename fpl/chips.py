"""Which chips you still hold, what each is worth this week, and whether to play it.

Four chips, each once per half of the season. The API publishes both the
windows and your own history, so the inventory is a fact rather than a guess.
The interesting part is the second question: a chip is a one-shot resource, and
its value this week has to be weighed against what it might be worth later.

**What each chip is worth is arithmetic, not opinion.**

    Triple Captain   the captain already counts twice, so the chip adds their
                     projection a third time: worth exactly the captain's xP.
    Bench Boost      the four bench players score instead of sitting out:
                     worth the sum of their projections.
    Free Hit         one gameweek with any squad you like, then yours returns:
                     worth the best possible eleven that week, less your own.
    Wildcard         a permanent rebuild, judged over the whole horizon in
                     advice.wildcard_verdict rather than here.

**Whether to play it is a judgement, and the tool says which part is which.**
The reason to hold a chip is the chance of a better week later, and the honest
position is that this model cannot see one: it projects a few gameweeks, and
the weeks that make these chips famous are double gameweeks announced when cup
fixtures are rearranged, often months ahead of the schedule this reads.

So the recommendation is deliberately conservative and always shows its
working. A chip is suggested only when this gameweek is the best of the ones
in view *and* the value clears a floor set at roughly what a good, ordinary
week returns. Anything else is reported as a hold, with the number, so the
manager can overrule it knowing exactly what they are turning down.
"""

from dataclasses import dataclass, field

from .squad import best_eleven

#: Human names for the API's spellings.
NAMES = {
    "wildcard": "Wildcard",
    "freehit": "Free Hit",
    "bboost": "Bench Boost",
    "3xc": "Triple Captain",
}

#: What each chip does, in one line, for a reader who has not memorised them.
BLURB = {
    "wildcard": "Unlimited transfers this gameweek, and the squad you end with is yours to keep.",
    "freehit": "Any squad you like for one gameweek only. Your own squad returns next week.",
    "bboost": "Your four bench players score as well as your eleven.",
    "3xc": "Your captain scores triple instead of double.",
}

#: Floors, in projected points, below which a chip is not worth spending.
#: These are the return of a good ordinary week rather than a remarkable one,
#: which is the bar a one-shot resource should have to clear.
FLOOR = {
    "3xc": 9.0,      # a captain projected here is a premium in a soft fixture
    "bboost": 18.0,  # four bench players all starting and none of them a punt
    "freehit": 12.0, # a whole eleven's worth of upgrade for one week
}


@dataclass
class Chip:
    """One chip slot: what it is, whether you still have it, what it is worth."""

    code: str
    window: tuple[int, int]
    used_in: int | None = None
    playable: bool = False
    reason: str = ""
    value: float | None = None
    #: Value in each gameweek of the horizon, so a better week ahead is visible.
    by_gameweek: dict[int, float] = field(default_factory=dict)
    recommend: bool = False
    note: str = ""

    @property
    def name(self) -> str:
        return NAMES.get(self.code, self.code)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "blurb": BLURB.get(self.code, ""),
            "window": {"from": self.window[0], "to": self.window[1]},
            "used_in": self.used_in,
            "playable": self.playable,
            "reason": self.reason,
            "value": None if self.value is None else round(self.value, 2),
            "by_gameweek": {str(k): round(v, 2) for k, v in self.by_gameweek.items()},
            "recommend": self.recommend,
            "note": self.note,
        }


def inventory(bootstrap: dict, hist: dict | None, next_gw: int) -> list[Chip]:
    """Every chip slot covering `next_gw`, with whether it has been spent.

    Only the window covering the coming gameweek is reported. The second-half
    copies exist, but telling somebody in September that they have a wildcard
    in January is noise, not information.
    """
    played = {}
    for entry in (hist or {}).get("chips", []):
        played.setdefault(entry["name"], []).append(entry["event"])

    out = []
    for spec in bootstrap.get("chips", []):
        lo, hi = spec["start_event"], spec["stop_event"]
        if not (lo <= next_gw <= hi):
            continue

        used = next((gw for gw in played.get(spec["name"], []) if lo <= gw <= hi), None)
        chip = Chip(code=spec["name"], window=(lo, hi), used_in=used)
        if used is not None:
            chip.playable = False
            chip.reason = f"Played in gameweek {used}."
        else:
            chip.playable = True
            chip.reason = f"Available, window covers gameweeks {lo} to {hi}."
        out.append(chip)

    order = {"wildcard": 0, "freehit": 1, "bboost": 2, "3xc": 3}
    out.sort(key=lambda c: order.get(c.code, 9))
    return out


def _eleven_for(player_ids, projections, players, gw):
    xp = {pid: projections[pid].per_gw.get(gw, 0.0) for pid in player_ids}
    return best_eleven(list(player_ids), xp, gw, players), xp


def doubles(player_ids, projections, gw) -> int:
    """How many of your players have two fixtures in this gameweek.

    The single strongest argument for Bench Boost or Triple Captain, and the
    one thing that turns an ordinary week into the week worth spending on.
    """
    return sum(
        1 for pid in player_ids
        if len(projections[pid].fixtures.get(gw, [])) > 1
    )


def value_chips(chips: list[Chip], squad_ids, projections, players, rules,
                horizon: list[int], budget: int) -> list[Chip]:
    """Fill in what each playable chip is worth, per gameweek of the horizon."""
    from .advice import optimal_squad

    for chip in chips:
        if not chip.playable or chip.code == "wildcard":
            continue

        for gw in horizon:
            eleven, xp = _eleven_for(squad_ids, projections, players, gw)

            if chip.code == "3xc":
                # The armband already doubles; the chip adds one more copy.
                chip.by_gameweek[gw] = xp.get(eleven.captain, 0.0)
            elif chip.code == "bboost":
                chip.by_gameweek[gw] = sum(xp.get(pid, 0.0) for pid in eleven.bench)
            elif chip.code == "freehit":
                draft = optimal_squad(projections, players, rules, [gw], budget)
                best, _ = _eleven_for(draft, projections, players, gw)
                chip.by_gameweek[gw] = max(
                    0.0, best.expected_points - eleven.expected_points
                )

        _judge(chip, horizon, squad_ids, projections)

    return chips


def _judge(chip: Chip, horizon: list[int], squad_ids, projections) -> None:
    """Decide play or hold, and say why in terms the reader can check."""
    if not chip.by_gameweek:
        return

    now = horizon[0]
    chip.value = chip.by_gameweek.get(now, 0.0)
    best_gw = max(chip.by_gameweek, key=lambda g: chip.by_gameweek[g])
    best_value = chip.by_gameweek[best_gw]
    floor = FLOOR.get(chip.code, 0.0)
    doubled = doubles(squad_ids, projections, now)

    if best_gw != now:
        chip.recommend = False
        chip.note = (
            f"Worth {chip.value:.1f} points now, but {best_value:.1f} in "
            f"gameweek {best_gw}. Hold it for the better week."
        )
        return

    if chip.value < floor:
        chip.recommend = False
        chip.note = (
            f"Worth {chip.value:.1f} points this week, under the {floor:.0f} "
            f"a chip should return before it is spent. Hold."
        )
        return

    chip.recommend = True
    chip.note = (
        f"Worth {chip.value:.1f} points this week, the best of gameweeks "
        f"{horizon[0]} to {horizon[-1]} and above the {floor:.0f} floor."
    )
    if doubled:
        chip.note += f" {doubled} of your players have two fixtures this week."


def summary(chips: list[Chip]) -> str:
    """One line for the top of a report."""
    spent = [c for c in chips if c.used_in is not None]
    left = [c for c in chips if c.playable]
    parts = []
    if spent:
        parts.append("played: " + ", ".join(f"{c.name} (GW{c.used_in})" for c in spent))
    parts.append(
        ("still held: " + ", ".join(c.name for c in left)) if left
        else "none left in this half"
    )
    return " · ".join(parts)
