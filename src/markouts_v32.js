/* ═══════════════════════════════════════════════════════════════════
   RCG Markout Dashboard v32 — Card grid + deep-linked detail page
   ═══════════════════════════════════════════════════════════════════ */

const STATE = {
  data: null,
  modelParam: null,
  filtered: [],
  slippage: '5bps',
  sortKey: 'streak',
  tier: 'all',
  championsOnly: false,
  hasTrades: true,
  tradeFilter: 'all',
  tradeSortKey: 'exit_time',
  tradeSortDir: 'desc',
};

// ═══════════════════════════════════════════════════════════════════
// FORMATTERS
// ═══════════════════════════════════════════════════════════════════

function fmtPct(v, dec = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return (v * 100).toFixed(dec) + '%';
}
function fmtNum(v, dec = 1) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toFixed(dec);
}
function fmtMoney(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const sign = v < 0 ? '-' : '';
  const a = Math.abs(v);
  return sign + '$' + a.toLocaleString('en-US', { maximumFractionDigits: 0 });
}
function fmtPrice(v) {
  if (v === null || v === undefined || Number.isNaN(v)) return '<span class="missing">—</span>';
  return '$' + v.toFixed(2);
}
function fmtHold(mins) {
  if (mins === null || mins === undefined || Number.isNaN(mins)) return '—';
  if (mins < 60) return Math.round(mins) + 'm';
  const h = Math.floor(mins / 60);
  const m = Math.round(mins - h * 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}
function fmtTs(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mn = String(d.getMinutes()).padStart(2, '0');
  return `${mm}/${dd} ${hh}:${mn}`;
}
function fmtRelative(iso) {
  if (!iso) return 'never';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const delta = Date.now() - t;
  const s = Math.floor(delta / 1000);
  if (s < 60)        return `${s}s ago`;
  if (s < 3600)      return `${Math.floor(s/60)}m ago`;
  if (s < 86400)     return `${Math.floor(s/3600)}h ago`;
  if (s < 86400*7)   return `${Math.floor(s/86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}
function clsSign(v) {
  if (v > 0) return 'pos';
  if (v < 0) return 'neg';
  return '';
}

// ═══════════════════════════════════════════════════════════════════
// METRIC HELPERS
// ═══════════════════════════════════════════════════════════════════

function getReturn(m) { return m.summary?.[STATE.slippage]?.cum_return ?? 0; }
function getSharpe(m) { return m.summary?.[STATE.slippage]?.sharpe ?? 0; }
function getMaxDD(m)  { return m.summary?.[STATE.slippage]?.max_dd ?? 0; }
function getStreak(m) { return m.current_streak ?? 0; }

function getTier(m) {
  const trades = m.n_trades || 0;
  if (trades < 10) return 'gray';
  const ret = getReturn(m);
  const sh  = getSharpe(m);
  const st  = getStreak(m);
  if (st >= 3 || (sh > 1.5 && ret > 0.005)) return 'hot';
  if (st <= -2 || (ret < -0.01 && sh < 0))  return 'cold';
  return 'warm';
}

function streakBadge(st) {
  if (st >= 3)       return `<span class="mc-badge streak-hot">🔥 ${st}W</span>`;
  if (st > 0)        return `<span class="mc-badge streak-hot">✓ ${st}W</span>`;
  if (st <= -3)      return `<span class="mc-badge streak-cold">❄ ${-st}L</span>`;
  if (st < 0)        return `<span class="mc-badge streak-cold">${-st}L</span>`;
  return '';
}

function championBadge(m) {
  return m.is_champion ? `<span class="mc-badge champion">⭐</span>` : '';
}

// ═══════════════════════════════════════════════════════════════════
// DATA LOAD + ENTRY POINT
// ═══════════════════════════════════════════════════════════════════

async function loadData() {
  try {
    const r = await fetch('markouts.json?t=' + Date.now());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    STATE.data = await r.json();
  } catch (err) {
    console.error('Failed to load markouts.json:', err);
    document.body.innerHTML = `<div style="padding:40px;font-family:monospace;color:#ef4444">
      Failed to load markouts.json — ${err.message}<br>
      Try refreshing the publisher (markout_eval_publish.py).
    </div>`;
    return;
  }

  // URL routing
  const params = new URLSearchParams(window.location.search);
  STATE.modelParam = params.get('model');

  if (STATE.modelParam) {
    document.getElementById('grid-view').style.display = 'none';
    document.getElementById('detail-view').style.display = 'block';
    renderDetail(STATE.modelParam);
  } else {
    document.getElementById('grid-view').style.display = 'block';
    document.getElementById('detail-view').style.display = 'none';
    renderGrid();
  }
}

// ═══════════════════════════════════════════════════════════════════
// GRID VIEW
// ═══════════════════════════════════════════════════════════════════

function renderGrid() {
  const d = STATE.data;

  // Header
  document.getElementById('generated-at').textContent =
    'Generated ' + (d.generated_at ? new Date(d.generated_at).toLocaleString() : 'unknown');
  document.getElementById('lookback-pill').textContent = `${d.lookback_days}d window`;
  document.getElementById('metrics-window').textContent = `${d.lookback_days} trading days`;
  document.getElementById('slip-bps').textContent = STATE.slippage.replace('bps', ' bps');

  // Populate family filter
  const families = [...new Set(d.models.map(m => m.family))].filter(Boolean).sort();
  const famSel = document.getElementById('family-filter');
  if (famSel.options.length <= 1) {
    families.forEach(f => {
      const o = document.createElement('option');
      o.value = f;
      o.textContent = f.replace(/_/g, ' ');
      famSel.appendChild(o);
    });
  }

  // Wire events
  document.getElementById('search-input').oninput   = applyFilters;
  document.getElementById('family-filter').onchange = applyFilters;
  document.getElementById('slippage-filter').onchange = (e) => {
    STATE.slippage = e.target.value;
    document.getElementById('slip-bps').textContent = STATE.slippage.replace('bps', ' bps');
    applyFilters();
  };
  document.getElementById('sort-filter').onchange = (e) => {
    STATE.sortKey = e.target.value;
    applyFilters();
  };
  document.getElementById('filter-champions').onchange = (e) => {
    STATE.championsOnly = e.target.checked;
    applyFilters();
  };
  document.getElementById('filter-has-trades').onchange = (e) => {
    STATE.hasTrades = e.target.checked;
    applyFilters();
  };
  document.querySelectorAll('.tier-btn').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('.tier-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      STATE.tier = b.dataset.tier;
      applyFilters();
    };
  });
  document.getElementById('btn-refresh').onclick = () => loadData();

  applyFilters();
}

function applyFilters() {
  const d = STATE.data;
  const q = document.getElementById('search-input').value.toLowerCase().trim();
  const fam = document.getElementById('family-filter').value;

  STATE.filtered = d.models.filter(m => {
    if (q && !m.model.toLowerCase().includes(q)) return false;
    if (fam !== 'all' && m.family !== fam) return false;
    if (STATE.championsOnly && !m.is_champion) return false;
    if (STATE.hasTrades && (m.n_trades || 0) < 10) return false;
    if (STATE.tier !== 'all' && getTier(m) !== STATE.tier) return false;
    return true;
  });

  // Sort
  const dir = -1; // descending
  STATE.filtered.sort((a, b) => {
    let va, vb;
    switch (STATE.sortKey) {
      case 'streak':    va = getStreak(a); vb = getStreak(b); break;
      case 'return':    va = getReturn(a); vb = getReturn(b); break;
      case 'sharpe':    va = getSharpe(a); vb = getSharpe(b); break;
      case 'hit_rate':  va = a.hit_rate || 0; vb = b.hit_rate || 0; break;
      case 'n_trades':  va = a.n_trades || 0; vb = b.n_trades || 0; break;
      case 'best_trade':va = a.best_trade_return || 0; vb = b.best_trade_return || 0; break;
      default:          va = getStreak(a); vb = getStreak(b);
    }
    if (va === vb) return (getReturn(b) - getReturn(a));
    return dir * (va - vb);
  });

  document.getElementById('filter-summary').innerHTML =
    `Showing <strong>${STATE.filtered.length}</strong> of <strong>${d.models.length}</strong>`;

  renderQuickStats();
  renderChampions();
  renderGridCards();
}

function renderQuickStats() {
  const d = STATE.data;
  const all = d.models;
  const withT = all.filter(m => (m.n_trades || 0) >= 10);
  const champs = all.filter(m => m.is_champion);
  const profit = withT.filter(m => getReturn(m) > 0);
  const hot    = withT.filter(m => getTier(m) === 'hot');
  const avgRet = withT.length ? withT.reduce((s, m) => s + getReturn(m), 0) / withT.length : 0;
  const avgShp = withT.length ? withT.reduce((s, m) => s + getSharpe(m), 0) / withT.length : 0;
  const totalT = all.reduce((s, m) => s + (m.n_trades || 0), 0);
  const totMins = withT.reduce((s, m) => s + (m.avg_hold_trading_minutes || 0) * (m.n_trades || 0), 0);
  const totN    = withT.reduce((s, m) => s + (m.n_trades || 0), 0);
  const avgHold = totN > 0 ? totMins / totN : 0;

  const stats = [
    { v: all.length,           lbl: 'Models',         cls: '' },
    { v: withT.length,         lbl: 'With ≥10 trades', cls: '' },
    { v: champs.length,        lbl: 'Champions',      cls: '' },
    { v: hot.length,           lbl: '🔥 Hot tier',    cls: 'pos' },
    { v: profit.length,        lbl: 'Profitable',     cls: profit.length > withT.length / 2 ? 'pos' : '' },
    { v: fmtPct(avgRet),       lbl: `Avg return (${d.lookback_days}d)`, cls: clsSign(avgRet) },
    { v: fmtNum(avgShp, 2),    lbl: `Avg Sharpe (${d.lookback_days}d)`, cls: clsSign(avgShp) },
    { v: totalT,               lbl: 'Total trades',   cls: '' },
    { v: fmtHold(avgHold),     lbl: 'Avg hold',       cls: '' },
  ];

  document.getElementById('quick-stats').innerHTML = stats.map(s =>
    `<div class="qs-item"><div class="qs-value ${s.cls}">${s.v}</div><div class="qs-label">${s.lbl}</div></div>`
  ).join('');
}

function renderChampions() {
  const top = [...STATE.data.models]
    .filter(m => (m.n_trades || 0) >= 10)
    .sort((a, b) => {
      const sa = getStreak(a), sb = getStreak(b);
      if (sa !== sb) return sb - sa;
      const ra = getReturn(a), rb = getReturn(b);
      if (ra !== rb) return rb - ra;
      return getSharpe(b) - getSharpe(a);
    })
    .slice(0, 5);

  const container = document.getElementById('champion-grid');
  if (top.length === 0) {
    document.getElementById('champion-board').style.display = 'none';
    return;
  }
  document.getElementById('champion-board').style.display = 'block';

  container.innerHTML = top.map((m, i) => {
    const st = getStreak(m);
    const ret = getReturn(m);
    return `<div class="champion-card" data-model="${m.model}">
      <div class="cc-rank">#${i + 1}  ${m.family.replace(/_/g, ' ')}</div>
      <div class="cc-name">${m.model}</div>
      <div class="cc-meta">${st >= 0 ? st + 'W' : (-st) + 'L'} streak · ${m.n_trades || 0} trades</div>
      <div class="cc-stats">
        <span class="${clsSign(ret)}">${fmtPct(ret)}</span>
        <span>Sh ${fmtNum(getSharpe(m), 2)}</span>
      </div>
    </div>`;
  }).join('');

  container.querySelectorAll('.champion-card').forEach(c => {
    c.onclick = () => openDetail(c.dataset.model);
  });
}

function renderGridCards() {
  const d = STATE.data;
  const win = d.lookback_days;
  const container = document.getElementById('model-grid');

  if (STATE.filtered.length === 0) {
    container.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:40px;color:#64748b">
      No models match current filters.
    </div>`;
    return;
  }

  container.innerHTML = STATE.filtered.map(m => cardHTML(m, win)).join('');

  // Wire interactions
  container.querySelectorAll('.model-card').forEach(card => {
    card.onclick = (e) => {
      // Don't trigger when user is selecting text
      if (window.getSelection().toString()) return;
      openDetail(card.dataset.model);
    };
    card.onmouseenter = (e) => showTooltip(card, e);
    card.onmousemove  = (e) => positionTooltip(e);
    card.onmouseleave = hideTooltip;
  });
}

