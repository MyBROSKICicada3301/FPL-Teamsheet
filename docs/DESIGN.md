# Transfer Room — Design Specification

A single-page web app that shows Premier League transfer probabilities.
Visual direction: Apple product-page grammar — full-bleed sections, oversized
type, one subject per screen, scroll as the primary navigation.

---

## 1. Brief

**Subject.** Predicted player movement in and out of the Premier League.

**Audience.** Football fans who follow transfer windows closely. They already
know the players. They do not know how likely each rumour actually is.

**Primary job.** Turn a rumour into a number, and make that number feel
credible enough to argue about.

**The tension to design around.** The product's output is a probability, which
is an abstract thing. The subject matter — players, shirts, crests, stadiums —
is intensely visual and emotional. The design's job is to let the photography
carry the emotion while the numbers stay calm and unhyped. If the interface
gets excited, the predictions stop looking trustworthy.

---

## 2. Design plan

### Colour

Apple's own neutrals, because the brief names Apple directly and these are the
actual values that produce that feel. Everything expressive is carried by one
system: the probability ramp.

| Token | Hex | Role |
|---|---|---|
| `--ink` | `#1D1D1F` | Primary text, dark section backgrounds |
| `--paper` | `#FFFFFF` | Light section backgrounds |
| `--mist` | `#F5F5F7` | Alternate light section, card fills |
| `--slate` | `#6E6E73` | Secondary text, labels, captions |
| `--hairline` | `#D2D2D7` | Dividers, input borders |

**The probability ramp.** This is the one place the design is allowed to have
an opinion. Colour encodes likelihood and nothing else — never decoration,
never brand.

| Band | Hex | Meaning |
|---|---|---|
| `--p-cold` | `#8E8E93` | Under 20% — noise |
| `--p-warm` | `#B8791F` | 20–50% — live but unresolved |
| `--p-hot` | `#C4442E` | 50–85% — expect movement |
| `--p-done` | `#1D7A3E` | Over 85% — effectively agreed |

Interpolate in OKLCH between bands so a value moving from 48% to 52% shifts
smoothly rather than snapping. Never use these four hues for anything else on
the page — the moment red means "error" as well as "likely", the encoding
breaks.

**Dark sections.** `--ink` background, `#F5F5F7` text, `--slate` lifted to
`#98989D` to hold contrast. The probability ramp stays identical; check each
band against `--ink` and lighten `--p-cold` to `#A1A1A6` on dark only.

### Type

One family. Apple's grammar is a single grotesque doing every job through
weight and size, not a display/body pairing.

```
font-family: "SF Pro Display", "Inter Tight", -apple-system,
             BlinkMacSystemFont, "Helvetica Neue", sans-serif;
```

Inter Tight is the closest free substitute for SF Pro Display's tighter
display widths. Load weights 400, 500, 600 only.

Modular scale, ratio 1.25, base 17px:

| Token | Size / line-height | Weight | Tracking | Use |
|---|---|---|---|---|
| `--t-hero` | 88 / 0.95 | 600 | -0.035em | Hero headline |
| `--t-display` | 56 / 1.05 | 600 | -0.03em | Section openers |
| `--t-title` | 32 / 1.15 | 600 | -0.02em | Player names, card heads |
| `--t-lead` | 21 / 1.5 | 400 | -0.01em | Section standfirst |
| `--t-body` | 17 / 1.6 | 400 | 0 | Running text |
| `--t-caption` | 13 / 1.4 | 400 | 0 | Sources, timestamps, notes |

Below 768px: hero drops to 44, display to 34, everything else holds.

**Numerals.** Every figure on the page uses
`font-variant-numeric: tabular-nums;`. Probabilities change on refresh and
digits must not reflow. The big probability readout additionally uses
`font-feature-settings: "ss01";` if SF Pro is available, for the flat-topped
alternate figures.

**Measure.** Body text caps at 68 characters. Hero and display headlines cap
at 14 words, hard — a long headline in this style collapses into a wall.

### Layout

Full-viewport sections stacked vertically, alternating `--paper` / `--mist` /
`--ink`. No sidebar, no tabs, no page routes. One scroll, one story.

Content grid: 12 columns, 1200px max, 24px gutters, 5vw side margin under
1200px. Hero and photography go full-bleed and ignore the grid.

**Alignment.** Hero and section openers are centred — that is the Apple
signature and it earns the drama. Everything with more than two lines of prose
or any tabular data is left-aligned. Do not centre the methodology section; it
will be unreadable.

**Vertical rhythm.** Sections get `padding-block: 120px` desktop, 72px mobile.
This is the single most-broken rule in a build like this: define it once on a
`section` base class and never override it per-section. Adjust the inner
content spacing instead.

---

## 3. Page structure

