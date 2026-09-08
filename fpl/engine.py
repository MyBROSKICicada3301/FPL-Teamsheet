"""One call that turns a team id or a list of names into complete advice.

The CLI and the HTTP service both want the same thing and should not each
assemble it, so the assembly lives here and they render it differently.
"""

import datetime as dt
from dataclasses import dataclass, field

from . import advice, data, projection, squad as squad_mod
from .rules import Rules


class InputError(ValueError):
    """The caller's input is wrong, with a machine-readable code."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class Context:
    """The game, loaded once."""

    bootstrap: dict
    fixtures: list
    rules: Rules
    next_gw: int
    deadline: str | None
    projections: dict = field(default_factory=dict)
    players: dict = field(default_factory=dict)
    horizon: list[int] = field(default_factory=list)


def next_gameweek(bootstrap: dict) -> tuple[int, str | None]:
    """The gameweek being planned for: the next one that has not kicked off."""
    events = bootstrap.get("events", [])
    for e in events:
        if e.get("is_next"):
            return e["id"], e.get("deadline_time")
    for e in events:
        if not e.get("finished"):
            return e["id"], e.get("deadline_time")
    raise InputError("season_over", "The season has finished; there is no next gameweek.", 409)


def load(horizon_length: int = advice.DEFAULT_HORIZON) -> Context:
    bootstrap = data.bootstrap()
    fixtures = data.fixtures()
    gw, deadline = next_gameweek(bootstrap)
    last = max(e["id"] for e in bootstrap["events"])
    horizon = [g for g in range(gw, gw + horizon_length) if g <= last]

    projections = projection.build(bootstrap, fixtures, horizon)
    return Context(
        bootstrap=bootstrap,
        fixtures=fixtures,
        rules=Rules.from_bootstrap(bootstrap),
        next_gw=gw,
        deadline=deadline,
        projections=projections,
        players={pid: p.player for pid, p in projections.items()},
        horizon=horizon,
    )


def _index_by_name(players: dict) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for pid, p in players.items():
        index.setdefault(p.name.casefold(), []).append(pid)
    return index


def resolve_names(names: list[str], players: dict) -> list[int]:
    """Turn typed names into player ids, saying precisely what went wrong.

    Ambiguity is reported rather than guessed: there are two Amads and picking
    one silently would put the wrong player in somebody's squad.
    """
    index = _index_by_name(players)
    ids, unknown, ambiguous = [], [], []

    for raw in names:
        key = raw.strip().casefold()
        if not key:
            continue
        hits = index.get(key)
        if hits is None:
            hits = [pid for name, pids in index.items() if key in name for pid in pids]
        if not hits:
            unknown.append(raw)
        elif len(hits) > 1:
            ambiguous.append((raw, [players[p] for p in hits[:4]]))
        else:
            ids.append(hits[0])

    if unknown:
        raise InputError(
            "unknown_player",
            "Not found in this season's game: " + ", ".join(unknown),
        )
    if ambiguous:
        detail = "; ".join(
            f"{raw!r} matches " + ", ".join(f"{p.name} ({p.team_short}, {p.position_name})"
                                            for p in opts)
            for raw, opts in ambiguous
        )
        raise InputError("ambiguous_player", f"More than one player matches. {detail}")
    return ids


def squad_from_team_id(team_id: int, ctx: Context) -> squad_mod.Squad:
    """Pull a manager's current squad, bank and banked free transfers."""
    try:
        entry = data.entry(team_id)
    except data.FPLError as e:
        if e.status == 404:
            raise InputError(
                "team_not_found",
                f"No FPL team with id {team_id}. The id is in the URL when you "
                "view your team on the FPL site.",
                404,
            ) from None
        raise

    last_played = entry.get("current_event")
    if not last_played:
        raise InputError(
            "team_not_started",
            f"Team {team_id} has not played a gameweek yet, so it has no squad to read.",
            409,
        )

    picks = data.picks(team_id, last_played)
    hist = data.history(team_id)

    ids = [p["element"] for p in picks["picks"]]
    bank = picks.get("entry_history", {}).get("bank", 0)
    free = advice.free_transfers_from_history(hist, ctx.next_gw, ctx.rules)

    return squad_mod.Squad(player_ids=ids, bank=bank, free_transfers=free)