function cardHTML(m, win) {
  const tier  = getTier(m);
  const ret   = getReturn(m);
  const sh    = getSharpe(m);
  const dd    = getMaxDD(m);
  const hit   = m.hit_rate || 0;
  const trades = m.n_trades || 0;
  const best  = m.best_trade_return ?? 0;
  const worst = m.worst_trade_return ?? 0;
  const desc  = m.description?.short_desc || m.family || '';
  const rs    = m.rolling_sharpe || {};
  const st    = getStreak(m);
  const lastT = fmtRelative(m.last_fire_ts);

  return `<div class="model-card tier-${tier}" data-model="${m.model}">
    <div class="mc-header">
      <div>
        <div class="mc-name">${m.model}</div>
      </div>
      <div class="mc-badges">${streakBadge(st)}${championBadge(m)}</div>
    </div>
    <div class="mc-description">${desc}</div>
    <div class="mc-metrics">
      <div class="mc-metric">
        <div class="mc-metric-label">Return · ${win}d</div>
        <div class="mc-metric-value ${clsSign(ret)}">${fmtPct(ret)}</div>
      </div>
      <div class="mc-metric">
        <div class="mc-metric-label">Sharpe · ${win}d</div>
        <div class="mc-metric-value ${clsSign(sh)}">${fmtNum(sh, 2)}</div>
      </div>
      <div class="mc-metric">
        <div class="mc-metric-label">Hit rate</div>
        <div class="mc-metric-value">${fmtPct(hit, 1)}</div>
      </div>
      <div class="mc-metric">
        <div class="mc-metric-label">Max DD · ${win}d</div>
        <div class="mc-metric-value ${clsSign(dd)}">${fmtPct(dd, 1)}</div>
      </div>
    </div>
    <div class="mc-best-worst">
      <span><span class="lbl">Best trade</span> <span class="pos">${fmtPct(best, 2)}</span></span>
      <span><span class="lbl">Worst</span> <span class="neg">${fmtPct(worst, 2)}</span></span>
      <span><span class="lbl">Trades</span> ${trades}</span>
    </div>
    <div class="mc-footer">
      <span class="rolling-sharpe">RS 5d ${fmtNum(rs['5d'], 1)} · 10d ${fmtNum(rs['10d'], 1)} · 30d ${fmtNum(rs['30d'], 1)}</span>
      <span>${lastT}</span>
    </div>
  </div>`;
}