```
┌──────────────────────────────────────────────────┐
│  ◦ Transfer Room      Players  Clubs  Method     │  sticky, 48px, blurred
├──────────────────────────────────────────────────┤
│                                                  │
│              [ player cutout, 720px tall ]       │
│                                                  │
│            Isak leaves in 73 days.               │  --t-hero, centred
│                    Probably.                     │
│                                                  │
│                    ┌──────┐                      │
│                    │ 68%  │                      │  --t-hero, ramp colour
│                    └──────┘                      │
│         likelihood of a summer exit              │  --t-caption, --slate
│                                                  │
├──────────────────────────────────────────────────┤  §2  --mist
│  Most likely moves this window                   │
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐              │  horizontal scroll
│  │ 91%│ │ 84%│ │ 76%│ │ 71%│ │ 64%│              │
│  │face│ │face│ │face│ │face│ │face│              │
│  └────┘ └────┘ └────┘ └────┘ └────┘              │
├──────────────────────────────────────────────────┤  §3  --ink
│                                                  │
│   [ half-bleed         ]   Why 68%?              │
│   [ player, right-     ]   ▇▇▇▇▇▇▇  contract     │  factor bars
│   [ aligned cutout     ]   ▇▇▇▇     minutes ↓    │
│   [                    ]   ▇▇▇      PSR pressure │
│                            ▁        age          │
├──────────────────────────────────────────────────┤  §4  --paper
│  Club view — Newcastle                           │
│  [ crest ]  4 likely out · 2 likely in           │
│  ── stacked probability rows, left aligned ──    │
├──────────────────────────────────────────────────┤  §5  --mist
│  How we know we're not just guessing             │
│  [ calibration curve ]   [ precision@20 ]        │
│  Plain-language explanation, left aligned, 68ch  │
├──────────────────────────────────────────────────┤
│  Sources · Updated 4 min ago · Not betting advice│
└──────────────────────────────────────────────────┘
```

### §1 Hero

The hero is the whole argument in one screen: a face you recognise, a number
you don't expect, and a sentence that puts them together.

- Player cutout, transparent PNG, 720px tall on desktop, centred, sitting
  behind the type. Type overlaps the player's chest and shoulders, not the
  face.
- Background: `--paper` with a very soft radial wash behind the player,
  `radial-gradient(60% 50% at 50% 45%, #F5F5F7, #FFFFFF)`. Barely visible.
  If it reads as a gradient, it is too strong.
- Headline names a specific player and a specific number of days. Rotate
  weekly to whoever tops the board. Generic headlines ("Predict the window")
  waste the strongest position on the page.
- The percentage is the largest element after the headline and takes its
  colour from the ramp.

**Motion.** One orchestrated page-load sequence, and it is the only
non-user-triggered motion on the site:

1. Player cutout fades in over 600ms, `translateY(12px) → 0`.
2. Headline lines reveal at 120ms stagger.
3. Percentage counts from 0 to its value over 900ms, `ease-out`, tabular
   figures so the box never resizes.

Under `prefers-reduced-motion: reduce`, everything appears at final state with
no count-up. The number must be correct instantly, not animated into place.

### §2 The board

Horizontally scrolling row of player cards. Cards are 260 × 380, radius 18px,
fill `--paper` on the `--mist` section, no border, no shadow. Separation comes
from the fill contrast — an Apple page almost never draws a card border.

Card contents, top to bottom: probability (large, ramp colour), player face
(square crop, 180px, radius 18px matching the card), name, current club →
predicted destination, last-updated caption.

On hover: the card lifts 4px, 200ms. That is the entire hover treatment. No
scale, no shadow bloom, no colour change.

Snap scrolling with `scroll-snap-type: x mandatory`. Keyboard: arrow keys move
between cards, focus ring visible at 3px `--ink` offset 2px.

### §3 The explanation

The section that separates this from a rumour aggregator. Half-bleed player
photograph on the left, factor breakdown on the right.

Factor bars are horizontal, left-aligned, ordered by contribution, labelled in
plain language on the left and unlabelled numerically — the magnitude is the
bar length. Five factors maximum. Positive contributions in `--p-hot`,
negative in `--p-cold`.

Label the factors the way a fan would say them: "Contract runs out in 11
months", "Playing 40% fewer minutes", "Club needs to sell by June 30". Never
"feature importance", never the raw column name, never a SHAP value on screen.

### §4 Club view

A dropdown selects one club; the section swaps content in place. This is a
user-triggered change, so it may animate: 180ms cross-fade, no slide.

Rows are left-aligned, full-width, separated by `--hairline`. Each row: face
thumbnail (40px circle), name, position, probability bar running the width of
the row with the value at the right edge. Tabular numerals hold the right edge
still as you scan down.

### §5 Method

