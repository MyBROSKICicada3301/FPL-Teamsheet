/* FPL Teamsheet, page behaviour.
 *
 * Framework-free: one form, one fetch, and a set of renderers. The layout is
 * the Modernist canvas, so the work here is mostly turning API rows into the
 * shapes that design draws, and keeping the two views of the eleven in step.
 *
 * Every failure path goes through an error renderer that reads the service's
 * envelope, {error:{code,message,status}}, and shows both. The code is what
 * tells you which thing to change.
 */

const $ = (sel) => document.querySelector(sel);
const money = (tenths) => `£${(tenths / 10).toFixed(1)}m`;
const signed = (n) => `${n >= 0 ? '+' : '−'}${Math.abs(n).toFixed(2)}`;

let COACH = { available: false };
let LAST_QUERY = null;
let VIEW = 'pitch';

const POSITION_ROWS = [
  ['GKP', 'GOALKEEPER'],
  ['DEF', 'DEFENCE'],
  ['MID', 'MIDFIELD'],
  ['FWD', 'ATTACK'],
];

/* The button reports its own state rather than disappearing.
 *
 * "Written the briefing" is a statement of completion, not an action, so the
 * button is disabled in that state: leaving it clickable invites a second
 * generation that spends upstream quota to produce the same paragraphs. Asking
 * for fresh advice resets it, which is where a regenerated briefing belongs.
 */
const COACH_LABELS = {
  idle: 'Write the briefing',
  working: 'Writing the briefing',
  done: 'Written the briefing',
  failed: 'Try again',
};

const FDR_TEXT = {
  1: 'Easiest fixture on the scale',
  2: 'Favourable',
  3: 'Even',
  4: 'Hard',
  5: 'Hardest fixture on the scale',
};

/* ------------------------------------------------------------------ fetch */

async function api(path, options) {
  const res = await fetch(path, options);
  let payload = null;
  try {
    payload = await res.json();
  } catch {
    throw { code: 'bad_response', message: `The service returned ${res.status}.` };
  }
  if (!res.ok) throw payload.error ?? { code: 'unknown', message: `HTTP ${res.status}` };
  return payload;
}

/* --------------------------------------------------------------- gameweek */

