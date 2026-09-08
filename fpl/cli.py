"""Terminal front end.

    python3 -m fpl.cli --team 1234567
    python3 -m fpl.cli --players "Raya,Gabriel,Saka,..."   (15 names)
"""

import argparse
import json
import sys

from . import advice, data, engine, squad as squad_mod
from .rules import money

BAR = "─" * 74


def _fixture_str(row: dict, gw: int) -> str:
    fx = [f for f in row["fixtures"] if f["gw"] == gw]
    if not fx:
        return "blank"
    return " ".join(
        f"{f['opponent']}{'(H)' if f['home'] else '(A)'}" for f in fx
    )


def _table(rows: list[dict], gw: int, mark: dict[int, str] | None = None) -> None:
    mark = mark or {}
    print(f"  {'':2}{'player':16}{'pos':5}{'club':6}{'price':>7}{'xP':>7}"
          f"{'xP4':>7}  fixture")
    for r in rows:
        flag = mark.get(r["id"], "")
        warn = " !" if r["availability"] < 1 else ""
        print(f"  {flag:2}{r['name'][:15]:16}{r['position']:5}{r['team']:6}"
              f"{money(r['cost']):>7}{r['xp_next']:>7.2f}{r['xp_horizon']:>7.2f}"
              f"  {_fixture_str(r, gw)}{warn}")


def report(result: dict) -> None:
    gw = result["gameweek"]
    print(BAR)
    print(f"  GAMEWEEK {gw}   deadline {result['deadline']}")
    print(f"  planning over gameweeks {result['horizon'][0]}-{result['horizon'][-1]}"
          f"  ·  {result['free_transfers']} free transfer(s)"
          f"  ·  bank {money(result['bank'])}")
    print(BAR)

    rec = result["recommended"]
    print("\nTRANSFERS")
    if not rec["moves"]:
        print("  Hold. No move gains more than it costs over the horizon.")
    else:
        for m in rec["moves"]:
            print(f"  OUT  {m['out']['name']:16} {m['out']['position']:4}"
                  f"{money(m['out']['cost']):>7}")
            print(f"  IN   {m['in']['name']:16} {m['in']['position']:4}"
                  f"{money(m['in']['cost']):>7}   +{m['gain']:.2f} pts over the horizon")
        print(f"\n  {rec['transfers']} transfer(s), hit {rec['hit']} pts, "
              f"net +{rec['net_gain']:.2f} pts")

    print("\n  Every option considered:")
    print(f"    {'transfers':>10}{'gross':>9}{'hit':>6}{'net':>9}")
    for p in result["plans"]:
        star = " <-" if p["transfers"] == rec["transfers"] else ""
        print(f"    {p['transfers']:>10}{p['gross_gain']:>9.2f}"
              f"{p['hit']:>6}{p['net_gain']:>9.2f}{star}")

    e = result["eleven"]
    print(f"\nSTARTING ELEVEN  ({e['formation']}, {e['expected_points']:.1f} xP "
          f"including the captain)")
    _table(e["starters"], gw, {e["captain"]["id"]: "C", e["vice_captain"]["id"]: "V"})
    print("\n  Bench, in substitution order:")
    _table(e["bench"], gw)

    w = result["wildcard"]
    print("\nWILDCARD")
    if not w["available"]:
        print(f"  Not available. {w['reason']}")
    else:
        print(f"  {w['reason']}")
        print(f"  current squad      {w['current_score']:.1f} pts over the horizon")
        print(f"  after the transfers {w['plan_score']:.1f}")
        print(f"  on a wildcard      {w['wildcard_score']:.1f}"
              f"  ({w['transfers_needed']} players change)")
        if w["recommend"]:
            print(f"\n  PLAY IT — worth {w['gain_over_plan']:+.1f} pts more than "
                  "the best ordinary plan.")
            _table(w["squad"], gw)
        else:
            print(f"\n  HOLD IT — only {w['gain_over_plan']:+.1f} pts better than "
                  "transfers you can make anyway.")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m fpl.cli",
        description="Transfer, captain and wildcard advice for the next gameweek.",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--team", type=int, metavar="ID",
                     help="your FPL team id (the number in the URL of your team page)")
    src.add_argument("--players", metavar="NAMES",
                     help="15 comma-separated player names, instead of a team id")
    ap.add_argument("--bank", type=float, default=0.0, metavar="M",
                    help="money in the bank, in millions (with --players)")
    ap.add_argument("--free-transfers", type=int, default=None, metavar="N",
                    help="override the number of free transfers")
    ap.add_argument("--horizon", type=int, default=advice.DEFAULT_HORIZON,
                    metavar="N", help="gameweeks to plan over (default 4)")
    ap.add_argument("--max-transfers", type=int, default=3, metavar="N",
                    help="most transfers to consider in one plan (default 3)")
    ap.add_argument("--json", action="store_true", help="print the raw report")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache")
    args = ap.parse_args(argv)

    if args.refresh:
        data.clear_cache()

    try:
        ctx = engine.load(args.horizon)

        hist = None
        if args.team:
            sq = engine.squad_from_team_id(args.team, ctx)
            hist = data.history(args.team)
        else:
            names = [n for n in args.players.split(",") if n.strip()]
            ids = engine.resolve_names(names, ctx.players)
            sq = squad_mod.Squad(ids, bank=int(round(args.bank * 10)), free_transfers=1)

        if args.free_transfers is not None:
            sq.free_transfers = max(0, min(ctx.rules.max_free_transfers,
                                           args.free_transfers))

        result = engine.advise(sq, ctx, args.max_transfers, hist, args.team)
    except engine.InputError as e:
        print(f"error [{e.code}]: {e}", file=sys.stderr)
        return 2
    except data.FPLError as e:
        print(f"error [{e.code}]: {e}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
