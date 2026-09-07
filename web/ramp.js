/* The probability ramp.
 *
 * DESIGN.md §2: colour encodes likelihood and nothing else, and values are
 * interpolated in OKLCH between bands so a value moving from 48% to 52% shifts
 * smoothly rather than snapping.
 *
 * The interpolation is done here in JS rather than with color-mix() so the
 * result is a real hex we can also measure contrast against.
 */

/* Band colours hold across the band, with a short blend across each boundary.
 *
 * DESIGN.md asks for two things at once: each band means something specific
 * ("50–85% — expect movement" is red), and a value moving from 48% to 52%
 * shifts smoothly rather than snapping. Interpolating the whole range between
 * band anchors satisfies the second and destroys the first — it renders 70% as
 * olive and 84% as green, so the colour stops agreeing with the band the
 * server assigned. Blending only within ±BLEND of a boundary satisfies both.
 */
const BANDS = [
  { upTo: 20,  hex: '#8E8E93' },   // cold — noise
  { upTo: 50,  hex: '#B8791F' },   // warm — live but unresolved
  { upTo: 85,  hex: '#C4442E' },   // hot  — expect movement
  { upTo: 101, hex: '#1D7A3E' },   // done — effectively agreed
];

// On --ink, --p-cold is lifted to hold contrast. Every other band is unchanged.
const BANDS_DARK = BANDS.map((b, i) => (i === 0 ? { ...b, hex: '#A1A1A6' } : b));

/* Half-width, in percentage points, of the blend either side of a boundary.
 * Sized to the example DESIGN.md gives: 48% and 52% should differ visibly, and
 * with ±3 they sit near either end of the crossing. Wider than this and too
 * much of each band renders as the muddy midpoint between two hues — at ±5,
 * 84% came out brown-gold rather than the red its `hot` band calls for. */
const BLEND = 3;

const clamp01 = (x) => Math.min(1, Math.max(0, x));

function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function linearToSrgb(c) {
  return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}

function hexToOklab(hex) {
  const n = parseInt(hex.slice(1), 16);
  const r = srgbToLinear(((n >> 16) & 255) / 255);
  const g = srgbToLinear(((n >> 8) & 255) / 255);
  const b = srgbToLinear((n & 255) / 255);

  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);

  return {
    L: 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
    a: 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
    b: 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
  };
}

function oklabToHex({ L, a, b }) {
  const l = Math.pow(L + 0.3963377774 * a + 0.2158037573 * b, 3);
  const m = Math.pow(L - 0.1055613458 * a - 0.0638541728 * b, 3);
  const s = Math.pow(L - 0.0894841775 * a - 1.2914855480 * b, 3);

  const rgb = [
     4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ].map((c) => Math.round(clamp01(linearToSrgb(c)) * 255));

  return '#' + rgb.map((c) => c.toString(16).padStart(2, '0')).join('');
}

/**
 * Colour for a probability expressed 0–100.
 * @param {number} pct
 * @param {{dark?: boolean}} [opts]
 * @returns {string} hex
 */
export function ramp(pct, opts = {}) {
  const bands = opts.dark ? BANDS_DARK : BANDS;
  const v = Math.min(100, Math.max(0, Number(pct) || 0));

  const index = bands.findIndex((b) => v < b.upTo);
  const i = index === -1 ? bands.length - 1 : index;

  // Inside a boundary's blend zone, mix the two bands that meet there.
  const lower = i > 0 ? bands[i - 1].upTo : null;          // boundary below
  const upper = i < bands.length - 1 ? bands[i].upTo : null; // boundary above

  if (lower !== null && v < lower + BLEND) {
    return mix(bands[i - 1].hex, bands[i].hex, (v - (lower - BLEND)) / (2 * BLEND));
  }
  if (upper !== null && v > upper - BLEND) {
    return mix(bands[i].hex, bands[i + 1].hex, (v - (upper - BLEND)) / (2 * BLEND));
  }
  return bands[i].hex;
}

/* Mix two band colours in OKLab.
 *
 * DESIGN.md says OKLCH, and for the cold→warm and warm→hot boundaries the two
 * are near-identical. At the hot→done boundary they are not: rotating hue from
 * red to green passes through saturated yellow, so an 86% would render mustard
 * — the warm band's own colour, on a value the server called `done`. That
 * breaks the rule the ramp exists to protect, that a hue means exactly one
 * thing. Interpolating in OKLab instead desaturates through the crossing
 * rather than borrowing another band's hue.
 */
function mix(fromHex, toHex, t) {
  const u = Math.min(1, Math.max(0, t));
  const A = hexToOklab(fromHex);
  const B = hexToOklab(toHex);
  return oklabToHex({
    L: A.L + (B.L - A.L) * u,
    a: A.a + (B.a - A.a) * u,
    b: A.b + (B.b - A.b) * u,
  });
}

/** Band name. Kept in sync with the server, which is the authority. */
export function bandOf(pct) {
  if (pct < 20) return 'cold';
  if (pct < 50) return 'warm';
  if (pct < 85) return 'hot';
  return 'done';
}
