const state = {
  config: null,
  data: null,
  tab: 'overview',
  filters: { search: '', result: 'all', timeClass: 'all', color: 'all', dateFrom: '', dateTo: '', page: '1' },
  game: null,
  coaching: null,
  progress: null,
  compare: null,
  training: null,
  exportScope: 'all',
};

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

function setStatus(text) {
  const el = $('app-status') || $('landing-status') || $('settings-status');
  if (el) el.textContent = text || '';
}

function toast(message, isError = false) {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast${isError ? ' error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add('hidden'), 4200);
}

function runJob(jobId, onProgress) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      try {
        const job = await api(`/api/job/${encodeURIComponent(jobId)}`);
        if (onProgress) onProgress(job);
        if (job.status === 'done') return resolve(job.result);
        if (job.status === 'error') return reject(new Error(job.error || 'Job failed.'));
        setTimeout(tick, 220);
      } catch (error) {
        reject(error);
      }
    };
    tick();
  });
}

function fmtNumber(value) {
  if (value === null || value === undefined || value === '') return '—';
  return Number(value).toLocaleString('en-US');
}

function fmtDate(value) {
  const parts = String(value || '').split('-');
  if (parts.length !== 3) return value || '—';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const month = months[Number(parts[1]) - 1];
  return month ? `${month} ${Number(parts[2])}, ${parts[0]}` : String(value);
}

function formatScore(score) {
  if (!score || (score.cp == null && score.mate == null)) return '—';
  if (score.mate != null) return score.mate > 0 ? `M${score.mate}` : `-M${Math.abs(score.mate)}`;
  const value = score.cp / 100;
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}`;
}

function resultLetter(result) {
  return ({ win: 'W', loss: 'L', draw: 'D' })[result] || '?';
}

function statCard(label, value, small = false) {
  return `<div class="stat-card"><div class="label">${esc(label)}</div><div class="value${small ? ' sm' : ''}">${esc(value)}</div></div>`;
}

function lineChart(labels, series) {
  const values = series.flatMap((item) => item.values.filter((value) => value != null));
  if (!values.length) return '<div class="empty">No rating data yet.</div>';
  const min = Math.min(...values) - 20;
  const max = Math.max(...values) + 20;
  const width = 640;
  const height = 160;
  const left = 38;
  const bottom = 24;
  const x = (index) => left + (index / Math.max(1, labels.length - 1)) * (width - left - 12);
  const y = (value) => height - bottom - ((value - min) / Math.max(1, max - min)) * (height - bottom - 12);
  const grid = [0, 0.5, 1].map((part) => {
    const yy = 12 + part * (height - bottom - 12);
    const label = Math.round(max - part * (max - min));
    return `<line class="grid" x1="${left}" y1="${yy}" x2="${width - 12}" y2="${yy}"/><text x="2" y="${yy + 3}">${label}</text>`;
  }).join('');
  const lines = series.map((item) => {
    const points = item.values.map((value, index) => value == null ? null : `${x(index)},${y(value)}`).filter(Boolean).join(' ');
    const dots = item.values.map((value, index) => value == null ? '' : `<circle cx="${x(index)}" cy="${y(value)}" r="2.5" fill="${esc(item.color)}"/>`).join('');
    return `<polyline class="line" stroke="${esc(item.color)}" points="${points}"/>${dots}`;
  }).join('');
  const labelsSvg = labels.map((label, index) => index % Math.max(1, Math.ceil(labels.length / 8)) === 0
    ? `<text x="${x(index)}" y="${height - 6}" text-anchor="middle">${esc(label)}</text>` : '').join('');
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${grid}${lines}${labelsSvg}</svg>`;
}

function donutChart(values, colors) {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (!total) return '<div class="empty">No results yet.</div>';
  const circumference = 2 * Math.PI * 42;
  let offset = 0;
  const circles = values.map((value, index) => {
    const length = (value / total) * circumference;
    const circle = `<circle cx="55" cy="55" r="42" fill="none" stroke="${colors[index]}" stroke-width="14" stroke-dasharray="${length} ${circumference - length}" stroke-dashoffset="${-offset}" transform="rotate(-90 55 55)"/>`;
    offset += length;
    return circle;
  }).join('');
  return `<div class="donut-wrap"><svg width="110" height="110" viewBox="0 0 110 110">${circles}<text x="55" y="59" text-anchor="middle" fill="#edf5ef" font-size="18" font-weight="700">${total}</text></svg><div class="legend">${['Wins', 'Losses', 'Draws'].map((name, index) => `<div><i style="background:${colors[index]}"></i>${name}: ${values[index]}</div>`).join('')}</div></div>`;
}

function barRow(label, value, max, suffix = '') {
  const width = max > 0 ? Math.round((value / max) * 100) : 0;
  return `<div class="bar-row"><span>${esc(label)}</span><div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div><span class="muted">${esc(value)}${esc(suffix)}</span></div>`;
}

async function init() {
  renderMiniBoard();
  try {
    state.config = await api('/api/config');
    $('username-input').value = state.config.username || '';
    if (!state.config.onboardingComplete) openSettings(true);
    const snapshot = await api('/api/state');
    if (snapshot.connected) enterDashboard(snapshot);
  } catch (error) {
    setStatus(error.message);
  }
}

function renderMiniBoard() {
  const fen = 'r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4';
  $('mini-board').innerHTML = boardCells(fen, 'w', null, false).join('');
}

async function connectAccount(force = false) {
  const username = $('username-input').value.trim();
  if (!username) return toast('Enter a Chess.com username.', true);
  $('connect-button').disabled = true;
  setStatus('Connecting…');
  try {
    const result = await post('/api/connect', { username, force });
    await runJob(result.job, (job) => setStatus(`${job.stage} · ${job.done}/${job.total}`));
    const snapshot = await api('/api/state');
    enterDashboard(snapshot);
    if (snapshot.errors && snapshot.errors.length) toast(snapshot.errors[0], true);
  } catch (error) {
    setStatus(error.message);
    toast(error.message, true);
  } finally {
    $('connect-button').disabled = false;
  }
}

