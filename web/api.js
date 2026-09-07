/* The API surface the page depends on.
 *
 * Shapes follow BACKEND.md §7. Until the service exists, the same shapes are
 * served as static files from ./fixtures, so switching over is a base-path
 * change and nothing else:
 *
 *   <html data-api="/api/v1">
 *
 * Two additions to the documented contract, both required by DESIGN.md and
 * flagged in README.md:
 *   - /board players carry `destination` and `updated_at`, because §2 puts
 *     "current club → predicted destination" and a last-updated caption on
 *     every card.
 *   - GET /clubs, because §4's dropdown needs a list to populate from.
 */

const BASE = document.documentElement.dataset.api || './fixtures';
const USING_FIXTURES = BASE.includes('fixtures');

function url(live, fixture) {
  return USING_FIXTURES ? `${BASE}/${fixture}` : `${BASE}${live}`;
}

async function get(path) {
  const res = await fetch(path, { headers: { accept: 'application/json' } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  board: (limit = 25) =>
    get(url(`/board?window=current&limit=${limit}`, 'board.json')),

  player: (id) => get(url(`/players/${id}`, `players/${id}.json`)),

  clubs: () => get(url('/clubs', 'clubs.json')),

  clubMovements: (id) =>
    get(url(`/clubs/${id}/movements`, `clubs/${id}.json`)),

  method: () => get(url('/method/calibration', 'method.json')),
};

export { USING_FIXTURES };