// ═══════════════════════════════════════════════════════════════════
// TOOLTIP
// ═══════════════════════════════════════════════════════════════════

function showTooltip(card, e) {
  const modelName = card.dataset.model;
  const m = STATE.data.models.find(x => x.model === modelName);
  if (!m) return;
  const desc = m.description || {};
  const rs = m.rolling_sharpe || {};

  const tt = document.getElementById('model-tooltip');
  tt.innerHTML = `
    <h4>${m.model}</h4>
    <div class="tt-section">
      <div class="tt-label">What it captures</div>
      <div class="tt-value">${desc.what_it_captures || '—'}</div>
    </div>
    <div class="tt-section">
      <div class="tt-label">Entry</div>
      <div class="tt-value">${desc.entry || '—'}</div>
      <div class="tt-label">Exit</div>
      <div class="tt-value">${desc.exit || '—'}</div>
    </div>
    <div class="tt-section">
      <div class="tt-stats">
        <div><span class="tt-label">Rolling Sharpe 5d/10d/30d</span></div>
        <div></div>
        <div>${fmtNum(rs['5d'], 2)}</div>
        <div>${fmtNum(rs['10d'], 2)} · ${fmtNum(rs['30d'], 2)}</div>
        <div><span class="tt-label">Current streak</span></div>
        <div>${getStreak(m) >= 0 ? getStreak(m) + 'W' : (-getStreak(m)) + 'L'}</div>
        <div><span class="tt-label">Max W / L streak</span></div>
        <div>${m.max_win_streak || 0}W / ${m.max_loss_streak || 0}L</div>
        <div><span class="tt-label">Last fire</span></div>
        <div>${fmtRelative(m.last_fire_ts)}</div>
      </div>
    </div>
  `;
  tt.style.display = 'block';
  positionTooltip(e);
}