function countdown(deadlineIso) {
  const tick = () => {
    const ms = new Date(deadlineIso).getTime() - Date.now();
    if (ms <= 0) { $('#gw-countdown').textContent = 'CLOSED'; return; }
    const d = Math.floor(ms / 86400000);
    const h = Math.floor((ms % 86400000) / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    $('#gw-countdown').textContent = d ? `${d}d ${h}h` : `${h}h ${m}m`;
  };
  tick();
  setInterval(tick, 30000);
}

async function loadGameweek() {
  try {
    const gw = await api('/api/gameweek');
    // Two digits so the hero numeral keeps its width from GW1 to GW38.
    $('#gw-number').textContent = String(gw.gameweek).padStart(2, '0');
    $('#gw-deadline').textContent = new Date(gw.deadline)
      .toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
      .toUpperCase();
    $('#hit-cost').textContent = gw.rules.hit;
    $('#band').hidden = false;
    countdown(gw.deadline);

    COACH = gw.coach || { available: false };
    const field = $('#team');
    if (!field.value && gw.default_team) field.value = gw.default_team;
  } catch (err) {
    showError(err);
  }
}

/* ----------------------------------------------------------------- states */

function showError(err) {
  $('#results').hidden = true;
  $('#state').innerHTML = `
    <div class="state state--error">
      <h3>That did not work</h3>
      <p class="muted" style="margin:0">${escapeHtml(err.message || 'Unknown error.')}</p>
      <p style="margin:6px 0 0"><code>${escapeHtml(err.code || 'unknown')}</code></p>
    </div>`;
}

function showBusy(message) {
  $('#results').hidden = true;
  $('#state').innerHTML = `<div class="state"><h3>${escapeHtml(message)}</h3>
    <p class="muted" style="margin:4px 0 0">Reading your squad and projecting every player.</p></div>`;
}

const clearState = () => { $('#state').innerHTML = ''; };

/* ---------------------------------------------------------------- pieces */

function fixtureChips(row) {
  if (!row.fixtures.length) {
    return `<span class="fdr fdr--none" title="No fixture in this gameweek">NONE</span>`;
  }
  return row.fixtures.map((f) => {
    // Away trips carry a lower-case suffix, so venue survives without colour.
    const label = f.home ? escapeHtml(f.opponent) : `${escapeHtml(f.opponent)}<span style="text-transform:lowercase">a</span>`;
    const title = `GW${f.gw}, ${f.home ? 'home' : 'away'} against ${f.opponent}, difficulty ${f.difficulty} of 5`;
    return `<span class="fdr fdr-${f.difficulty}" title="${escapeHtml(title)}">${label}</span>`;
  }).join('');
}

function badgeFor(row, eleven) {
  if (row.id === eleven.captain.id) return '<span class="pcard__badge">C</span>';
  if (row.id === eleven.vice_captain.id) return '<span class="pcard__badge pcard__badge--v">V</span>';
  if (row.availability < 1) {
    return `<span class="pcard__badge pcard__badge--flag" title="${escapeHtml(row.news || 'Doubtful')}">!</span>`;
  }
  return '';
}

function playerCard(row, eleven, gw) {
  return `
    <div class="pcard">
      <div class="pcard__top">
        <span class="pcard__own" title="Selected by ${row.selected_by}% of managers">${row.selected_by}%</span>
        ${badgeFor(row, eleven)}
      </div>
      <div class="pcard__name">${escapeHtml(row.name)}</div>
      <div class="pcard__meta">${escapeHtml(row.team)} · ${money(row.cost)}</div>
      <div class="pcard__xp">
        <span class="fig">${row.xp_next.toFixed(1)}</span>
        <span>XP GW${gw}</span>
      </div>
      <div class="strip">${fixtureChips(row)}</div>
    </div>`;
}

/* ------------------------------------------------------------- renderers */

function renderBand(d) {
  $('#band-ft').textContent = d.free_transfers;
  $('#band-bank').textContent = money(d.bank);
}

function renderTransfers(d) {
  const rec = d.recommended;
  const chips = (d.chips_used || []).map((c) => `${c.name} (GW${c.gameweek})`).join(', ');

  $('#transfers-hint').textContent =
    `Judged over gameweeks ${d.horizon[0]} to ${d.horizon[d.horizon.length - 1]}`
    + (chips ? `. Chips played: ${chips}` : '');

  $('#moves').innerHTML = rec.moves.length
    ? rec.moves.map((m) => `
        <div class="move-grid">
          <div class="move-card">
            <div class="role">OUT</div>
            <div class="who">${escapeHtml(m.out.name)}</div>
            <div class="meta">${escapeHtml(m.out.position)} · ${escapeHtml(m.out.team)}</div>
            <div class="fig">${money(m.out.cost)}</div>
          </div>
          <div class="move-arrow">→</div>
          <div class="move-card move-card--in">
            <div class="role">IN</div>
            <div class="who">${escapeHtml(m.in.name)}</div>
            <div class="meta">${escapeHtml(m.in.position)} · ${escapeHtml(m.in.team)}</div>
            <div style="display:flex; align-items:baseline; gap:10px; margin-top:8px">
              <span class="fig" style="font-size:22px">${money(m.in.cost)}</span>
              <span class="gain">${signed(m.gain)}</span>
            </div>
          </div>
        </div>`).join('')
    : `<p class="muted" style="margin:0 0 6px">No move gains more than it costs. Roll the transfer.</p>`;

  $('#totals').innerHTML = `
    <div>
      <div class="kicker">Transfers</div>
      <div class="fig">${rec.transfers}</div>
    </div>
    <div>
      <div class="kicker">Points hit</div>
      <div class="fig">${rec.hit ? '−' + rec.hit : '0'}</div>
    </div>
    <div>
      <div class="kicker">Net over ${d.horizon.length} GW</div>
      <div class="fig fig--accent">${signed(rec.net_gain)}</div>
    </div>`;

  $('#options tbody').innerHTML = d.plans.map((p) => `
    <tr class="${p.transfers === rec.transfers ? 'pick' : ''}">
      <td style="font-family:var(--font-heading); font-weight:800; font-stretch:75%; font-size:16px; font-variant-numeric:tabular-nums">${p.transfers}</td>
      <td class="n">${p.gross_gain.toFixed(2)}</td>
      <td class="n">${p.hit ? '−' + p.hit : '0'}</td>
      <td class="n" style="font-family:var(--font-heading); font-weight:900; font-stretch:75%; font-size:16px">${signed(p.net_gain)}</td>
    </tr>`).join('');

  // Say why the runner-up lost, which is the whole argument of the table.
  //
  // The runner-up can score HIGHER on paper and still lose: a plan carrying a
  // points hit has to clear the best free plan by a real margin, because the
  // hit is certain and the gain is an estimate. Reporting that case as "beats
  // it by -0.08" is nonsense, so the two cases are worded separately.
  const best = d.plans.find((p) => p.transfers === rec.transfers);
  const rival = d.plans
    .filter((p) => p.transfers !== rec.transfers)
    .sort((a, b) => b.net_gain - a.net_gain)[0];

  const noun = (n) => `${n} ${n === 1 ? 'move' : 'moves'}`;
  let note = '';
  if (rival) {
    const margin = Math.abs(best.net_gain - rival.net_gain).toFixed(2);
    note = best.net_gain >= rival.net_gain
      ? `${noun(best.transfers)} beats ${noun(rival.transfers)} by ${margin} points`
        + (rival.hit ? `, once the ${rival.hit} point hit is paid.` : '.')
      : `${noun(rival.transfers)} scores ${margin} more on paper, but pays a `
        + `certain ${rival.hit} point hit for it. Too close to buy, so the `
        + `free ${noun(best.transfers)} wins.`;
  }
  $('#options-note').textContent = note;
}

function renderEleven(d) {
  const e = d.eleven;
  $('#formation').textContent = e.formation;
  $('#xp-head').textContent = `xP GW${d.gameweek}`;

  const now = d.current_captain;
  const armband = !now
    ? `Captain ${escapeHtml(e.captain.name)}.`
    : e.captain_changes
      ? `Captain <b>${escapeHtml(now.name)}</b> now, change to <b>${escapeHtml(e.captain.name)}</b>.`
      : `Captain <b>${escapeHtml(now.name)}</b>, keep it.`;
  $('#eleven-hint').innerHTML =
    `${armband} ${e.expected_points.toFixed(1)} expected points in GW${d.gameweek}, `
    + `captain doubled. Bench is in automatic-substitution order.`;

  // Pitch
  const rows = POSITION_ROWS.map(([code, label]) => {
    const players = e.starters.filter((p) => p.position === code);
    if (!players.length) return '';
    return `
      <div class="pitch__row">
        <div class="pitch__label">${label}</div>
        <div class="pitch__players">
          ${players.map((p) => playerCard(p, e, d.gameweek)).join('')}
        </div>
      </div>`;
  }).join('');

  $('#pitch').innerHTML = `
    <div class="pitch__box"></div>
    ${rows}
    <div class="bench">
      <span class="pitch__label" style="writing-mode:horizontal-tb">BENCH</span>
      ${e.bench.map((b, i) => `
        <span class="bench__pill">${i + 1} ${escapeHtml(b.name)}
          <span>${escapeHtml(b.team)}</span></span>`).join('')}
    </div>`;

  $('#legend').innerHTML = [1, 2, 3, 4, 5].map((n) => `
    <div>
      <span class="fdr fdr-${n}">${n}</span>
      <span class="muted" style="font-size:12px">${FDR_TEXT[n]}</span>
    </div>`).join('');
  $('#legend-note').textContent =
    `${d.horizon.length} fixtures per player, left to right from GW${d.horizon[0]}. `
    + `A lower-case a marks an away trip.`;

  // Table
  $('#eleven tbody').innerHTML =
    e.starters.map((p) => tableRow(p, e)).join('')
    + `<tr><th colspan="7">Bench, in substitution order</th></tr>`
    + e.bench.map((p) => tableRow(p, e)).join('');
}

function tableRow(row, eleven) {
  const badge = row.id === eleven.captain.id ? ' C'
    : row.id === eleven.vice_captain.id ? ' V' : '';
  const flag = row.availability < 1
    ? ` <span style="color:var(--color-accent-700)" title="${escapeHtml(row.news || 'Doubtful')}">!</span>` : '';
  return `<tr>
    <td><span style="font-family:var(--font-heading); font-weight:800; font-stretch:80%; text-transform:uppercase; font-size:14px">${escapeHtml(row.name)}</span><span style="font-size:10px; font-weight:700; letter-spacing:0.1em; margin-left:6px; color:var(--color-accent-700)">${badge}</span>${flag}</td>
    <td style="font-size:12px; letter-spacing:0.06em">${escapeHtml(row.position)}</td>
    <td style="font-size:12px; letter-spacing:0.06em">${escapeHtml(row.team)}</td>
    <td class="n">${money(row.cost)}</td>
    <td class="n">${row.xp_next.toFixed(2)}</td>
    <td class="n">${row.xp_horizon.toFixed(2)}</td>
    <td><span class="strip" style="max-width:170px">${fixtureChips(row)}</span></td>
  </tr>`;
}

/* Both squads, side by side and at equal weight.
 *
 * The point of this view is that the reader can disagree. Showing only the
 * improved eleven asks them to take the improvement on trust; showing the
 * current one beside it, scored the same way, lets them see what is being
 * given up as well as what is gained. So the current squad is not dimmed or
 * struck through, and only the rows that genuinely differ are marked.
 */
function lineup(eleven, changed, mark) {
  const row = (p) => {
    const isChanged = changed.has(p.id);
    const cls = isChanged ? ` lineup__row--${mark === 'OUT' ? 'gone' : 'new'}` : '';
    let tag = '';
    if (isChanged) {
      tag = `<span class="lineup__mark lineup__mark--${mark === 'OUT' ? 'out' : 'in'}">${mark}</span>`;
    } else if (p.id === eleven.captain.id) {
      tag = '<span class="lineup__mark lineup__mark--cap">C</span>';
    } else if (p.id === eleven.vice_captain.id) {
      tag = '<span class="lineup__mark lineup__mark--cap">V</span>';
    }
    return `
      <div class="lineup__row${cls}">
        <span class="lineup__pos">${escapeHtml(p.position)}</span>
        <span class="lineup__name">${escapeHtml(p.name)}${tag}
          <span class="lineup__pos" style="text-transform:none"> ${escapeHtml(p.team)} · ${money(p.cost)}</span></span>
        <span class="lineup__xp">${p.xp_next.toFixed(2)}</span>
      </div>`;
  };

  return `
    <div class="lineup">${eleven.starters.map((p) => row(p)).join('')}</div>
    <div class="lineup__sub">Bench, in substitution order</div>
    <div class="lineup">${eleven.bench.map((p) => row(p)).join('')}</div>`;
}

function renderCompare(d) {
  const before = d.eleven_current;
  const after = d.eleven;
  const outIds = new Set(d.recommended.moves.map((m) => m.out.id));
  const inIds = new Set(d.recommended.moves.map((m) => m.in.id));
  const delta = after.expected_points - before.expected_points;

  const side = (title, sub, eleven, changed, mark, deltaText) => `
    <div class="compare__side">
      <div class="compare__head">
        <div>
          <div class="compare__title">${title}</div>
          <div class="lineup__pos" style="margin-top:4px">${sub} · ${eleven.formation}</div>
        </div>
        <div style="text-align:right">
          <div class="compare__xp">
            <span class="fig">${eleven.expected_points.toFixed(1)}</span>
            <span>XP GW${d.gameweek}</span>
          </div>
          ${deltaText ? `<div class="compare__delta">${deltaText}</div>` : ''}
        </div>
      </div>
      ${lineup(eleven, changed, mark)}
    </div>`;

  const moves = d.recommended.moves.length;
  $('#compare').innerHTML =
    side('Squad now', 'No transfers made', before, outIds, 'OUT', '')
    + side(
        moves ? `After ${moves} ${moves === 1 ? 'transfer' : 'transfers'}` : 'After no change',
        moves
          ? `Hit ${d.recommended.hit} points`
          : 'Nothing worth doing',
        after, inIds, 'IN',
        moves ? `${signed(delta)} xP this gameweek` : '',
      );
}

/* The four chips, spent and unspent alike.
 *
 * A chip is a one-shot resource, so the panel has to answer two questions at
 * once: what is it worth this week, and is this the week. The sparkline is
 * what answers the second one honestly. It plots the chip's value across every
 * gameweek in view and outlines the current one, so "hold it for gameweek 6"
 * is something the reader can see rather than something the tool asserts.
 */
function sparkline(byGameweek, now) {
  const entries = Object.entries(byGameweek).map(([gw, v]) => [Number(gw), v]);
  if (entries.length < 2) return '';
  const peak = Math.max(...entries.map(([, v]) => v));
  if (peak <= 0) return '';

  const bars = entries.map(([gw, v]) => {
    const classes = ['spark__bar'];
    if (v === peak) classes.push('spark__bar--peak');
    if (gw === now) classes.push('spark__bar--now');
    const height = Math.max(2, Math.round((v / peak) * 34));
    return `<span class="${classes.join(' ')}" style="height:${height}px"
      title="Gameweek ${gw}: ${v.toFixed(1)} points"></span>`;
  }).join('');

  const labels = entries.map(([gw]) => `<span>${gw}</span>`).join('');
  return `<div class="spark">${bars}</div><div class="spark__labels">${labels}</div>`;
}

function renderChips(d) {
  const chips = d.chips || [];
  $('#chips-hint').textContent = d.chips_summary
    ? `${d.chips_summary}. `
      + (d.doubles_next_gw
          ? `${d.doubles_next_gw} of your players have two fixtures in GW${d.gameweek}.`
          : `No player in your squad has two fixtures in GW${d.gameweek}, which is `
            + `usually the week these are saved for.`)
    : '';

  $('#chips').innerHTML = chips.map((c) => {
    const spent = c.used_in !== null && c.used_in !== undefined;
    const state = spent ? 'spent' : (c.recommend ? 'play' : 'hold');
    const label = spent ? `GW${c.used_in}` : (c.recommend ? 'Play it' : 'Hold');
    const cls = spent ? ' chip--spent' : (c.recommend ? ' chip--play' : '');

    const value = (!spent && c.value !== null && c.value !== undefined)
      ? `<div class="chip__value">
           <span class="fig">${signed(c.value)}</span>
           <span>PTS</span>
         </div>`
      : '';

    return `
      <div class="chip${cls}">
        <div style="display:flex; align-items:baseline; justify-content:space-between; gap:8px">
          <span class="chip__name">${escapeHtml(c.name)}</span>
          <span class="chip__state chip__state--${state}">${label}</span>
        </div>
        ${value}
        ${spent ? '' : sparkline(c.by_gameweek || {}, d.gameweek)}
        <p class="chip__note">${escapeHtml(spent
          ? `${c.reason} ${c.blurb}`
          : (c.note || c.reason))}</p>
        ${spent ? '' : `<p class="chip__blurb">${escapeHtml(c.blurb)}</p>`}
      </div>`;
  }).join('');
}

function renderWildcard(d) {
  const w = d.wildcard;
  if (!w.available) {
    $('#wildcard').innerHTML = `
      <div class="verdict__call">Not available</div>
      <p class="muted" style="margin:0; font-size:13px">${escapeHtml(w.reason)}</p>`;
    return;
  }
  $('#wildcard').innerHTML = `
    <div class="verdict__call">${w.recommend ? 'Play it' : 'Hold it'}</div>
    <div class="verdict__nums">
      <div><div class="kicker">Wildcard</div><div class="fig">${w.wildcard_score.toFixed(1)}</div></div>
      <div><div class="kicker">Plan above</div><div class="fig">${w.plan_score.toFixed(1)}</div></div>
      <div><div class="kicker">Stand still</div><div class="fig">${w.current_score.toFixed(1)}</div></div>
    </div>
    <p class="muted" style="margin:14px 0 0; font-size:13px">
      A full rebuild is worth ${signed(w.gain_over_plan)} points over
      ${d.horizon.length} gameweeks for ${w.transfers_needed} changes.
      ${w.recommend
        ? 'Enough to spend the chip.'
        : 'Not enough to spend the chip on transfers you could make anyway.'}
      ${escapeHtml(w.reason)}
    </p>`;
}

/* ------------------------------------------------------------- briefing */

function withCitations(paragraph) {
  return escapeHtml(paragraph).replace(
    /\[([a-z_,\s]+)\]/g,
    (_, tags) => `<span class="cite">[${tags}]</span>`,
  );
}

function renderCoach(written) {
  const paragraphs = written.text.split('\n').filter((p) => p.trim());
  const sources = Object.entries(written.sources || {})
    .map(([tag, text]) => `<dt>[${escapeHtml(tag)}]</dt><dd>${escapeHtml(text)}</dd>`)
    .join('');
  $('#coach-out').innerHTML = `
    <div class="coach">
      ${paragraphs.map((p) => `<p>${withCitations(p.trim())}</p>`).join('')}
      <div class="coach__sources">
        <h6>Sources</h6>
        <dl style="margin-top:10px">${sources}</dl>
      </div>
      <p class="muted" style="font-size:12px; margin-top:14px">Written by
        ${escapeHtml(written.model)} from the figures on this page. It cannot see
        team news, injuries or press conferences.</p>
    </div>`;
}

/* ----------------------------------------------------------------- views */

const VIEWS = ['pitch', 'table', 'compare'];

function setView(next) {
  VIEW = VIEWS.includes(next) ? next : 'pitch';
  for (const name of VIEWS) {
    $(`#view-${name}`).setAttribute('aria-pressed', String(name === VIEW));
    $(`#${name}-view`).hidden = name !== VIEW;
  }
  try { localStorage.setItem('fpl-view', VIEW); } catch { /* private mode */ }
}

/* -------------------------------------------------------------------- boot */

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

for (const name of VIEWS) {
  $(`#view-${name}`).addEventListener('click', () => setView(name));
}

$('#refresh').addEventListener('click', async () => {
  // Actually drop the server's cached FPL responses. Re-running the form
  // against an hour-old cache is not what the label promises, and prices move
  // overnight.
  const button = $('#refresh');
  button.disabled = true;
  button.textContent = 'Refreshing';
  try {
    await api('/api/refresh', { method: 'POST' });
    await loadGameweek();
    if (LAST_QUERY) $('#form').requestSubmit();
  } catch (err) {
    showError(err);
  } finally {
    button.disabled = false;
    button.textContent = 'Refresh data';
  }
});

$('#form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const team = $('#team').value.trim();
  if (!/^\d+$/.test(team)) {
    return showError({ code: 'invalid_team_id',
                       message: 'A team id is digits only, the number in your team URL.' });
  }

  const params = new URLSearchParams({
    team,
    horizon: $('#horizon').value,
    max_transfers: $('#maxt').value,
  });
  if ($('#free').value) params.set('free_transfers', $('#free').value);

  $('#go').disabled = true;
  showBusy('Working');
  try {
    const data = await api(`/api/advice?${params}`);
    clearState();
    renderBand(data);
    renderTransfers(data);
    renderEleven(data);
    renderCompare(data);
    renderChips(data);
    renderWildcard(data);

    LAST_QUERY = params.toString();
    $('#coach-out').innerHTML = '';
    $('#coach-go').hidden = false;
    $('#coach-go').disabled = false;
    $('#coach-go').textContent = COACH_LABELS.idle;
    $('#coach-go').closest('div').hidden = false;
    if (!COACH.available) {
      $('#coach-go').hidden = true;
      $('#coach-hint').textContent =
        'Set GEMINI_API_KEY in the server environment to enable the briefing.';
    }
    $('#results').hidden = false;
    try { localStorage.setItem('fpl-team', team); } catch { /* private mode */ }
  } catch (err) {
    showError(err);
  } finally {
    $('#go').disabled = false;
  }
});

$('#coach-go').addEventListener('click', async () => {
  if (!LAST_QUERY) return;
  const button = $('#coach-go');
  button.disabled = true;
  button.textContent = COACH_LABELS.working;
  $('#coach-out').innerHTML = '';
  try {
    renderCoach(await api(`/api/coach?${LAST_QUERY}`));
    button.textContent = COACH_LABELS.done;
    // Stays disabled: the work is done and repeating it costs quota.
  } catch (err) {
    // Local to its own panel: the numbers above are still valid and must stay.
    $('#coach-out').innerHTML = `
      <div class="state state--error">
        <h3>The briefing did not generate</h3>
        <p class="muted" style="margin:0">${escapeHtml(err.message || 'Unknown error.')}</p>
        <p style="margin:6px 0 0"><code>${escapeHtml(err.code || 'unknown')}</code></p>
      </div>`;
    button.disabled = false;
    button.textContent = COACH_LABELS.failed;
  }
});

try {
  const remembered = localStorage.getItem('fpl-team');
  if (remembered) $('#team').value = remembered;
  const view = localStorage.getItem('fpl-view');
  if (view && view !== 'pitch') setView(view);
} catch { /* private mode */ }

loadGameweek();
