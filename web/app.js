/* Transfer Room — page behaviour.
 *
 * Deliberately small and framework-free. The only orchestrated motion is the
 * hero load sequence (DESIGN.md §1); everything else is either user-triggered
 * or simply present when you reach it.
 */

import { api, USING_FIXTURES } from './api.js';
import { ramp } from './ramp.js';

const $ = (sel) => document.querySelector(sel);

const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ------------------------------------------------------------- formatting */

const pct = (p) => `${Math.round(p * 100)}%`;   // never 67.8%

function initials(name) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join('');
}

function surname(name) {
  const parts = name.split(/\s+/).filter(Boolean);
  return parts[parts.length - 1];
}

function daysUntil(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  return Math.max(0, Math.round(ms / 86_400_000));
}

/** "Updated 4 min ago" — plain and honest, never "LIVE". */
function updatedAgo(iso) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (mins < 1) return 'Updated just now';
  if (mins < 60) return `Updated ${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `Updated ${hrs} hr ago`;
  const days = Math.round(hrs / 24);
  return `Updated ${days} day${days === 1 ? '' : 's'} ago`;
}

/* Missing player → initials on a mist fill. Never a broken image or a grey
   silhouette; around 15% of squad players will not have a usable image. */
function portrait(person, variant) {
  const src = person.image?.[variant === 'hero' ? 'cutout' : 'square'];
  if (src) {
    const img = document.createElement('img');
    img.src = src;
    img.alt = '';
    img.width = variant === 'row' ? 40 : 400;
    img.height = variant === 'row' ? 40 : 400;
    if (variant === 'hero') img.fetchPriority = 'high';   // this is the LCP element
    else img.loading = 'lazy';
    img.addEventListener('error', () => img.replaceWith(monogram(person, variant)));
    return img;
  }
  return monogram(person, variant);
}

function monogram(person, variant) {
  const el = document.createElement('span');
  el.className = `monogram monogram--${variant}`;
  el.setAttribute('aria-hidden', 'true');
  el.textContent = initials(person.name);
  return el;
}

/* ----------------------------------------------------------------- §1 hero */

function renderHero(board) {
  const top = board.players[0];
  if (!top) {
    $('#hero-headline').textContent = 'Not enough signal yet';
    $('#hero-pct').textContent = '';
    return;
  }

  const days = daysUntil(board.window.closes_on);
  const value = Math.round(top.p_exit * 100);

  $('#hero-figure').replaceChildren(portrait(top, 'hero'));

  $('#hero-headline').replaceChildren(
    line(`${surname(top.name)} leaves in ${days} days.`),
    line('Probably.'),
  );

  $('#hero-caption').textContent = `likelihood of a ${board.window.type} exit`;

  const out = $('#hero-pct');
  out.style.setProperty('--ramp', ramp(value));

  // The number must be correct instantly under reduced motion, not animated
  // into place.
  if (REDUCED) { out.textContent = `${value}%`; return; }
  countUp(out, value);
}

function line(text) {
  const span = document.createElement('span');
  span.className = 'hero__line';
  span.textContent = text;
  return span;
}

function countUp(el, target) {
  const paint = (v) => {
    el.textContent = `${v}%`;
    el.style.setProperty('--ramp', ramp(v));
  };

  paint(0);

  // requestAnimationFrame is throttled in background and non-compositing tabs.
  // If the frames never arrive the readout would sit on 0% indefinitely, which
  // is a wrong number rather than an unanimated one — so a guard writes the
  // true value regardless. Cleared when the animation finishes on its own.
  const guard = setTimeout(() => paint(target), 1200);

  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / 900);
    paint(Math.round(target * (1 - Math.pow(1 - t, 3))));   // ease-out
    if (t < 1) requestAnimationFrame(step);
    else clearTimeout(guard);
  };
  requestAnimationFrame(step);
}

/* ---------------------------------------------------------------- §2 board */

function renderBoard(board, onSelect) {
  const scroller = $('#board');
  scroller.replaceChildren(...board.players.map((p) => card(p, onSelect)));
  wireArrowKeys(scroller);
}

function card(p, onSelect) {
  const value = Math.round(p.p_exit * 100);
  const el = document.createElement('a');
  el.className = 'card';
  el.href = '#explain';
  el.setAttribute('role', 'listitem');
  el.style.setProperty('--ramp', ramp(value));
  el.dataset.playerId = p.player_id;

  const route = p.destination
    ? `${p.club.name} → ${p.destination.name}`
    : `${p.club.name} → undecided`;

  el.innerHTML = `
    <p class="card__pct">${value}%</p>
    <span class="card__face"></span>
    <span class="card__body">
      <span class="card__name">${escapeHtml(p.name)}</span>
      <span class="card__route">${escapeHtml(route)}</span>
    </span>
    <span class="card__route">${escapeHtml(updatedAgo(p.updated_at))}</span>`;

  el.querySelector('.card__face').replaceChildren(portrait(p, 'square'));
  el.setAttribute('aria-label',
    `${p.name}, ${value}% likely to leave. ${route}.`);
  el.addEventListener('click', () => onSelect(p.player_id));
  return el;
}

/** Arrow keys move between cards; focus stays visible throughout. */
function wireArrowKeys(scroller) {
  scroller.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const cards = [...scroller.querySelectorAll('.card')];
    const i = cards.indexOf(document.activeElement);
    if (i === -1) return;
    const next = cards[i + (e.key === 'ArrowRight' ? 1 : -1)];
    if (!next) return;
    e.preventDefault();
    next.focus();
    next.scrollIntoView({ block: 'nearest', inline: 'center',
                          behavior: REDUCED ? 'auto' : 'smooth' });
  });
}

/* ---------------------------------------------------------- §3 explanation */

function renderFactors(player) {
  const value = Math.round(player.p_exit * 100);
  $('#explain-heading').textContent = `Why ${value}%?`;
  $('#explain-figure').replaceChildren(portrait(player, 'hero'));

  // Five factors maximum, ordered by contribution.
  const factors = [...player.factors]
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
    .slice(0, 5);

  const max = Math.max(...factors.map((f) => Math.abs(f.contribution)), 0.01);

  $('#explain-standfirst').textContent =
    `${factors.length} things move ${surname(player.name)}’s number. Longer bars ` +
    `contribute more. The figure is the sum of them, not any single one.`;

  $('#factors').replaceChildren(...factors.map((f) => {
    const up = f.contribution >= 0;
    const li = document.createElement('li');
    li.className = 'factor';
    li.innerHTML = `
      <span class="factor__head">
        <span class="factor__label">${escapeHtml(f.label)}</span>
        <span class="factor__dir">${up ? 'raises' : 'lowers'}</span>
      </span>
      <span class="factor__track">
        <span class="factor__bar" style="width:${(Math.abs(f.contribution) / max) * 100}%"></span>
      </span>`;
    // Positive contributions in --p-hot, negative in --p-cold.
    li.style.setProperty('--ramp',
      up ? 'var(--p-hot)' : 'var(--p-cold)');
    return li;
  }));
}

/* ------------------------------------------------------------ §4 club view */

function renderClubRows(data) {
  const rows = $('#club-rows');
  const empty = $('#club-empty');

  $('#club-name').textContent = data.club.name;
  $('#club-summary').textContent =
    `${data.summary.out} likely out · ${data.summary.in} likely in`;

  const movements = [...data.movements].sort((a, b) => b.p_exit - a.p_exit);
  empty.hidden = movements.length > 0;

  rows.replaceChildren(...movements.map((m) => {
    const value = Math.round(m.p_exit * 100);
    const el = document.createElement('div');
    el.className = 'row';
    el.style.setProperty('--ramp', ramp(value));
    el.innerHTML = `
      <span class="row__face"></span>
      <span class="row__name">${escapeHtml(m.name)}</span>
      <span class="row__pos">${escapeHtml(
        m.direction === 'in' ? `${m.position} · incoming` : m.position)}</span>
      <span class="row__meter">
        <span class="row__track"><span class="row__bar" style="width:${value}%"></span></span>
        <span class="row__pct">${value}%</span>
      </span>`;
    el.querySelector('.row__face').replaceChildren(portrait(m, 'row'));
    return el;
  }));
}

/** User-triggered, so it may animate: a 180ms cross-fade, no slide. */
async function swapClub(id) {
  const rows = $('#club-rows');
  if (REDUCED) return renderClubRows(await api.clubMovements(id));

  rows.dataset.fading = 'true';
  const [data] = await Promise.all([
    api.clubMovements(id),
    new Promise((r) => setTimeout(r, 180)),
  ]);
  renderClubRows(data);
  rows.dataset.fading = 'false';
}

/* --------------------------------------------------------------- §5 method */

function renderMethod(m) {
  const prose = m.notes.concat(m.misses ? [m.misses] : [])
    .map((text) => {
      const p = document.createElement('p');
      p.className = 'body';
      p.textContent = text;
      return p;
    });

  const foot = document.createElement('p');
  foot.className = 'caption';
  foot.textContent = m.footnote;
  prose.push(foot);
  $('#method-prose').replaceChildren(...prose);

  drawCalibration(m.calibration);

  $('#precision').replaceChildren(
    precisionRow('Transfer Room', m.precision_at_20.model, m.precision_at_20.n, false),
    precisionRow('Contract-length baseline', m.precision_at_20.baseline, m.precision_at_20.n, true),
  );
  $('#precision-caption').textContent =
    `Precision at ${m.precision_at_20.n}, ${m.precision_at_20.window}.`;
}

function precisionRow(label, hits, n, baseline) {
  const el = document.createElement('div');
  el.className = 'precision__row';
  el.innerHTML = `
    <span class="precision__head"><span>${escapeHtml(label)}</span><b>${hits} / ${n}</b></span>
    <span class="precision__track">
      <span class="precision__bar${baseline ? ' precision__bar--baseline' : ''}"
            style="width:${(hits / n) * 100}%"></span>
    </span>`;
  return el;
}

/* No fills, no gradients, no axis chrome beyond one tick label at each end.
   The misses are part of the point, so the curve is drawn as measured. */
function drawCalibration(points) {
  const svg = $('#calibration');
  const X = (p) => 20 + p * 220;
  const Y = (p) => 180 - p * 160;

  const path = points.map((d) => `${X(d.predicted)},${Y(d.observed)}`).join(' ');

  svg.innerHTML = `
    <line x1="20" y1="180" x2="240" y2="20"
          stroke="var(--muted)" stroke-width="1" stroke-dasharray="4 4"></line>
    <polyline points="${path}" fill="none" stroke="var(--text)" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round"></polyline>
    <line x1="20" y1="180" x2="240" y2="180" stroke="var(--rule)" stroke-width="1"></line>
    <line x1="20" y1="20" x2="20" y2="180" stroke="var(--rule)" stroke-width="1"></line>
    <text x="20" y="196" font-size="10" fill="var(--muted)">0%</text>
    <text x="222" y="196" font-size="10" fill="var(--muted)">100%</text>`;
}

/* ----------------------------------------------------------------- helpers */

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showError(on) {
  $('#hero-error').hidden = !on;
}

/* -------------------------------------------------------------------- boot */

async function boot() {
  showError(false);
  try {
    const board = await api.board();
    renderHero(board);
    renderBoard(board, selectPlayer);

    $('#footer-sources').textContent =
      `${USING_FIXTURES ? 'Dummy data — placeholder players and probabilities' : 'Sources: licensed feed'}` +
      ` · ${updatedAgo(board.computed_at).toLowerCase()} · model ${board.model_version}`;

    await selectPlayer(board.players[0].player_id);
  } catch (err) {
    console.error(err);
    showError(true);
    return;
  }

  try {
    const clubs = await api.clubs();
    const select = $('#club-select');
    select.replaceChildren(...clubs.clubs.map((c) => {
      const o = document.createElement('option');
      o.value = c.id;
      o.textContent = c.name;
      return o;
    }));
    select.addEventListener('change', (e) => swapClub(e.target.value));
    if (clubs.clubs.length) await swapClub(clubs.clubs[0].id);
  } catch (err) {
    console.error(err);
  }

  try {
    renderMethod(await api.method());
  } catch (err) {
    console.error(err);
  }
}

async function selectPlayer(id) {
  try {
    renderFactors(await api.player(id));
  } catch (err) {
    console.error(err);
  }
}

$('#hero-retry').addEventListener('click', boot);
boot();
