# Transfer Room

A single-page web app showing Premier League transfer probabilities, built from
[`docs/DESIGN.md`](docs/DESIGN.md) and [`docs/BACKEND.md`](docs/BACKEND.md), with
[`design/Transfer Room.dc.html`](design/Transfer%20Room.dc.html) as the visual
reference.

## Status in one line

The **UI is finished and working**. The **backend is a deliberate skeleton** —
schema, database layer and a dataset simulator exist, but features, model and
API are not written, so the page runs on fixture files rather than a live
service.

Nothing here needs installing. No `pip`, no Postgres, no Node, no build step —
Python 3 and a browser are the whole toolchain.

---

## What has been done

**The whole page**, all five sections of DESIGN.md §3, in `web/`:

- **§1 Hero** — headline and probability driven by whoever tops the board, with
  the orchestrated load sequence (cutout fade, 120 ms headline stagger, 900 ms
  count-up).
- **§2 Board** — horizontally scrolling snap cards, arrow-key navigation,
  4 px hover lift.
- **§3 Explanation** — half-bleed portrait, factor bars in plain language,
  ordered by contribution, no numbers printed on the bars. Clicking any board
  card swaps this section to that player.
- **§4 Club view** — dropdown with a 180 ms cross-fade, left-aligned rows with
  a probability meter and a tabular value at the right edge.
- **§5 Method** — calibration curve, precision@20 against the naive baseline,
  and the misses, at a 68-character measure.
- Footer, disclaimer, error state, empty state, initials fallback for missing
  photography, and the full type and colour system as tokens.

**The colour ramp** (`web/ramp.js`) — OKLab interpolation with band-holding
behaviour, written from scratch. See the judgement calls below.

**The data layer** in `backend/`:

- `sql/schema.postgres.sql` — the BACKEND.md §3 schema, plus the
  `squad_membership`, `club_finance`, `player_window_features` and `model_run`
  tables §4–§6 need but §3 does not define.
- `sql/schema.sqlite.sql` — a SQLite translation so local dev needs no server.
- `db.py`, `config.py`, `bands.py` — one interface over both databases, and the
  band thresholds in a single place.
- `seed.py` — a simulator producing ~1,800 labelled player-windows across the
  four clubs at a 19% positive rate, with correct observation dates so the
  leak rule is enforceable later.

**Verified in-browser**, not by eye: responsive to 360 px with no horizontal
scroll, contrast measured on the token pairs actually in use, keyboard
reachability, the error state and its recovery. Figures are in
"Verified against the quality floor" below.

---

## What is still to do

In BACKEND.md's own build order. Each depends on the one above it.

| # | Piece | Notes |
|---|---|---|
| 1 | **Feature builder** (§5) | One row per player-window, every feature computed as of `opens_on`, nothing read with `observed_on > opens_on`. The schema already carries the dates that make this enforceable and `seed.py` writes them correctly, so this is a query, not a data-model change. |
| 2 | **Model and evaluation** (§6) | LightGBM, temporal split, isotonic calibration, precision@20 and Brier against the contract-length baseline. Needs `pip`, which this machine does not currently have. |
| 3 | **FastAPI service** (§7) | The five endpoints, Redis cache, the two contract additions listed below. |
| 4 | **Point the page at it** | One attribute: `<html data-api="/api/v1">`. No other frontend change. |
| 5 | **Real data** | Every source is licensed or restricted — see "Why the data is placeholder" below. This is a procurement decision, not a coding one. |

Smaller outstanding items:

- The **empty-club state** is implemented but no longer reachable from the
  fixtures, because all four clubs now have players. To see it, empty the
  `movements` array in `web/fixtures/clubs/4.json`.
- **Photography** is absent by design; every portrait renders the initials
  fallback. `portrait()` in `app.js` already handles real images and falls back
  on load error, so a licensed feed needs no code change.
- `spec-extract/` is the unpacked source zip, left where it was. Safe to delete.

---

## How to start and stop it

```bash
./start.sh
```

```bash
./stop.sh
```

`start.sh` prints the URL — <http://127.0.0.1:8765> — and hands your prompt
back; the server keeps running behind you. Run it again when it is already up
and it just prints the URL, so it doubles as the "is it running?" check.

Details, for when something is off:

- The page has to be **served**. It fetches JSON, and browsers block that on
  `file://`, so opening `index.html` directly gives a blank page.
- It is `python3 -m http.server` on port 8765, serving `web/`. If that port is
  taken, pass another: `PORT=9000 ./start.sh`.
- Requests are logged to `server.log`, which is a useful sign the page is
  loading its fixtures. The process id is in `.server.pid`. Both are ignored
  by git.
- If `.server.pid` is ever lost, find and kill the process by hand:
  `pgrep -af "http.server 8765"`, then `kill <pid>`. By PID rather than
  `pkill -f`, because `-f` matches whole command lines and will also hit any
  other shell that happens to have that string in its arguments — including
  the terminal you typed it in.

### Regenerate the dummy database

Not needed to view the site. This rebuilds `backend/transferroom.db` from
scratch, and is deterministic — the same seed gives the same dataset:

```bash
cd backend && python3 -m transferroom.seed
```