async function refreshArchives() {
  setStatus('Refreshing archives…');
  try {
    const result = await post('/api/refresh', {});
    await runJob(result.job, (job) => setStatus(`${job.stage} · ${job.done}/${job.total}`));
    const snapshot = await api('/api/state');
    enterDashboard(snapshot);
    setStatus('Archives refreshed.');
    toast('Archives refreshed.');
  } catch (error) {
    toast(error.message, true);
  }
}

function enterDashboard(snapshot) {
  state.data = snapshot;
  state.config = snapshot.config;
  $('landing').classList.add('hidden');
  $('dashboard').classList.remove('hidden');
  const profile = snapshot.profile || {};
  const initials = (profile.username || state.username || '8×8').slice(0, 2).toUpperCase();
  $('profile-avatar').textContent = initials;
  $('profile-name').textContent = profile.username || state.username;
  $('profile-meta').textContent = [profile.country, profile.status].filter(Boolean).join(' · ') || profile.title || '';
  $('db-path').textContent = snapshot.database || '';
  updateEngineStatus();
  showTab(state.tab);
}

function updateEngineStatus() {
  const config = state.config || {};
  const el = $('engine-status');
  el.textContent = config.engineAvailable ? 'Stockfish ready' : 'Stockfish not configured';
  el.className = `engine-status${config.engineAvailable ? ' ok' : ''}`;
  $('engine-detail').textContent = config.engineAvailable
    ? `depth ${config.depth} · ${config.movetime} ms per position`
    : 'Analysis needs a Stockfish executable in settings.';
}

function showTab(name) {
  state.tab = name;
  document.querySelectorAll('#nav button').forEach((button) => button.classList.toggle('active', button.dataset.tab === name));
  const titles = {
    overview: 'Overview', games: 'Match history', openings: 'Openings', compare: 'Compare',
    favorites: 'Favorites', training: 'Training', progress: 'Progress', export: 'Export',
  };
  $('page-title').textContent = titles[name] || name;
  const root = $('tab-content');
  root.innerHTML = '<div class="empty">Loading…</div>';
  const renderers = {
    overview: renderOverview, games: () => renderGames(false), openings: renderOpenings,
    compare: renderCompare, favorites: () => renderGames(true), training: renderTraining,
    progress: renderProgress, export: renderExport,
  };
  Promise.resolve(renderers[name] ? renderers[name]() : null).catch((error) => {
    root.innerHTML = `<div class="panel empty">${esc(error.message)}</div>`;
  });
}

async function renderOverview() {
  await refreshState();
  const snapshot = state.data;
  const stats = snapshot.stats || {};
  const insights = snapshot.insights || {};
  const totals = stats.totals || {};
  const ratings = stats.ratings || {};
  const winRate = totals.winPercentage == null ? '—' : `${totals.winPercentage}%`;
  const cards = [
    ['Rapid', fmtNumber(ratings.rapid)], ['Blitz', fmtNumber(ratings.blitz)],
    ['Bullet', fmtNumber(ratings.bullet)], ['Daily', fmtNumber(ratings.daily)],
    ['Highest', fmtNumber((stats.highest || {}).rating)], ['Games', fmtNumber(snapshot.gameCount)],
    ['Wins', fmtNumber(totals.wins)], ['Losses', fmtNumber(totals.losses)],
    ['Draws', fmtNumber(totals.draws)], ['Win rate', winRate],
  ];
  const months = [...new Set(Object.values(insights.ratingHistory || {}).flatMap((series) => Object.keys(series)))].sort().slice(-24);
  const colors = { bullet: '#f07777', blitz: '#73a7f4', rapid: '#5fd39a', daily: '#d8b46a' };
  const series = Object.entries(insights.ratingHistory || {}).map(([key, values]) => ({
    name: key, color: colors[key] || '#9bad9f', values: months.map((month) => values[month]),
  }));
  const monthCounts = insights.byMonth || {};
  const monthKeys = Object.keys(monthCounts).sort().slice(-12);
  const byClass = insights.byClass || {};
  const classMax = Math.max(1, ...Object.values(byClass).map((item) => item.games));
  const openings = Object.values(insights.byOpening || {}).sort((a, b) => b.games - a.games).slice(0, 7);
  const opponents = Object.entries(insights.byOpponent || {}).sort((a, b) => b[1] - a[1]).slice(0, 7);
  $('tab-content').innerHTML = `
    <h2 style="margin-bottom:14px">Welcome back, ${esc(snapshot.profile?.username || state.username)}</h2>
    <div class="callout">
      <div><strong>LEARN FROM EVERY MOVE</strong><p>Open a game, run Stockfish, then click any move in the list. You'll see the best move, the evaluation swing and the reason the position changed — in plain language.</p></div>
      <button id="overview-analyze" class="primary">Analyze a game →</button>
    </div>
    <div class="stat-grid">${cards.map(([label, value]) => statCard(label, value)).join('')}</div>
    <div class="two-col">
      <section class="panel"><div class="section-label">Rating progression</div>${lineChart(months.map((m) => m.slice(5)), series)}</section>
      <section class="panel"><div class="section-label">Results</div>${donutChart([insights.totals?.win || 0, insights.totals?.loss || 0, insights.totals?.draw || 0], ['#5fd39a', '#f07777', '#d8b46a'])}</section>
    </div>
    <div class="two-col">
      <section class="panel"><div class="section-label">Games per month</div>${lineChart(monthKeys.map((m) => m.slice(5)), [{ name: 'Games', color: '#5fd39a', values: monthKeys.map((m) => monthCounts[m]) }])}</section>
      <section class="panel"><div class="section-label">Time control mix</div>${Object.entries(byClass).sort((a, b) => b[1].games - a[1].games).map(([key, item]) => barRow(key.replace(/^./, (c) => c.toUpperCase()), item.games, classMax)).join('') || '<div class="empty">No time-control data yet.</div>'}</section>
    </div>
    <div class="two-col equal">
      <section class="panel"><div class="section-label">Most played openings</div><div class="mini-list">${openings.map((item) => {
        const decided = item.win + item.loss + item.draw;
        const rate = decided ? Math.round(item.win / decided * 100) : 0;
        return `<div class="row"><button class="link" data-search="${esc(item.name)}">${esc(item.eco ? item.eco + ' · ' + item.name : item.name)}</button><span class="meta">${rate}% wins · ${item.games} games</span></div>`;
      }).join('') || '<div class="empty">No openings loaded.</div>'}</div></section>
      <section class="panel"><div class="section-label">Frequent opponents</div><div class="mini-list">${opponents.map(([name, count]) => `<div class="row"><button class="link" data-search="${esc(name)}">${esc(name)}</button><span class="meta">${count} games</span></div>`).join('') || '<div class="empty">No opponents loaded.</div>'}</div></section>
    </div>`;
  $('overview-analyze').onclick = () => openFirstGame();
  $('tab-content').querySelectorAll('[data-search]').forEach((button) => {
    button.onclick = () => {
      state.filters.search = button.dataset.search;
      state.filters.page = '1';
      showTab('games');
    };
  });
}