function positionTooltip(e) {
  const tt = document.getElementById('model-tooltip');
  if (tt.style.display === 'none') return;
  const pad = 14;
  let x = e.pageX + pad;
  let y = e.pageY + pad;
  const w = tt.offsetWidth;
  const h = tt.offsetHeight;
  if (x + w > window.innerWidth + window.scrollX) x = e.pageX - w - pad;
  if (y + h > window.innerHeight + window.scrollY) y = e.pageY - h - pad;
  tt.style.left = x + 'px';
  tt.style.top  = y + 'px';
}

function hideTooltip() {
  document.getElementById('model-tooltip').style.display = 'none';
}

function openDetail(modelName) {
  window.open('markouts.html?model=' + encodeURIComponent(modelName), '_blank');
}

// ═══════════════════════════════════════════════════════════════════
// DETAIL VIEW
// ═══════════════════════════════════════════════════════════════════

function renderDetail(modelName) {
  const d = STATE.data;
  const m = d.models.find(x => x.model === modelName);
  if (!m) {
    document.getElementById('detail-view').innerHTML = `
      <div style="padding:40px;font-family:monospace;color:#ef4444">
        Model '<strong>${modelName}</strong>' not found in markouts.json.
        <br><br><a href="markouts.html" style="color:#3b82f6">← Back to grid</a>
      </div>`;
    return;
  }
  document.title = `${m.model} — Markout v32`;

  // Header
  document.getElementById('detail-model-name').textContent = m.model;
  document.getElementById('detail-description').textContent =
    `${m.description?.short_desc || m.family} · ${m.horizon || 'n/a'}`;

  // Methodology
  const desc = m.description || {};
  document.getElementById('detail-what').textContent  = desc.what_it_captures || '—';
  document.getElementById('detail-entry').textContent = desc.entry || '—';
  document.getElementById('detail-exit').textContent  = desc.exit  || '—';

  // Badges
  const st = getStreak(m);
  const badges = [];
  if (m.is_champion) badges.push(`<span class="db-badge champion">⭐ Champion</span>`);
  if (st >= 3)       badges.push(`<span class="db-badge streak-hot">🔥 ${st}W streak</span>`);
  else if (st > 0)   badges.push(`<span class="db-badge streak-hot">✓ ${st}W</span>`);
  else if (st <= -3) badges.push(`<span class="db-badge streak-cold">❄ ${-st}L streak</span>`);
  else if (st < 0)   badges.push(`<span class="db-badge streak-cold">${-st}L</span>`);
  badges.push(`<span class="db-badge"><span class="lbl">Family</span> ${m.family || '—'}</span>`);
  badges.push(`<span class="db-badge"><span class="lbl">Horizon</span> ${m.horizon || 'n/a'}</span>`);
  badges.push(`<span class="db-badge"><span class="lbl">Trades</span> ${m.n_trades || 0}</span>`);
  badges.push(`<span class="db-badge"><span class="lbl">Open at end</span> ${m.n_open_at_end || 0}</span>`);
  document.getElementById('detail-badges').innerHTML = badges.join('');

  // Key metrics
  const win = d.lookback_days;
  const ret = getReturn(m), sh = getSharpe(m), dd = getMaxDD(m);
  const dmItems = [
    { lbl: 'Return',     win, val: fmtPct(ret),  cls: clsSign(ret), sub: `Gross ${fmtPct(m.summary?.gross?.cum_return || 0)}` },
    { lbl: 'Sharpe',     win, val: fmtNum(sh, 2), cls: clsSign(sh), sub: `Annualized, ${STATE.slippage}` },
    { lbl: 'Max DD',     win, val: fmtPct(dd, 2), cls: clsSign(dd), sub: 'Peak → trough, NET' },
    { lbl: 'Hit Rate',   win: '', val: fmtPct(m.hit_rate || 0, 1), cls: '', sub: `${m.n_long || 0}L / ${m.n_short || 0}S round-trips` },
    { lbl: 'Best Trade', win: '', val: fmtPct(m.best_trade_return || 0, 2), cls: 'pos', sub: `Max gain ${fmtMoney(m.max_gain_dollars)}` },
    { lbl: 'Worst Trade',win: '', val: fmtPct(m.worst_trade_return || 0, 2), cls: 'neg', sub: `Max loss ${fmtMoney(m.max_loss_dollars)}` },
    { lbl: 'Avg Hold',   win: '', val: fmtHold(m.avg_hold_trading_minutes), cls: '', sub: 'Trading minutes' },
    { lbl: 'N Fires',    win, val: m.n_fires || 0, cls: '', sub: `${m.n_trades || 0} closed round-trips` },
  ];
  document.getElementById('detail-metrics').innerHTML = dmItems.map(it => `
    <div class="dm-item">
      <div class="dm-label">${it.lbl} ${it.win ? `<span class="window-tag">${it.win}d</span>` : ''}</div>
      <div class="dm-value ${it.cls}">${it.val}</div>
      <div class="dm-sub">${it.sub}</div>
    </div>`).join('');

  // Strip — rolling sharpe + streaks
  const rs = m.rolling_sharpe || {};
  document.getElementById('detail-strip').innerHTML = `
    <div class="strip-card">
      <h3>Rolling Sharpe (trailing window)</h3>
      <div class="rs-row"><span class="lbl">5-day</span><span class="val ${clsSign(rs['5d'])}">${fmtNum(rs['5d'], 2)}</span></div>
      <div class="rs-row"><span class="lbl">10-day</span><span class="val ${clsSign(rs['10d'])}">${fmtNum(rs['10d'], 2)}</span></div>
      <div class="rs-row"><span class="lbl">30-day</span><span class="val ${clsSign(rs['30d'])}">${fmtNum(rs['30d'], 2)}</span></div>
    </div>
    <div class="strip-card">
      <h3>Streak & Activity</h3>
      <div class="rs-row"><span class="lbl">Current streak</span><span class="val ${st > 0 ? 'pos' : (st < 0 ? 'neg' : '')}">${st >= 0 ? st + 'W' : (-st) + 'L'}</span></div>
      <div class="rs-row"><span class="lbl">Max win streak</span><span class="val pos">${m.max_win_streak || 0}W</span></div>
      <div class="rs-row"><span class="lbl">Max loss streak</span><span class="val neg">${m.max_loss_streak || 0}L</span></div>
      <div class="rs-row"><span class="lbl">Last fire</span><span class="val">${fmtRelative(m.last_fire_ts)}</span></div>
    </div>
  `;

  // Equity chart
  document.getElementById('chart-window').textContent = `${win}-day window · ${STATE.slippage} slippage`;
  renderEquityChart(m);

  // Per-ticker table
  const pt = m.per_ticker || [];
  document.getElementById('ticker-tbody').innerHTML = pt.map(r => `
    <tr>
      <td>${r.ticker}</td>
      <td class="numeric">${r.n_trades || 0}</td>
      <td class="numeric">${r.n_long || 0}</td>
      <td class="numeric">${r.n_short || 0}</td>
      <td class="numeric ${clsSign(r.cum_pnl)}">${fmtPct(r.cum_pnl, 3)}</td>
    </tr>
  `).join('');

  // Trade table
  renderTradeTable(m);

  // Footer
  document.getElementById('detail-last-fire').textContent =
    `Last fire: ${m.last_fire_ts ? new Date(m.last_fire_ts).toLocaleString() : '—'}`;

  // Refresh
  document.getElementById('btn-refresh-detail').onclick = () => loadData();
}

