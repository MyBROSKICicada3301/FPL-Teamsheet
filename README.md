# FPL Assistant

Put in your Fantasy Premier League team id, get back the transfers worth making
for the coming gameweek, the eleven to start, who to captain, and whether this
is the week to play your wildcard.

Everything runs on the public FPL API. No key, no signup, no `pip`, no build
step — Python 3 and a browser are the whole toolchain.

## Start and stop

```bash
./start.sh
```

```bash
./stop.sh
```

`start.sh` prints the URL — <http://127.0.0.1:8765> — and hands your prompt
back. Run it again when it is already up and it just prints the URL, so it also
answers "is it running?".

Or skip the browser:

```bash
python3 -m fpl.cli --team 1234567
```

Your team id is the number in the URL when you view your own team on the FPL
site: `/entry/`**`1234567`**`/event/…`. It is a public id — the same one that
appears in league tables — and nothing about your team is stored here.

## What it actually decides

**Transfers, priced properly.** Every plan from zero transfers upwards is
scored, and the one with the best *net* figure wins:

```
net = points gained over the horizon − 4 × (transfers beyond your free ones)
```

Three things follow from that, and they are the reasons this tends to disagree
with a gut call:

- **A hit is judged over four gameweeks, not one.** Over a single gameweek
  almost no hit ever pays; over a season almost every hit does. Four gameweeks
  is roughly how long a transfer's edge survives before form, fixtures and
  prices move on. Change it with `--horizon`, or the dropdown.
- **Free transfers now roll over, up to five, so an unused one is not wasted.**
  The bar for spending one is higher than it used to be, and "roll it" is a
  recommendation this will make.
- **A move is scored on what it does to your squad, not to the player.**
  Upgrading a bench goalkeeper looks like a big gain on raw projections and is
  worth nearly nothing in points. Candidates are shortlisted on the raw
  difference, then rescored by rebuilding the eleven around the change.

**The eleven and the captain.** There are only eight legal formations, so all
eight are tried and the best is kept. The captain is the highest projected
starter, chosen after the eleven — a player who does not start cannot wear the
armband. The bench comes back in automatic-substitution order.

**The wildcard.** An optimal fifteen is built from scratch against your budget,
then compared **against the best ordinary plan** rather than against standing
still. Almost any wildcard beats standing still; the question is whether it
beats what your free transfers and a sensible hit would have got you anyway.
It has to win by 8 points before the recommendation flips, because the chip is
a one-shot resource and a marginal gain is not a reason to burn it. Whether you
still *have* it is read from your history against the two published windows.

## Where the numbers come from

Expected points per player per gameweek, from the terms of the actual scoring
system:

```
xP = P(appears) × appearance points
   + expected goals   × points per goal for the position
   + expected assists × 3
   + P(clean sheet)   × clean sheet points for the position
   + saves / 3                       (goalkeepers)
   − expected goals conceded / 2     (goalkeepers and defenders)
   + expected bonus
   + P(defensive contribution) × 2
```

Rates come from each player's own per-90 statistics, **shrunk towards the
positional average** by a weight that grows with minutes played. That is what
stops a striker with one goal from a single cameo reading as a 1.0 xG/90
player, and it is the difference between a model that recommends him and one
that does not.

Fixtures are FPL's own 1–5 difficulty ratings, applied to attacking output,
clean-sheet probability and goals conceded, with a small home adjustment.
Blank and double gameweeks fall out for free: a blank is an empty fixture list
and scores zero, a double is two entries and the terms add.

For the immediate gameweek the estimate is blended 65/35 with FPL's own
published `ep_next`, which sees team news this model cannot.

### What it does not know

- **Rotation and team news beyond the flags.** A fit player who has quietly
  lost his place still projects as a starter until the minutes data catches up.
- **Anything tactical.** Fixture difficulty knows Arsenal away is hard. It does
  not know their centre-backs are suspended.
- **Your purchase prices.** Selling price is taken as the current price, so a
  squad sitting on price rises has slightly more money than this assumes. FPL
  only exposes true selling prices to the logged-in manager.

## The rules it plays by

Squad size, the eleven, the three-per-club cap, the £100.0m budget, the 50%
sell-on fee, the five-transfer bank and both wildcard windows are all read from
`bootstrap-static` at runtime — that block *is* the configuration the live game
runs on, so it cannot drift out of date here without drifting in the real game.

The one number the API does not publish is the 4-point charge for a transfer
beyond your free ones. It is pinned as a single named constant in
[`fpl/rules.py`](fpl/rules.py) with its source, and that is the only line to
edit if it ever changes.

Reference: <https://fantasy.premierleague.com/en/help/rules>

## API

The page is a client of a small local service; the same endpoints are yours.

| Route | Returns |
|---|---|
| `GET /api/healthz` | up, current gameweek, whether upstream is reachable |
| `GET /api/gameweek` | next gameweek, deadline, horizon, the rule constants |
| `GET /api/players?search=&limit=` | name search, best projected first |
| `GET /api/advice?team=&horizon=&max_transfers=&free_transfers=` | the whole report |
| `POST /api/advice` | the same, from `{"players": [...15...], "bank": 0, "free_transfers": 1}` |

Failures all arrive in one envelope, with a code you can branch on rather than
prose you have to parse:

```json
{ "error": { "code": "team_not_found",
             "message": "No FPL team with id 999999999. ...",
             "status": 404 } }
```

The full set is declared in `ERROR_CODES` in [`fpl/server.py`](fpl/server.py) —
`invalid_team_id`, `team_not_found`, `team_not_started`, `unknown_player`,
`ambiguous_player`, `squad_size`, `duplicate_player`, `position_quota`,
`club_limit`, `over_budget`, `upstream_error`, `upstream_unreachable`, and the
rest — so the contract is documented rather than discovered.

## Layout

```
start.sh / stop.sh   run it
fpl/
  rules.py           the game's rules, mostly read from the live config
  data.py            FPL API client, disk cache, typed failures
  projection.py      expected points per player per gameweek
  squad.py           legality, best eleven, captaincy
  advice.py          transfers, hit arithmetic, wildcard verdict
  engine.py          assembles one report
  cli.py             terminal front end
  server.py          HTTP service + static files
fplweb/              the page
.cache/fpl/          cached API responses (gitignored)
```

## Caching and manners

`bootstrap-static` is 1.7 MB and this is someone else's server, so responses
are cached on disk — an hour for the player list and fixtures, two minutes for
anything manager-specific. Requests are serial, carry a real user agent, and
back off on 429s and 5xxs. `--refresh` on the CLI clears the cache; the deleted
files come straight back on the next call.

## Not affiliated

Data from the public Fantasy Premier League API. This is not affiliated with or
endorsed by the Premier League. Projections are estimates, not forecasts.