async function refreshState() {
  state.data = await api('/api/state');
  state.config = state.data.config;
  updateEngineStatus();
}

async function openFirstGame() {
  const data = await api('/api/games?page=1&pageSize=1');
  if (!data.games.length) return toast('No games loaded yet. Connect an account first.', true);
  openGame(data.games[0].id);
}

async function renderGames(favoritesOnly) {
  const root = $('tab-content');
  root.innerHTML = `
    <div class="filters">
      <label class="search">Search<input id="filter-search" type="text" placeholder="Opponent, opening, ECO…" value="${esc(state.filters.search)}"></label>
      <label>Result<select id="filter-result">
        ${[['all', 'All results'], ['win', 'Wins'], ['loss', 'Losses'], ['draw', 'Draws']].map(([value, label]) => `<option value="${value}" ${state.filters.result === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <label>Time control<select id="filter-time">
        ${[['all', 'All time controls'], ['bullet', 'Bullet'], ['blitz', 'Blitz'], ['rapid', 'Rapid'], ['daily', 'Daily']].map(([value, label]) => `<option value="${value}" ${state.filters.timeClass === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <label>Color<select id="filter-color">
        ${[['all', 'Both colors'], ['white', 'As White'], ['black', 'As Black']].map(([value, label]) => `<option value="${value}" ${state.filters.color === value ? 'selected' : ''}>${label}</option>`).join('')}
      </select></label>
      <label>From<input id="filter-from" type="date" value="${esc(state.filters.dateFrom)}"></label>
      <label>To<input id="filter-to" type="date" value="${esc(state.filters.dateTo)}"></label>
      <button id="filter-clear" class="ghost small">Clear filters</button>
    </div>
    <div id="games-table" class="table-wrap"><div class="empty">Loading games…</div></div>
    <div id="games-pager" class="pager"></div>`;
  const bindings = [
    ['filter-search', 'search'], ['filter-result', 'result'], ['filter-time', 'timeClass'],
    ['filter-color', 'color'], ['filter-from', 'dateFrom'], ['filter-to', 'dateTo'],
  ];
  bindings.forEach(([id, key]) => {
    $(id).addEventListener(id === 'filter-search' ? 'input' : 'change', () => {
      clearTimeout(renderGames.debounce);
      renderGames.debounce = setTimeout(() => {
        state.filters[key] = $(id).value;
        state.filters.page = '1';
        loadGamesTable(favoritesOnly);
      }, id === 'filter-search' ? 240 : 0);
    });
  });
  $('filter-clear').onclick = () => {
    state.filters = { search: '', result: 'all', timeClass: 'all', color: 'all', dateFrom: '', dateTo: '', page: '1' };
    renderGames(favoritesOnly);
  };
  await loadGamesTable(favoritesOnly);
}

async function loadGamesTable(favoritesOnly) {
  const query = new URLSearchParams({ ...state.filters, pageSize: '25' });
  if (favoritesOnly) query.set('favorites', '1');
  const data = await api(`/api/games?${query}`);
  const table = $('games-table');
  if (!data.games.length) {
    table.innerHTML = '<div class="empty">No games match the current filters.</div>';
    $('games-pager').textContent = '';
    return;
  }
  table.innerHTML = `<table><thead><tr><th>Result</th><th>Date</th><th>Color</th><th>Opponent</th><th>Opening</th><th>Time</th><th>Moves</th><th>Accuracy</th><th></th></tr></thead><tbody>${data.games.map((game) => `
    <tr data-game="${esc(game.id)}">
      <td><span class="badge ${esc(game.result)}">${resultLetter(game.result)}</span></td>
      <td>${esc(fmtDate(game.date))}</td>
      <td>${esc((game.color || '').replace(/^./, (c) => c.toUpperCase()))}</td>
      <td>${esc(game.opponent)}${game.opponentRating ? ` <span class="muted">(${esc(game.opponentRating)})</span>` : ''}</td>
      <td>${esc(game.opening || 'Unknown opening')}</td>
      <td>${esc(game.timeClass)}</td>
      <td>${esc(game.moves)}</td>
      <td>${game.accuracy == null ? '—' : `${esc(game.accuracy)}%`}</td>
      <td><button class="icon ghost favorite-btn" data-id="${esc(game.id)}" title="Toggle favorite">${state.data?.favorites?.includes(game.id) ? '★' : '☆'}</button></td>
    </tr>`).join('')}</tbody></table>`;
  table.querySelectorAll('tr[data-game]').forEach((row) => {
    row.onclick = () => openGame(row.dataset.game);
  });
  table.querySelectorAll('.favorite-btn').forEach((button) => {
    button.onclick = async (event) => {
      event.stopPropagation();
      const result = await post(`/api/favorite/${encodeURIComponent(button.dataset.id)}`, {});
      button.textContent = result.favorite ? '★' : '☆';
      const snapshot = await api('/api/state');
      state.data = snapshot;
      toast(result.favorite ? 'Added to favorites.' : 'Removed from favorites.');
    };
  });
  const pages = Math.max(1, Math.ceil(data.total / data.pageSize));
  $('games-pager').innerHTML = `<span>${fmtNumber(data.total)} games · page ${data.page} of ${pages}</span>
    <button class="small" id="prev-page" ${data.page <= 1 ? 'disabled' : ''}>◀ Previous</button>
    <button class="small" id="next-page" ${data.page >= pages ? 'disabled' : ''}>Next ▶</button>`;
  $('prev-page').onclick = () => { state.filters.page = String(data.page - 1); loadGamesTable(favoritesOnly); };
  $('next-page').onclick = () => { state.filters.page = String(data.page + 1); loadGamesTable(favoritesOnly); };
}

async function renderOpenings() {
  await refreshState();
  const insights = state.data.insights || {};
  const openings = Object.values(insights.byOpening || {}).sort((a, b) => b.games - a.games);
  const maxGames = Math.max(1, ...openings.map((item) => item.games));
  const colorPanel = ['white', 'black'].map((color) => {
    const record = (insights.byColor || {})[color] || { win: 0, loss: 0, draw: 0 };
    const decided = record.win + record.loss + record.draw;
    const rate = decided ? Math.round(record.win / decided * 100) : 0;
    return `<div>${color.replace(/^./, (c) => c.toUpperCase())}: ${rate}% wins over ${decided} games</div>`;
  }).join('');
  $('tab-content').innerHTML = `
    <section class="panel"><div class="section-label">Opening win rates</div>
      ${openings.slice(0, 40).map((item) => {
        const decided = item.win + item.loss + item.draw;
        const rate = decided ? Math.round(item.win / decided * 100) : 0;
        return `<div class="bar-row"><button class="link" data-search="${esc(item.name)}">${esc(item.eco ? item.eco + ' · ' + item.name : item.name)}</button>
          <div class="bar-track"><div class="bar-fill" style="width:${rate}%"></div></div><span class="muted">${rate}% · ${item.games} games</span></div>`;
      }).join('') || '<div class="empty">No openings loaded yet.</div>'}
    </section>
    <section class="panel" style="margin-top:14px"><div class="section-label">White versus Black</div>${colorPanel}</section>`;
  $('tab-content').querySelectorAll('[data-search]').forEach((button) => {
    button.onclick = () => { state.filters.search = button.dataset.search; state.filters.page = '1'; showTab('games'); };
  });
}

async function renderCompare() {
  const root = $('tab-content');
  root.innerHTML = `<section class="panel"><div class="section-label">Compare players</div>
    <div class="row gap"><label style="flex:1">Chess.com username<input id="compare-input" type="text" placeholder="Another public username"></label><button id="compare-button" class="primary">Compare</button></div>
    <p class="hint">Review ratings side by side and direct encounters in your loaded archive.</p></section>
    <div id="compare-result" style="margin-top:14px"></div>`;
  $('compare-input').value = state.compare ? state.compare.other : '';
  $('compare-button').onclick = async () => {
    const username = $('compare-input').value.trim();
    if (!username) return toast('Enter a username to compare.', true);
    $('compare-button').disabled = true;
    setStatus('Comparing players…');
    try {
      const result = await post('/api/compare', { username });
      const data = await runJob(result.job, (job) => setStatus(job.stage));
      state.compare = { other: username, data };
      renderCompareResult(data);
      setStatus('Comparison ready.');
    } catch (error) {
      toast(error.message, true);
    } finally {
      $('compare-button').disabled = false;
    }
  };
  if (state.compare) renderCompareResult(state.compare.data);
}

function renderCompareResult(data) {
  const cards = data.players.map((player) => {
    const stats = player.stats || {};
    const totals = stats.totals || {};
    const ratings = stats.ratings || {};
    return `<section class="panel"><div class="row gap"><div class="avatar">${esc(player.username.slice(0, 2).toUpperCase())}</div><div><h3>${esc(player.username)}</h3><div class="muted small">${esc(player.profile?.country || '')}</div></div></div>
      <div class="stat-grid" style="margin-bottom:0">
        ${statCard('Rapid', fmtNumber(ratings.rapid), true)}${statCard('Blitz', fmtNumber(ratings.blitz), true)}
        ${statCard('Highest', fmtNumber((stats.highest || {}).rating), true)}${statCard('Games', fmtNumber(totals.games), true)}
        ${statCard('Win rate', totals.winPercentage == null ? '—' : `${totals.winPercentage}%`, true)}
      </div></section>`;
  }).join('');
  const head = data.headToHead || [];
  $('compare-result').innerHTML = `<div class="two-col equal">${cards}</div>
    <section class="panel" style="margin-top:14px"><div class="section-label">Head-to-head · ${head.length} games</div>
      ${head.length ? `<div class="table-wrap"><table><thead><tr><th>Result</th><th>Color</th><th>Opening</th><th>Time</th><th>Date</th><th>Rating</th></tr></thead><tbody>${head.map((game) => `
        <tr data-game="${esc(game.id)}"><td><span class="badge ${esc(game.result)}">${resultLetter(game.result)}</span></td><td>${esc(game.color || '')}</td><td>${esc(game.opening || 'Unknown')}</td><td>${esc(game.timeClass)}</td><td>${esc(fmtDate(game.date))}</td><td>${esc(game.playerRating || '—')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No direct encounters found in this archive.</div>'}
    </section>`;
  $('compare-result').querySelectorAll('tr[data-game]').forEach((row) => { row.onclick = () => openGame(row.dataset.game); });
}

async function renderTraining() {
  const root = $('tab-content');
  root.innerHTML = `<div class="row gap" style="margin-bottom:14px"><div><div class="section-label">Personal coaching</div><p class="muted small">Lessons and exercises built from your saved Stockfish analyses.</p></div><span class="spacer"></span><button id="build-coaching" class="primary">Build coaching</button></div><div id="training-content"><div class="empty">Loading coaching…</div></div>`;
  $('build-coaching').onclick = async () => {
    $('build-coaching').disabled = true;
    setStatus('Building personalised lessons…');
    try {
      state.coaching = await api('/api/coaching');
      state.training = { session: { correct: 0, total: 0, focus: 'Balanced' }, index: 0, answers: {} };
      renderTrainingContent();
      setStatus('Coaching ready.');
    } catch (error) {
      toast(error.message, true);
      $('training-content').innerHTML = `<div class="panel empty">${esc(error.message)}</div>`;
    } finally {
      $('build-coaching').disabled = false;
    }
  };
  try {
    state.coaching = await api('/api/coaching');
    if (!state.training) state.training = { session: { correct: 0, total: 0, focus: 'Balanced' }, index: 0, answers: {} };
    renderTrainingContent();
  } catch (error) {
    $('training-content').innerHTML = `<div class="panel empty">${esc(error.message)}</div>`;
  }
}

function renderTrainingContent() {
  const data = state.coaching || {};
  const lessons = data.lessons || [];
  const exercises = data.exercises || [];
  const summary = data.summary || {};
  const session = state.training.session;
  const index = state.training.index;
  const exercise = exercises[index];
  const answer = state.training.answers[index];
  $('training-content').innerHTML = `
    <div class="stat-grid">
      ${statCard('Lessons', lessons.length, true)}${statCard('Exercises', exercises.length, true)}
      ${statCard('Accuracy', summary.accuracy == null ? '—' : `${summary.accuracy}%`, true)}
      ${statCard('Sessions', summary.sessions || 0, true)}
    </div>
    <div class="two-col equal">
      <section class="panel"><div class="section-label">Lessons</div>${lessons.map((lesson) => `<div class="lesson">
        <h4>${esc(lesson.title)}</h4><div class="muted small"><span class="tag ${esc(lesson.severity)}">${esc(lesson.severity)}</span>${esc(lesson.focus || '')} · ${esc(lesson.source || '')}</div>
        <p class="muted small">${esc(lesson.summary)}</p><ol>${(lesson.steps || []).map((step) => `<li>${esc(step)}</li>`).join('')}</ol></div>`).join('') || '<div class="empty">No lessons yet.</div>'}</section>
      <section class="panel"><div class="section-label">Exercise ${exercises.length ? `${index + 1} / ${exercises.length}` : ''}</div>
        ${exercise ? `<div class="exercise"><h4>${esc(exercise.prompt)}</h4>
          <div class="muted small"><span class="tag">${esc(exercise.phase)}</span><span class="tag ${esc(exercise.classification)}">${esc(exercise.classification)}</span></div>
          <div class="options">${exercise.options.map((option, number) => `<button data-option="${esc(option)}" class="${answer ? (option === exercise.answer ? 'correct' : (option === answer ? 'wrong' : '')) : ''}" ${answer ? 'disabled' : ''}>${esc(option)}</button>`).join('')}</div>
          ${answer ? `<div class="explanation"><strong>${answer === exercise.answer ? 'Correct' : `Not quite — the best move is ${esc(exercise.answer)}`}</strong>
            <p>${esc(exercise.explanation)}</p><p>${esc(exercise.whyBetter || '')}</p><div class="muted small">From ${esc(exercise.source?.game_id || '')} · ${esc(fmtDate(exercise.source?.date))}</div></div>` : ''}
          </div>
          <div class="row gap" style="margin-top:10px">
            ${answer ? '<button id="next-exercise" class="small">Next exercise →</button>' : ''}
            <span class="spacer"></span><span class="muted small">Session: ${session.correct} / ${session.total} correct</span>
            <button id="finish-session" class="small">Finish session</button>
          </div>` : '<div class="empty">No exercises yet. Analyze at least one game, then build coaching.</div>'}
      </section>
    </div>`;
  if (exercise && !answer) {
    $('training-content').querySelectorAll('[data-option]').forEach((button) => {
      button.onclick = () => {
        const option = button.dataset.option;
        state.training.answers[index] = option;
        session.total += 1;
        if (option === exercise.answer) session.correct += 1;
        renderTrainingContent();
      };
    });
  }
  if ($('next-exercise')) $('next-exercise').onclick = () => {
    state.training.index = Math.min(exercises.length - 1, index + 1);
    renderTrainingContent();
  };
  if ($('finish-session')) $('finish-session').onclick = async () => {
    try {
      await post('/api/coaching/finish', session);
      state.training = { session: { correct: 0, total: 0, focus: 'Balanced' }, index: 0, answers: {} };
      const data = await api('/api/coaching');
      state.coaching = data;
      renderTrainingContent();
      toast('Training session saved.');
    } catch (error) {
      toast(error.message, true);
    }
  };
}

async function renderProgress() {
  const root = $('tab-content');
  root.innerHTML = '<div class="empty">Loading progress…</div>';
  try {
    const data = await api('/api/progress');
    state.progress = data;
    const summary = data.summary || {};
    const focus = summary.focus || [];
    const maxFocus = Math.max(1, ...focus.map((item) => item.count));
    root.innerHTML = `
      <div class="stat-grid">
        ${statCard('Sessions', summary.sessions || 0)}${statCard('Average accuracy', summary.accuracy == null ? '—' : `${summary.accuracy}%`)}
        ${statCard('Recent trend', `${summary.trend > 0 ? '+' : ''}${summary.trend || 0}%`)}${statCard('Questions', summary.questions || 0)}
        ${statCard('Best streak', summary.bestStreak || 0)}
      </div>
      <div class="two-col">
        <section class="panel"><div class="section-label">Accuracy over time</div>${lineChart(summary.labels || [], [{ name: 'Accuracy', color: '#5fd39a', values: summary.accuracySeries || [] }])}</section>
        <section class="panel"><div class="section-label">Focus areas</div>${focus.map((item) => barRow(item.label, item.count, maxFocus)).join('') || '<div class="empty">No focus areas yet.</div>'}</section>
      </div>
      <section class="panel" style="margin-top:14px"><div class="section-label">Session history</div>
        ${(data.history || []).length ? `<div class="table-wrap"><table><thead><tr><th>Date</th><th>Focus</th><th>Correct</th><th>Total</th><th>Accuracy</th></tr></thead><tbody>${[...data.history].reverse().map((item) => {
          const accuracy = item.total ? Math.round(item.correct / item.total * 100) : 0;
          return `<tr><td>${esc(fmtDate(item.date))}</td><td>${esc(item.focus || 'Balanced')}</td><td>${esc(item.correct)}</td><td>${esc(item.total)}</td><td>${accuracy}%</td></tr>`;
        }).join('')}</tbody></table></div>` : '<div class="empty">Complete a training session to see progress.</div>'}
      </section>`;
  } catch (error) {
    root.innerHTML = `<div class="panel empty">${esc(error.message)}</div>`;
  }
}

async function renderExport() {
  const root = $('tab-content');
  const query = new URLSearchParams({ ...state.filters });
  if (state.exportScope === 'favorites') query.set('favorites', '1');
  const links = [
    ['pgn', 'PGN collection', 'All games in the current scope as PGN.'],
    ['csv', 'Match history CSV', 'Dates, results, opponents, openings and accuracy.'],
    ['training_report', 'Training report', 'Lessons, exercises and session history as CSV.'],
  ];
  root.innerHTML = `<section class="panel"><div class="section-label">Export center</div>
    <div class="row gap"><label>Scope<select id="export-scope"><option value="all" ${state.exportScope === 'all' ? 'selected' : ''}>All filtered games</option><option value="favorites" ${state.exportScope === 'favorites' ? 'selected' : ''}>Favorites only</option></select></label></div>
    <p class="hint">Exports are generated locally from your cached archive and training database.</p></section>
    <div class="three-col" style="margin-top:14px">${links.map(([kind, title, description]) => `<section class="panel"><h3>${esc(title)}</h3><p class="muted small">${esc(description)}</p><a class="primary" style="display:inline-block;text-decoration:none;padding:8px 13px;border-radius:9px" href="/api/export/${kind}?${query}">Download ${esc(kind === 'training_report' ? 'CSV' : kind.toUpperCase())}</a></section>`).join('')}</div>`;
  $('export-scope').onchange = () => { state.exportScope = $('export-scope').value; renderExport(); };
}

function boardCells(fen, orientation, lastMove, coordinates) {
  const cells = [];
  const rows = fen.split(' ')[0].split('/');
  const files = orientation === 'b' ? ['h', 'g', 'f', 'e', 'd', 'c', 'b', 'a'] : ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  const ranks = orientation === 'b' ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
  const glyphs = { p: '♟', r: '♜', n: '♞', b: '♝', q: '♛', k: '♚' };
  rows.forEach((row, rowIndex) => {
    const pieces = [];
    for (const char of row) {
      if (/\d/.test(char)) for (let i = 0; i < Number(char); i += 1) pieces.push(' ');
      else pieces.push(char);
    }
    const rank = ranks[rowIndex];
    pieces.forEach((piece, column) => {
      const file = files[column];
      const square = `${file}${rank}`;
      const light = (files.indexOf(file) + rank) % 2 === 0;
      const highlighted = lastMove && (square === lastMove.from || square === lastMove.to);
      const label = coordinates ? `<span class="coord ${column === 0 ? 'top' : ''}">${column === 0 ? rank : file}</span>` : '';
      cells.push(`<div class="sq ${light ? 'light' : 'dark'}${highlighted ? ' last' : ''}">${piece === ' ' ? '' : `<span>${glyphs[piece.toLowerCase()]}</span>`}${label}</div>`);
    });
  });
  return cells;
}

function capturedText(moves, ply) {
  const values = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };
  let whiteLost = 0;
  let blackLost = 0;
  for (let index = 0; index < ply; index += 1) {
    const move = moves[index];
    if (!move.captured) continue;
    if (move.color === 'w') blackLost += values[move.captured] || 0;
    else whiteLost += values[move.captured] || 0;
  }
  const difference = blackLost - whiteLost;
  return difference === 0 ? 'Even material' : difference > 0 ? `White +${difference}` : `Black +${-difference}`;
}

async function openGame(gameId) {
  $('game-modal').classList.remove('hidden');
  $('game-title').textContent = 'Loading game…';
  $('game-subtitle').textContent = '';
  $('board').innerHTML = '<div class="empty">Loading…</div>';
  $('move-list').innerHTML = '';
  $('analysis-area').innerHTML = '<div class="empty">Loading…</div>';
  try {
    const data = await api(`/api/game/${encodeURIComponent(gameId)}`);
    state.game = { id: gameId, data, ply: 0, flipped: false, playing: false, timer: null, analysis: data.analysis };
    const detail = data.detail || {};
    $('game-title').textContent = `${detail.white || ''} vs ${detail.black || ''}`;
    $('game-subtitle').textContent = `${fmtDate(detail.date)} · ${detail.timeClass || ''} · ${detail.result || ''} · ${detail.timeControl || ''}`;
    $('game-link').href = detail.url || '#';
    updateFavoriteButton();
    renderMoveList();
    renderAnalysisSummary();
    setPly(0);
  } catch (error) {
    $('board').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    toast(error.message, true);
  }
}

function updateFavoriteButton() {
  if (!state.game) return;
  const favorite = state.game.data.favorite;
  $('game-favorite').textContent = favorite ? '★ Saved' : '☆ Save';
}

function renderMoveList() {
  const moves = state.game?.data.moves || [];
  const pairs = [];
  for (let index = 0; index < moves.length; index += 2) {
    pairs.push(`<span class="move-pair"><span class="num">${index / 2 + 1}.</span>
      ${[0, 1].map((offset) => {
        const ply = index + offset + 1;
        return moves[index + offset] ? `<button class="move-btn" data-ply="${ply}">${esc(moves[ply - 1].san)}</button>` : '';
      }).join('')}</span>`);
  }
  $('move-list').innerHTML = pairs.join('') || '<div class="empty">No moves to replay.</div>';
  $('move-list').querySelectorAll('[data-ply]').forEach((button) => {
    button.onclick = () => { stopPlay(); setPly(Number(button.dataset.ply)); };
  });
}

function setPly(ply) {
  const game = state.game;
  if (!game) return;
  const moves = game.data.moves || [];
  game.ply = Math.max(0, Math.min(moves.length, ply));
  const last = game.ply > 0 ? moves[game.ply - 1] : null;
  if ((game.data.fens || []).length) {
    $('board').innerHTML = boardCells(game.data.fens[game.ply], game.flipped ? 'b' : 'w', last, true).join('');
  } else {
    $('board').innerHTML = `<div class="empty">${esc(game.data.pgnError || 'No board available.')}</div>`;
  }
  const slider = $('ply-slider');
  slider.max = String(moves.length);
  slider.value = String(game.ply);
  $('move-status').textContent = game.ply === 0 ? 'Starting position'
    : `${game.ply}. ${last?.san || ''} (${last?.color === 'w' ? 'White' : 'Black'})`;
  $('captured').textContent = capturedText(moves, game.ply);
  $('move-list').querySelectorAll('[data-ply]').forEach((button) => {
    button.classList.toggle('current', Number(button.dataset.ply) === game.ply);
  });
  renderAnalysisEntry();
}

function stopPlay() {
  if (!state.game) return;
  state.game.playing = false;
  clearInterval(state.game.timer);
  $('play-button').textContent = '▶ Play';
}

function togglePlay() {
  const game = state.game;
  if (!game) return;
  if (game.playing) return stopPlay();
  game.playing = true;
  $('play-button').textContent = '❚❚ Pause';
  game.timer = setInterval(() => {
    const moves = game.data.moves || [];
    if (game.ply >= moves.length) return stopPlay();
    setPly(game.ply + 1);
  }, 750);
}

function renderAnalysisSummary() {
  const analysis = state.game?.analysis;
  if (!analysis) {
    $('analysis-area').innerHTML = `<div class="empty">Stockfish analysis is not saved for this game. Run it below to see evaluations, classifications and explanations.${state.config?.engineAvailable ? '' : ' Stockfish is not configured — add its path in Engine settings.'}</div>
      <button id="analyze-all" class="primary" ${state.config?.engineAvailable ? '' : 'disabled'}>Analyze all moves</button>
      <p id="analysis-progress" class="status-line"></p>`;
  } else {
    const summary = analysis.summary || {};
    $('analysis-area').innerHTML = `<div class="headline">
      ${statCard('Classified', `${summary.classified || 0} / ${summary.moves || 0}`, true)}
      ${statCard('Blunders', summary.blunders || 0, true)}${statCard('Mistakes', summary.mistakes || 0, true)}
      ${statCard('Inaccuracies', summary.inaccuracies || 0, true)}
    </div>
    <button id="analyze-all" class="small">Re-analyze all moves</button>
    <p id="analysis-progress" class="status-line"></p>
    <div id="analysis-entry"></div>`;
  }
  if ($('analyze-all')) $('analyze-all').onclick = analyzeCurrentGame;
  renderAnalysisEntry();
}

function renderAnalysisEntry() {
  const target = $('analysis-entry');
  if (!target || !state.game) return;
  const analysis = state.game.analysis;
  if (!analysis || !state.game.ply) {
    target.innerHTML = analysis ? '<div class="muted small" style="margin-top:12px">Select a move in the list to inspect its analysis.</div>' : '';
    return;
  }
  const entry = (analysis.moves || {})[String(state.game.ply)] || (analysis.moves || {})[state.game.ply];
  if (!entry) { target.innerHTML = '<div class="muted small" style="margin-top:12px">No analysis for this move.</div>'; return; }
  const color = entry.color === 'w' ? 'White' : 'Black';
  const suffix = entry.color === 'w' ? '.' : '...';
  const classification = entry.classification || '';
  const classificationClass = classification.includes('blunder') ? 'blunder'
    : classification === 'mistake' ? 'mistake'
      : classification === 'inaccuracy' ? 'inaccuracy' : 'best';
  const candidates = (entry.candidates || []).slice(0, 3).map((candidate, index) => `
    <div class="candidate"><div class="line"><span>${index + 1}. ${esc(candidate.san || '—')} <span class="muted">${esc(formatScore(candidate.score))}</span></span>
      <span class="muted">${candidate.capture ? 'capture' : ''}${candidate.check ? ' check' : ''}</span></div>
      <div class="pv">${esc((candidate.pv || []).join(' '))}</div></div>`).join('');
  const opportunities = (entry.tacticalOpportunities || []).map((text) => `<div class="opportunity">• ${esc(text)}</div>`).join('');
  target.innerHTML = `<div style="margin-top:14px;border-top:1px solid var(--border);padding-top:12px">
    <div class="entry-title">Move ${entry.ply}${suffix} ${esc(entry.move || '')} (${color})</div>
    <div class="kv">
      <span class="k">Best move</span><span>${esc(entry.bestMoveSan || '—')}</span>
      <span class="k">Evaluation before (${color})</span><span>${esc(formatScore(entry.evalBefore))}</span>
      <span class="k">Evaluation after (${color})</span><span>${esc(formatScore(entry.evalAfter))}</span>
      <span class="k">Centipawn loss</span><span>${entry.centipawnLoss == null ? '—' : `${esc(entry.centipawnLoss)} · <span class="classification ${classificationClass}">${esc(classification || '—')}</span>`}</span>
      <span class="k">Why</span><span>${esc(entry.explanation || '')}</span>
      <span class="k">Better move</span><span>${esc(entry.whyBetter || '')}</span>
      <span class="k">Themes</span><span>${esc((entry.motifs || []).join(', ') || '—')}</span>
    </div>
    <div class="section-label">Top candidates</div>${candidates || '<div class="muted small">—</div>'}
    ${opportunities ? '<div class="section-label" style="margin-top:10px">Tactical opportunities</div>' + opportunities : ''}
  </div>`;
}

async function analyzeCurrentGame() {
  const game = state.game;
  if (!game || !state.config?.engineAvailable) return toast('Configure Stockfish in Engine settings first.', true);
  $('analyze-all').disabled = true;
  const progressLabel = $('analysis-progress');
  try {
    const result = await post(`/api/analyze/${encodeURIComponent(game.id)}`, {
      depth: state.config.depth, movetime: state.config.movetime,
    });
    await runJob(result.job, (job) => {
      if (progressLabel) progressLabel.textContent = `${job.stage} · ${job.done}/${job.total}`;
    });
    const data = await api(`/api/game/${encodeURIComponent(game.id)}`);
    game.data = data;
    game.analysis = data.analysis;
    renderAnalysisSummary();
    setPly(game.ply);
    toast('Analysis complete and saved for coaching.');
  } catch (error) {
    if (progressLabel) progressLabel.textContent = `Analysis failed: ${error.message}`;
    toast(error.message, true);
  } finally {
    if ($('analyze-all')) $('analyze-all').disabled = false;
  }
}

async function toggleGameFavorite() {
  const game = state.game;
  if (!game) return;
  try {
    const result = await post(`/api/favorite/${encodeURIComponent(game.id)}`, {});
    game.data.favorite = result.favorite;
    updateFavoriteButton();
    const snapshot = await api('/api/state');
    state.data = snapshot;
    toast(result.favorite ? 'Added to favorites.' : 'Removed from favorites.');
  } catch (error) {
    toast(error.message, true);
  }
}

function closeModal(name) {
  if (name === 'game') {
    stopPlay();
    state.game = null;
  }
  $(`${name}-modal`).classList.add('hidden');
}

function openSettings(onboarding = false) {
  const config = state.config || {};
  $('setting-engine').value = config.enginePath || '';
  $('setting-depth').value = config.depth || 12;
  $('setting-movetime').value = config.movetime || 150;
  $('setting-llm-url').value = config.llmUrl || '';
  $('setting-llm-model').value = config.llmModel || '';
  $('setting-llm-key').value = '';
  $('settings-modal').dataset.onboarding = onboarding ? '1' : '0';
  $('settings-modal').classList.remove('hidden');
}

async function saveSettings() {
  $('save-settings').disabled = true;
  try {
    const values = {
      enginePath: $('setting-engine').value.trim(),
      depth: Number($('setting-depth').value),
      movetime: Number($('setting-movetime').value),
      llmUrl: $('setting-llm-url').value.trim(),
      llmModel: $('setting-llm-model').value.trim(),
      llmKey: $('setting-llm-key').value.trim(),
      onboardingComplete: true,
    };
    state.config = await post('/api/config', values);
    updateEngineStatus();
    $('settings-status').textContent = 'Settings saved.';
    setTimeout(() => closeModal('settings'), 450);
    toast('Settings saved.');
  } catch (error) {
    $('settings-status').textContent = error.message;
    toast(error.message, true);
  } finally {
    $('save-settings').disabled = false;
  }
}

function wireEvents() {
  $('connect-button').onclick = () => connectAccount(false);
  $('username-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') connectAccount(false);
  });
  $('landing-settings').onclick = () => openSettings(false);
  $('open-settings').onclick = () => openSettings(false);
  $('refresh-button').onclick = refreshArchives;
  $('change-account').onclick = () => {
    stopPlay();
    state.game = null;
    $('game-modal').classList.add('hidden');
    $('dashboard').classList.add('hidden');
    $('landing').classList.remove('hidden');
    $('username-input').value = state.config?.username || '';
  };
  document.querySelectorAll('#nav button').forEach((button) => { button.onclick = () => showTab(button.dataset.tab); });
  document.querySelectorAll('[data-close]').forEach((button) => { button.onclick = () => closeModal(button.dataset.close); });
  $('game-favorite').onclick = toggleGameFavorite;
  $('ply-slider').oninput = (event) => { stopPlay(); setPly(Number(event.target.value)); };
  document.querySelectorAll('[data-move]').forEach((button) => {
    button.onclick = () => {
      const action = button.dataset.move;
      const game = state.game;
      if (!game) return;
      if (action === 'first') { stopPlay(); setPly(0); }
      else if (action === 'prev') { stopPlay(); setPly(game.ply - 1); }
      else if (action === 'next') { stopPlay(); setPly(game.ply + 1); }
      else if (action === 'last') { stopPlay(); setPly((game.data.moves || []).length); }
      else if (action === 'play') togglePlay();
      else if (action === 'flip') { game.flipped = !game.flipped; setPly(game.ply); }
    };
  });
  $('save-settings').onclick = saveSettings;
  document.addEventListener('keydown', (event) => {
    if (event.ctrlKey && event.key === ',') { event.preventDefault(); openSettings(false); return; }
    if (event.ctrlKey && /^[1-8]$/.test(event.key)) {
      const names = ['overview', 'games', 'openings', 'compare', 'favorites', 'training', 'progress', 'export'];
      event.preventDefault();
      if (state.data) showTab(names[Number(event.key) - 1]);
      return;
    }
    if (event.key === 'F5' && state.data) { event.preventDefault(); refreshArchives(); return; }
    if (!$('game-modal').classList.contains('hidden')) {
      if (event.key === 'ArrowLeft') { stopPlay(); setPly(state.game.ply - 1); }
      else if (event.key === 'ArrowRight') { stopPlay(); setPly(state.game.ply + 1); }
      else if (event.code === 'Space' && event.target.tagName !== 'INPUT') { event.preventDefault(); togglePlay(); }
      else if (event.key === 'Escape') closeModal('game');
    }
  });
}

wireEvents();
init();