def advise(sq: squad_mod.Squad, ctx: Context, max_transfers: int = 3,
           hist: dict | None = None, team_id: int | None = None) -> dict:
    """The whole report, as plain data."""
    problems = squad_mod.validate(sq, ctx.players, ctx.rules)
    if problems:
        raise InputError(
            problems[0].code,
            " ".join(p.message for p in problems),
        )

    plans = advice.find_transfers(sq, ctx.projections, ctx.players, ctx.rules,
                                  ctx.horizon, max_transfers)
    chosen = advice.best_plan(plans)

    available, reason = (True, "")
    if hist is not None:
        available, reason = advice.wildcard_available(ctx.bootstrap, hist, ctx.next_gw)
    wc = advice.wildcard_verdict(sq, ctx.projections, ctx.players, ctx.rules,
                                 ctx.horizon, chosen, available, reason)

    after = list(sq.player_ids)
    for m in chosen.moves:
        after.remove(m.out_id)
        after.append(m.in_id)

    xp_now = {pid: ctx.projections[pid].per_gw.get(ctx.next_gw, 0.0) for pid in after}
    eleven = squad_mod.best_eleven(after, xp_now, ctx.next_gw, ctx.players)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        .isoformat().replace("+00:00", "Z"),
        "team_id": team_id,
        "gameweek": ctx.next_gw,
        "deadline": ctx.deadline,
        "horizon": ctx.horizon,
        "free_transfers": sq.free_transfers,
        "bank": sq.bank,
        "squad": [_player_row(pid, ctx) for pid in sq.player_ids],
        "plans": [p.as_dict(ctx.players) for p in plans],
        "recommended": chosen.as_dict(ctx.players),
        "eleven": {
            "formation": eleven.formation,
            "expected_points": eleven.expected_points,
            "starters": [_player_row(pid, ctx) for pid in eleven.starters],
            "bench": [_player_row(pid, ctx) for pid in eleven.bench],
            "captain": _player_row(eleven.captain, ctx),
            "vice_captain": _player_row(eleven.vice_captain, ctx),
        },
        "wildcard": {
            "available": wc.available,
            "reason": wc.reason,
            "recommend": wc.recommend,
            "current_score": wc.current_score,
            "plan_score": wc.plan_score,
            "wildcard_score": wc.wildcard_score,
            "gain_over_plan": wc.gain_over_plan,
            "transfers_needed": wc.transfers_needed,
            "squad": [_player_row(pid, ctx) for pid in wc.squad],
        },
    }


def _player_row(pid: int, ctx: Context) -> dict:
    p = ctx.players[pid]
    proj = ctx.projections[pid]
    fixtures = []
    for gw in ctx.horizon:
        for fx in proj.fixtures.get(gw, []):
            opp = next((t for t in ctx.bootstrap["teams"] if t["id"] == fx.opponent), {})
            fixtures.append({
                "gw": gw,
                "opponent": opp.get("short_name", "?"),
                "home": fx.home,
                "difficulty": fx.difficulty,
            })
    return {
        "id": p.id,
        "name": p.name,
        "team": p.team_short,
        "position": p.position_name,
        "cost": p.cost,
        "status": p.status,
        "news": p.news,
        "availability": round(p.availability, 2),
        "selected_by": p.selected_by,
        "form": p.form,
        "xp_next": round(proj.per_gw.get(ctx.next_gw, 0.0), 2),
        "xp_horizon": round(proj.over(ctx.horizon), 2),
        "fixtures": fixtures,
    }