function renderEquityChart(m) {
  const slipKey = `equity_net_${STATE.slippage}`;
  const eq = m[slipKey] || m.equity_net_5bps || [];
  const gross = m.equity_gross || [];
  if (!eq.length) {
    document.getElementById('chart-equity').innerHTML =
      `<div style="padding:20px;color:#64748b;text-align:center">No equity data — n_fires = ${m.n_fires || 0}</div>`;
    return;
  }
  const traces = [
    {
      x: eq.map(p => p.date),
      y: eq.map(p => p.value),
      name: `Net (${STATE.slippage})`,
      type: 'scatter', mode: 'lines',
      line: { color: '#22c55e', width: 2 },
    },
    {
      x: gross.map(p => p.date),
      y: gross.map(p => p.value),
      name: 'Gross',
      type: 'scatter', mode: 'lines',
      line: { color: '#3b82f6', width: 1, dash: 'dot' },
    },
  ];
  Plotly.newPlot('chart-equity', traces, {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor:  'rgba(0,0,0,0)',
    font: { family: 'DM Sans, sans-serif', color: '#94a3b8', size: 11 },
    margin: { t: 10, r: 10, b: 30, l: 50 },
    xaxis: { gridcolor: '#334155', zeroline: false },
    yaxis: { gridcolor: '#334155', zeroline: false, tickformat: '.3f' },
    legend: { orientation: 'h', y: -0.18 },
    hovermode: 'x unified',
  }, { displayModeBar: false, responsive: true });
}

