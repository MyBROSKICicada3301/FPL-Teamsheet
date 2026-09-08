/* FPL Assistant — page behaviour.
 *
 * Framework-free on purpose: the page has one form, one fetch and four tables.
 *
 * Every failure path goes through `showError`, which reads the service's error
 * envelope — {error:{code,message,status}} — and shows the message. The code is
 * shown too, because "team_not_found" tells you what to change in a way that a
 * spinner that never stops does not.
 */

const $ = (sel) => document.querySelector(sel);
const money = (tenths) => `£${(tenths / 10).toFixed(1)}m`;
const signed = (n) => `${n >= 0 ? '+' : ''}${n.toFixed(2)}`;

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
    if (ms <= 0) { $('#gw-countdown').textContent = 'closed'; return; }
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
    $('#gw-number').textContent = gw.gameweek;
    $('#gw-deadline').textContent = new Date(gw.deadline)
      .toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
    $('#hit-cost').textContent = gw.rules.hit;
    $('#gw-strip').hidden = false;
    countdown(gw.deadline);
  } catch (err) {
    showError(err);
  }
}

/* ----------------------------------------------------------------- states */

function showError(err) {
  $('#results').hidden = true;
  $('#state').innerHTML = `
    <div class="state state--error">
      <h3>That didn't work</h3>
      <p>${escapeHtml(err.message || 'Unknown error.')}</p>
      <p><code>${escapeHtml(err.code || 'unknown')}</code></p>
    </div>`;
}

function showBusy(message) {
  $('#results').hidden = true;
  $('#state').innerHTML = `<div class="state"><h3>${escapeHtml(message)}</h3>
    <p>Reading your squad and projecting every player.</p></div>`;
}

function clearState() { $('#state').innerHTML = ''; }

/* ---------------------------------------------------------------- render */

function fixtureCells(row) {
  if (!row.fixtures.length) return '<span class="fdr fdr-3">—</span>';
  return row.fixtures.map((f) =>
    `<span class="fdr fdr-${f.difficulty}" title="GW${f.gw} ${
      f.home ? 'home' : 'away'} v ${f.opponent}, difficulty ${f.difficulty}">${
      escapeHtml(f.opponent)}${f.home ? '' : ' (a)'}</span>`).join(' ');
}

function playerRow(row, tags = '') {
  const warn = row.availability < 1
    ? ` <span class="warn" title="${escapeHtml(row.news || 'Doubtful')}">!</span>` : '';
  return `<tr class="${tags ? 'pick' : ''}">
    <td>${escapeHtml(row.name)}${tags}${warn}</td>
    <td>${escapeHtml(row.position)}</td>
    <td>${escapeHtml(row.team)}</td>
    <td class="n">${money(row.cost)}</td>
    <td class="n">${row.xp_next.toFixed(2)}</td>
    <td class="n">${row.xp_horizon.toFixed(2)}</td>
    <td>${fixtureCells(row)}</td>
  </tr>`;
}

function renderTransfers(d) {
  const rec = d.recommended;
  $('#transfers-hint').textContent =
    `${d.free_transfers} free transfer${d.free_transfers === 1 ? '' : 's'}`
    + ` · ${money(d.bank)} in the bank`
    + ` · judged over gameweeks ${d.horizon[0]}–${d.horizon[d.horizon.length - 1]}`;

  $('#moves').innerHTML = rec.moves.length
    ? rec.moves.map((m) => `
        <div class="move">
          <span class="dir dir--out">OUT</span>
          <span class="who">${escapeHtml(m.out.name)}
            <span class="meta">${escapeHtml(m.out.position)} · ${escapeHtml(m.out.team)}</span></span>
          <span class="num">${money(m.out.cost)}</span>
        </div>
        <div class="move">
          <span class="dir dir--in">IN</span>
          <span class="who">${escapeHtml(m.in.name)}
            <span class="meta">${escapeHtml(m.in.position)} · ${escapeHtml(m.in.team)}</span></span>
          <span class="num">${money(m.in.cost)} · ${signed(m.gain)} pts</span>
        </div>`).join('')
    : '<p class="hint">No move gains more than it costs. Roll the transfer.</p>';

  $('#transfer-verdict').innerHTML = rec.moves.length
    ? `<b>${rec.transfers}</b> transfer${rec.transfers === 1 ? '' : 's'},
       hit <b>${rec.hit}</b> pts, net <b>${signed(rec.net_gain)}</b> pts
       over ${d.horizon.length} gameweeks.`
    : `<b>Hold.</b> Banking the transfer is worth more than any move available.`;

  $('#options tbody').innerHTML = d.plans.map((p) => `
    <tr class="${p.transfers === rec.transfers ? 'pick' : ''}">
      <td>${p.transfers}</td>
      <td class="n">${p.gross_gain.toFixed(2)}</td>
      <td class="n">${p.hit}</td>
      <td class="n">${signed(p.net_gain)}</td>
    </tr>`).join('');
}