The trust section, and the one that must not look designed. Left-aligned,
68-character measure, `--t-body`, generous line-height. Two small charts:
a calibration curve and a precision@20 figure against the naive baseline.

Charts use `--ink` for the model line and `--slate` dashed for the ideal
diagonal. No fills, no gradients, no axis chrome beyond one tick label at each
end. Include the misses — a methodology section that only shows wins reads as
marketing and undoes the section's purpose.

---

## 4. Photography

This is the part of the design that carries the product, and the part most
likely to cause a legal problem.

**Treatment.** Two crops of every player:

- *Cutout* — background removed, transparent PNG, shoulders-up or waist-up,
  used in §1 and §3. Shoot direction should be consistent; if half the cutouts
  face left and half right, the page looks assembled rather than designed.
- *Square* — 1:1 face crop, 400 × 400 source, used in cards and rows.

Serve AVIF with WebP fallback, three widths (1x/2x/3x), `loading="lazy"` on
everything below the fold, explicit `width`/`height` to prevent layout shift.

**Fallback.** Never show a broken image or a grey silhouette. Missing player →
render the initials in `--t-title` on a `--mist` circle. Around 15% of squad
players will not have a usable image, and it needs to look deliberate.

**Licensing — read this before shipping.** Press photography of Premier League
players is owned by agencies (Getty, PA, Reuters) and is not free to use
because it appears in search results. Public URLs to agency images will also
break without warning. Your options:

1. License a media feed. Sportmonks, SportsDataIO and similar bundle player
   headshots with the data feed, cleared for use in your product. This is the
   correct answer for anything public.
2. Getty Images embed. Free, legal, but forces their player, their frame and
   their attribution — incompatible with this design.
3. Illustrated portraits. A consistent commissioned or generated illustration
   set sidesteps the problem entirely and can become the product's signature.
   Costs more up front, owns the look afterwards.

Club crests are separately trademarked. Use the club colour and name as a
text lockup instead unless you have a licence.

---

## 5. Copy

The interface is quiet so the numbers can be loud. Sentence case throughout.
No all-caps labels, no eyebrow text above headings, no arrows appended to
links.

| Situation | Write | Not |
|---|---|---|
| Hero | "Isak leaves in 73 days. Probably." | "AI-Powered Transfer Intelligence" |
| Board heading | "Most likely moves this window" | "TOP PREDICTIONS" |
| Factor | "Contract runs out in 11 months" | "contract_months_remaining: 11" |
| Low confidence | "Not enough signal yet" | "Insufficient data" |
| Empty club | "No moves above 20% at this club" | "No results found" |
| Load failure | "Predictions didn't load. Retry" | "Oops! Something went wrong" |
| Stale data | "Updated 4 min ago" | "LIVE" |

**The disclaimer.** Footer, `--t-caption`, `--slate`: "Predictions are
statistical estimates for entertainment. Not betting advice." Keep it plain
and keep it visible. Framing this as wagering guidance pulls you into gambling
regulation in the UK and EU, which is not a light-touch regime.

---

## 6. Quality floor

- Responsive to 360px. Hero player scales to 60vh; the board stays
  horizontally scrollable rather than becoming a vertical stack.
- Contrast: all text ≥ 4.5:1, large display ≥ 3:1. `--p-warm` on `--paper`
  measures around 3.9:1 — acceptable for the large percentage readout, not for
  body text. Check every ramp band against both backgrounds.
- Colour is never the only encoding. Every probability shows its number.
- Keyboard reachable throughout, visible focus, logical tab order.
- `prefers-reduced-motion` respected: no count-up, no parallax, no reveals.
- Sticky nav uses `backdrop-filter: blur(20px)` with a `rgba(255,255,255,0.72)`
  fallback fill for browsers without support.
- Target LCP under 2.5s. The hero cutout is the LCP element — preload it,
  don't lazy-load it.

---

## 7. Things to avoid

Specific to this build, because each one is a short walk from where the design
already is:

- **Neon-on-black "sports betting" styling.** The nearest cliché to a football
  prediction product. The whole point of the Apple direction is that it looks
  like an instrument, not a slip.
- **Cards with borders and soft grey shadows.** Apple pages separate with fill
  and space. One radius family (18px) on cards, 8px on inputs, nothing else.
- **Reveal-on-scroll on every section.** One orchestrated moment in the hero.
  After that, sections are simply present when you reach them.
- **Numbered section markers (01 / 02 / 03).** The page is not a sequence.
- **A different accent colour for the brand.** The ramp is the colour system.
  Adding a brand blue breaks the one thing colour is supposed to mean.
- **Precision the model doesn't have.** Show 68%, never 67.8%. A decimal place
  claims accuracy that a model trained on a few thousand transfers cannot
  support.
