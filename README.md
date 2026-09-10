# FPL Assistant

Tells you what to do before the next Fantasy Premier League deadline: which
transfers are worth making, who to captain, and whether this is the week to
spend a chip. It shows the arithmetic behind every call, so you can disagree
with it.

**Nothing to install.** No `pip`, no npm, no build step, no API key. If you have
Python 3.10 or newer and a browser, you have everything.

---

## Try it

```bash
git clone https://github.com/MyBROSKICicada3301/fpl-assistant.git
cd fpl-assistant
./start.sh
```

Open <http://127.0.0.1:8765>, put in your team id, press **Get advice**.

Stop it with `./stop.sh`.

### Where do I find my team id?

Log in to the FPL site, click **Points**, and look at the address bar:

```
https://fantasy.premierleague.com/entry/1234567/event/4
                                        ^^^^^^^
```

That number is your team id. It is public, the same one that appears in league
tables, and nothing about your team is stored by this tool.

### Prefer the terminal?

```bash
python3 -m fpl.cli --team 1234567
```

```
──────────────────────────────────────────────────────────────────────
  GAMEWEEK 4   deadline 2026-09-12T12:30:00Z
  planning over gameweeks 4-7  ·  2 free transfer(s)  ·  bank £0.0m
  chips played: Triple Captain (GW3) · still held: Wildcard, Free Hit, Bench Boost
──────────────────────────────────────────────────────────────────────

TRANSFERS
  OUT  Rashford         MID   £7.0m
  IN   Ødegaard         MID   £6.6m   +7.43 pts over the horizon
  OUT  Araujo           DEF   £5.4m
  IN   Calafiori        DEF   £5.7m   +7.15 pts over the horizon

  2 transfer(s), hit 0 pts, net +14.58 pts

  Every option considered:
     transfers    gross   hit      net
             0     0.00     0     0.00
             1     7.43     0     7.43
             2    14.58     0    14.58 <-
             3    18.20     4    14.20

STARTING ELEVEN  (4-4-2, 70.4 xP including the captain)
  captain: Haaland, keep it
```

---

## What it tells you

**Transfers, with the hit priced in.** Every plan from zero transfers upwards is
scored and shown side by side, so you can see the trade rather than take it on
trust. Doing nothing is always one of the options, and it often wins.

**Your eleven and your captain.** All eight legal formations are tried and the
best is kept. It shows the captain you have now next to the one it suggests, so
a recommendation never reads as a claim about your team.

**Old squad and new squad, side by side.** Both scored the same way, neither one
dimmed. You are being asked to judge whether the change is worth making.

**All four chips.** What each would earn if you played it this week, and what it
would earn in each of the next few gameweeks. A chip worth 13 points now still
comes back as "hold" if it is worth 15 in a fortnight.

**A written briefing**, optionally. See below.

---

## Three things it does differently

**A hit is judged over four gameweeks, not one.** Over a single gameweek almost
no 4 point hit ever pays; over a season almost all of them do. Four is roughly
how long a transfer's edge lasts before form and fixtures move on.

**A hit must win by a real margin.** The hit is a certain loss bought with an
estimated gain. Without a margin the tool once recommended paying 4 certain
points for an edge of 0.08, which is noise.

**A move is scored on what it does to your squad, not to the player.** Upgrading
a bench goalkeeper looks like a huge gain on paper and is worth almost nothing
in points, because he never plays.

The full method, with the formulas, is at **/method.html** once the app is
running, or in [`fplweb/method.html`](fplweb/method.html).

---

## Optional: the written briefing

A plain-English summary of the numbers, written by Gemini. It is given the
figures and forbidden from adding anything to them, and every claim carries a
tag naming its source.

You need a free [Google AI Studio](https://aistudio.google.com/apikey) key:

```bash
cp .env.example .env
# put your key in .env, then
./start.sh
```

Without a key everything else works and the briefing panel simply hides itself.

---

## Settings

Everything lives in `.env`, which is gitignored. Copy `.env.example` to start.

| Variable | What it does |
|---|---|
| `GEMINI_API_KEY` | Enables the written briefing. Optional. |
| `GEMINI_MODEL` | Which model writes it. Defaults to `gemini-flash-latest`. |
| `FPL_TEAM_ID` | Your team id, so the CLI needs no arguments and the web form is prefilled. |
| `PORT` | Defaults to 8765. |

Anything already exported in your shell wins over the file.

---

## What it cannot see

- **Team news beyond the injury flags.** A fit player who has quietly lost his
  place still projects as a starter until the minutes data catches up.
- **Anything past the horizon.** Chip values cover the gameweeks in view. Double
  gameweeks are often announced later than this data reflects.
- **Your purchase prices.** Selling price is taken as the current price, because
  FPL only tells the logged-in manager what a player would actually sell for.

Projections are estimates, not forecasts. Nobody scores their expected points.
This is not betting advice.

---

## For developers

Python standard library and vanilla ES modules. No dependencies at all.

```
fpl/          rules, data, projection, squad, advice, chips, engine, cli, server
fplweb/       the page, and method.html
start.sh      run it       stop.sh   stop it
```

The page is a client of a small local JSON API, which is yours to use:

| Route | Returns |
|---|---|
| `GET /api/advice?team=&horizon=&max_transfers=` | the whole report |
| `GET /api/gameweek` | next gameweek, deadline, the rule constants |
| `GET /api/players?search=` | name search |
| `GET /api/coach?team=` | the written briefing |
| `GET /api/health` | cache freshness, what is loaded |
| `GET /api/healthz` | liveness probe |
| `POST /api/refresh` | drop cached data and reload |

Errors all arrive in one envelope with a code you can branch on:

```json
{ "error": { "code": "team_not_found", "message": "...", "status": 404 } }
```

Responses are cached on disk, an hour for the player list and two minutes for
anything manager-specific, so running this does not hammer somebody else's
server.

---

## Not affiliated

Data comes from the public Fantasy Premier League API. This project is not
affiliated with, endorsed by, or connected to the Premier League or Fantasy
Premier League.