// ═══════════════════════════════════════════════════════════════════
// TRADE TABLE
// ═══════════════════════════════════════════════════════════════════

function renderTradeTable(m) {
  const all = m.all_trades || [];
  document.getElementById('trade-count').textContent = all.length;

  // Wire filter bar (idempotent)
  document.querySelectorAll('.trade-filter-btn').forEach(b => {
    b.onclick = () => {
      document.querySelectorAll('.trade-filter-btn').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      STATE.tradeFilter = b.dataset.tf;
      renderTradeRows(m);
    };
  });

  // Wire column sorting
  document.querySelectorAll('#trade-table th.sortable').forEach(th => {
    th.onclick = () => {
      const k = th.dataset.tc;
      if (STATE.tradeSortKey === k) {
        STATE.tradeSortDir = STATE.tradeSortDir === 'asc' ? 'desc' : 'asc';
      } else {
        STATE.tradeSortKey = k;
        STATE.tradeSortDir = 'desc';
      }
      renderTradeRows(m);
    };
  });

  renderTradeRows(m);
}

function renderTradeRows(m) {
  let rows = m.all_trades || [];

  // Filter
  switch (STATE.tradeFilter) {
    case 'long':   rows = rows.filter(t => t.direction === 'long'); break;
    case 'short':  rows = rows.filter(t => t.direction === 'short'); break;
    case 'wins':   rows = rows.filter(t => t.return_pct > 0); break;
    case 'losses': rows = rows.filter(t => t.return_pct < 0); break;
  }

  // Sort
  const k = STATE.tradeSortKey;
  const dirMul = STATE.tradeSortDir === 'asc' ? 1 : -1;
  rows = [...rows].sort((a, b) => {
    let va = a[k], vb = b[k];
    if (va === null || va === undefined) va = -Infinity;
    if (vb === null || vb === undefined) vb = -Infinity;
    if (typeof va === 'string' && typeof vb === 'string') {
      return dirMul * va.localeCompare(vb);
    }
    return dirMul * ((va < vb) ? -1 : (va > vb ? 1 : 0));
  });

  // Header indicators
  document.querySelectorAll('#trade-table th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.tc === k) {
      th.classList.add(STATE.tradeSortDir === 'asc' ? 'sort-asc' : 'sort-desc');
    }
  });

  // Render
  const html = rows.map(t => {
    const rc = t.return_pct > 0 ? 'win' : (t.return_pct < 0 ? 'loss' : '');
    return `<tr>
      <td>${fmtTs(t.exit_time)}</td>
      <td>${t.ticker}</td>
      <td class="dir-${t.direction}">${t.direction === 'long' ? 'L' : 'S'}</td>
      <td class="numeric">${fmtNum(t.entry_score, 1)}</td>
      <td class="numeric">${fmtNum(t.exit_score, 1)}</td>
      <td class="numeric">${fmtPrice(t.entry_price)}</td>
      <td class="numeric">${fmtPrice(t.exit_price)}</td>
      <td class="numeric">${fmtHold(t.hold_minutes)}</td>
      <td class="numeric">${fmtMoney(t.capital_used)}</td>
      <td class="numeric ${rc}">${fmtPct(t.return_pct, 2)}</td>
      <td class="numeric ${rc}">${fmtMoney(t.return_dollars)}</td>
      <td>${(t.exit_reason || '').replace(/_/g, ' ')}</td>
    </tr>`;
  }).join('');

  document.getElementById('trade-tbody').innerHTML = html ||
    `<tr><td colspan="12" style="text-align:center;padding:20px;color:#64748b">No trades match this filter.</td></tr>`;
}

// ═══════════════════════════════════════════════════════════════════
// BOOT
// ═══════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', loadData);