function renderEleven(d) {
  const e = d.eleven;
  $('#formation').textContent = e.formation;
  $('#eleven-hint').textContent =
    `${e.expected_points.toFixed(1)} expected points in GW${d.gameweek}, `
    + `captain doubled. Bench is in automatic-substitution order.`;

  const body = e.starters.map((p) => playerRow(
    p,
    p.id === e.captain.id ? '<span class="tag">C</span>'
      : p.id === e.vice_captain.id ? '<span class="tag tag--v">V</span>' : '',
  )).join('');

  const bench = `<tr><th colspan="7">Bench</th></tr>`
    + e.bench.map((p) => playerRow(p)).join('');

  $('#eleven tbody').innerHTML = body + bench;
}

function renderWildcard(d) {
  const w = d.wildcard;
  const box = $('#wildcard-verdict');
  const squad = $('#wildcard-squad');
  squad.innerHTML = '';

  if (!w.available) {
    box.innerHTML = `<b>Not available.</b> ${escapeHtml(w.reason)}`;
    return;
  }

  box.innerHTML = `
    <b>${w.recommend ? 'Play it.' : 'Hold it.'}</b>
    A wildcard squad is worth <b>${w.wildcard_score.toFixed(1)}</b> points over
    ${d.horizon.length} gameweeks, against <b>${w.plan_score.toFixed(1)}</b> for the
    transfers above and <b>${w.current_score.toFixed(1)}</b> for standing still —
    <b>${signed(w.gain_over_plan)}</b> points, for
    ${w.transfers_needed} changes. ${w.reason ? escapeHtml(w.reason) : ''}`;

  if (w.recommend && w.squad.length) {
    squad.innerHTML = `
      <div class="scroll" style="margin-top:16px">
        <table><thead><tr>
          <th>Player</th><th>Pos</th><th>Club</th><th class="n">Price</th>
          <th class="n">xP</th><th class="n">xP horizon</th><th>Fixtures</th>
        </tr></thead><tbody>${w.squad.map((p) => playerRow(p)).join('')}</tbody></table>
      </div>`;
  }
}

/* -------------------------------------------------------------------- boot */

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

$('#form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const team = $('#team').value.trim();
  if (!/^\d+$/.test(team)) {
    return showError({ code: 'invalid_team_id',
                       message: 'A team id is digits only — the number in your team URL.' });
  }

  const params = new URLSearchParams({
    team,
    horizon: $('#horizon').value,
    max_transfers: $('#maxt').value,
  });
  if ($('#free').value) params.set('free_transfers', $('#free').value);

  $('#go').disabled = true;
  showBusy('Working…');
  try {
    const data = await api(`/api/advice?${params}`);
    clearState();
    renderTransfers(data);
    renderEleven(data);
    renderWildcard(data);
    $('#results').hidden = false;
    localStorage.setItem('fpl-team', team);
  } catch (err) {
    showError(err);
  } finally {
    $('#go').disabled = false;
  }
});

const remembered = localStorage.getItem('fpl-team');
if (remembered) $('#team').value = remembered;

loadGameweek();