## How the frontend gets its data

`web/api.js` reads a base path off the root element. Unset, it serves the
fixtures in `web/fixtures/`, which are shaped exactly like the responses in
BACKEND.md §7. When the service exists, this becomes a one-line change:

```html
<html lang="en-GB" data-api="/api/v1">
```

No other frontend change is needed.

### Two additions to the documented API contract

DESIGN.md asks for things BACKEND.md §7 does not return. Both are in the
fixtures and will need to exist on the real endpoints:

1. **`/board` players need `destination` and `updated_at`.** §2 puts
   "current club → predicted destination" and a last-updated caption on every
   card, and the documented payload carries neither.
2. **`GET /clubs` needs to exist.** §4's dropdown has to populate from
   something; the documented endpoints only cover a single club's movements.

## Design decisions that needed a judgement call

Three places where following the spec literally produced a worse result. Each
is commented at the point it happens in the code.

### The ramp is banded, not linearly interpolated

DESIGN.md asks for two things that pull against each other: each band means
something specific (`--p-hot` is "50–85% — expect movement"), and "a value
moving from 48% to 52% shifts smoothly rather than snapping".

Interpolating the whole range between band anchors satisfies the second and
destroys the first. Measured from the first implementation: 10% rendered
`#ad7b8b`, a dusty pink; 70% rendered `#816c00`, olive; 84% rendered
`#287a38`, green — a value the server had called `hot`, drawn in the `done`
colour.

`web/ramp.js` instead holds each band's colour across its range and blends only
within ±3 points of a boundary — sized to the 48%/52% example the spec itself
gives. Those two now render `#bb7123` and `#c34e2c`, a clear smooth shift,
while everything from 54% to 81% stays pure `--p-hot` red.

The blend was ±5 first. At that width 84% came out brown-gold rather than the
red its `hot` band calls for, because too much of the band sat inside the
crossing.

### Boundary blends interpolate in OKLab, not OKLCH

For cold→warm and warm→hot the two are nearly identical. At the hot→done
boundary they are not: rotating hue from red to green passes through saturated
yellow, so 86% rendered mustard — the warm band's own colour, on a `done`
value. That breaks the rule the ramp exists to protect. OKLab desaturates
through the crossing instead of borrowing another band's hue.

### Club-row percentages are `--ink`, not ramp-coloured

The design canvas colours them with the ramp. At `--t-body` (17px) they are
body text and need 4.5:1; measured against `--paper`, `--p-cold` is 3.26:1 and
`--p-warm` is 3.63:1, so cold and warm values failed the quality floor.
DESIGN.md §4 describes the row as "a probability bar running the width of the
row with the value at the right edge" and does not colour the value, so the bar
carries the encoding and the numeral stays readable.

## Why the data is placeholder

DESIGN.md §4 and BACKEND.md §10 both land in the same place: press photography
is agency-owned, Transfermarkt's terms rule out commercial reuse, and FBref's
rule out public hosting. So nothing here fabricates claims about a real person.

- **All player names are dummy placeholders.** Ten first names crossed with ten
  surnames, in both the fixtures and `seed.py`. No probability here is attached
  to a real person. Club names are real, but every figure beside them is
  invented — the footer says so.
- **The dataset's shape is real** — observation dates, leak rules, class
  balance — so the pipeline that runs on it is the pipeline that would run on a
  licensed feed.
- **No images.** Every portrait renders the initials fallback DESIGN.md §4
  specifies for missing players. When a licensed feed supplies `image.cutout`
  and `image.square`, `portrait()` in `app.js` uses them and falls back on
  error.

## Verified against the quality floor (§6)

Checked in-browser, not by eye:

- Responsive to 360px with no horizontal scroll; hero drops to 44px, display to
  34px, section padding to 72px.
- Contrast: ink on paper 16.8:1, slate on paper 5.07:1, slate on mist 4.66:1,
  dark-section muted on ink 5.86:1. Ramp bands on `--paper` run 3.26–5.38:1 —
  used only on large readouts (40px cards, 88px hero) where 3:1 applies.
- Colour is never the only encoding; every probability shows its number.
- Arrow keys move between board cards, focus ring visible at 3px offset 2px.
- `prefers-reduced-motion` cuts the count-up and all reveals; the number is
  correct instantly.
- Error state shows "Predictions didn't load. Retry" and recovers on retry
  (tested by stubbing `fetch` to reject).
- Empty club shows "No moves above 20% at this club" — verified while a club
  with no movements was still in the fixtures.

## Layout

```
start.sh / stop.sh    run the site locally
web/                  the site — open this
  index.html          structure, all five sections
  app.css             tokens, type scale, section rhythm
  app.js              hero sequence, board, factors, club view, charts
  ramp.js             the probability colour ramp
  api.js              endpoint map; swaps fixtures for the live API
  fixtures/           API-shaped JSON standing in for the service
backend/
  sql/                Postgres schema + SQLite translation
  transferroom/       db, config, bands, seed  (features/model/api absent)
docs/                 DESIGN.md and BACKEND.md as supplied
design/               the design canvas used as visual reference
.claude/launch.json   lets Claude Code's preview pane start the same server
```
