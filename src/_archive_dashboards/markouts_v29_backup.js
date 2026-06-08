/* ===================================================================
   RCG Markout Dashboard v29 — JavaScript Logic
   Table-first interface with family grouping and detail drill-down
   =================================================================== */

// ===== STATE =====
const state = {
    allModels: [],
    filteredModels: [],
    selectedModelId: null,
    sortColumn: 'return',
    sortDir: 'desc',
    filters: {
        search: '',
        family: 'all',
        minTrades: 10,
        championsOnly: false,
        profitableOnly: false,
        hasTradesOnly: true,
        slippage: '5bps'
    },
    data: null  // Full JSON payload
};

// ===== FAMILY DEFINITIONS (human-readable labels) =====
const FAMILY_LABELS = {
    'momentum': 'Momentum',
    'mean_reversion': 'Mean Reversion',
    'bollinger_pos': 'Range / Bands',
    'donchian_break': 'Breakout',
    'rsi_extreme': 'RSI Extreme',
    'sma_cross': 'Moving Avg Cross',
    'ema_cross': 'EMA Cross',
    'lr_slope': 'Linear Regression',
    'arima': 'Time Series (ARIMA/AR)',
    'pattern': 'Statistical Patterns',
    'cross_sectional': 'Cross-Sectional',
    'ensemble': 'Ensemble / Combo',
    'meta_blend': 'Meta-Model (OLS)',
    'bbg_composite': 'BBG Composite',
    'other': 'Other'
};

// ===== FAMILY COLOR PALETTE =====
const FAMILY_COLORS = {
    'momentum': '#3b82f6',
    'mean_reversion': '#10b981',
    'bollinger_pos': '#8b5cf6',
    'donchian_break': '#f59e0b',
    'rsi_extreme': '#ef4444',
    'sma_cross': '#06b6d4',
    'ema_cross': '#14b8a6',
    'lr_slope': '#f97316',
    'arima': '#ec4899',
    'pattern': '#a855f7',
    'cross_sectional': '#84cc16',
    'ensemble': '#6366f1',
    'meta_blend': '#c8a84e',
    'bbg_composite': '#d97706',
    'other': '#64748b'
};

// ===== INIT =====
document.addEventListener('DOMContentLoaded', () => {
    loadData();
    wireEventListeners();
});

function wireEventListeners() {
    document.getElementById('btn-refresh').addEventListener('click', loadData);
    document.getElementById('filter-search').addEventListener('input', e => {
        state.filters.search = e.target.value.toLowerCase();
        applyFilters();
    });
    document.getElementById('filter-family').addEventListener('change', e => {
        state.filters.family = e.target.value;
        applyFilters();
    });
    document.getElementById('filter-min-trades').addEventListener('change', e => {
        state.filters.minTrades = parseInt(e.target.value);
        applyFilters();
    });
    document.getElementById('filter-slippage').addEventListener('change', e => {
        state.filters.slippage = e.target.value;
        document.getElementById('current-slippage').textContent = e.target.value.replace('bps', ' bps');
        applyFilters();
    });
    document.getElementById('filter-champions').addEventListener('change', e => {
        state.filters.championsOnly = e.target.checked;
        applyFilters();
    });
    document.getElementById('filter-profitable').addEventListener('change', e => {
        state.filters.profitableOnly = e.target.checked;
        applyFilters();
    });
    document.getElementById('filter-has-trades').addEventListener('change', e => {
        state.filters.hasTradesOnly = e.target.checked;
        applyFilters();
    });

    // Detail panel navigation
    document.getElementById('btn-close-detail').addEventListener('click', closeDetailPanel);
    document.getElementById('btn-prev-model').addEventListener('click', navigatePrev);
    document.getElementById('btn-next-model').addEventListener('click', navigateNext);
    document.getElementById('btn-export').addEventListener('click', exportCSV);

    // Keyboard shortcuts
    document.addEventListener('keydown', e => {
        if (!state.selectedModelId) return;
        if (e.key === 'ArrowUp') { e.preventDefault(); navigatePrev(); }
        if (e.key === 'ArrowDown') { e.preventDefault(); navigateNext(); }
        if (e.key === 'Escape') { closeDetailPanel(); }
    });

    // Table header sorting
    document.querySelectorAll('#models-table th.sortable').forEach(th => {
        th.addEventListener('click', () => sortTable(th.dataset.col));
    });
}

// ===== DATA LOADING =====
async function loadData() {
    const banner = document.getElementById('error-banner');
    banner.classList.remove('visible');
    banner.textContent = '';

    try {
        const r = await fetch('markouts.json?_=' + Date.now(), { cache: 'no-cache' });
        if (!r.ok) throw new Error('HTTP ' + r.status);
        state.data = await r.json();
    } catch (e) {
        banner.textContent = 'Failed to load markouts.json: ' + e.message;
        banner.classList.add('visible');
        return;
    }

    // Update header info
    document.getElementById('generated-at').textContent =
        'Generated: ' + (state.data.generated_at || '?').slice(0, 19).replace('T', ' ');
    document.getElementById('lookback-days').textContent = state.data.lookback_days || 90;

    // Populate family filter dropdown
    populateFamilyFilter();

    // Process models
    state.allModels = (state.data.models || []).map((m, idx) => ({
        ...m,
        id: `${m.model}|${m.horizon}`,
        idx: idx,
        family_label: FAMILY_LABELS[m.family] || m.family || 'Other'
    }));

    applyFilters();
    renderSummaryPanel();
    renderScatterPlots();
    renderCorrelationMatrix();
}

function populateFamilyFilter() {
    const families = [...new Set(state.allModels.map(m => m.family))].sort();
    const sel = document.getElementById('filter-family');
    sel.innerHTML = '<option value="all">All Types</option>';
    families.forEach(f => {
        const label = FAMILY_LABELS[f] || f;
        const opt = document.createElement('option');
        opt.value = f;
        opt.textContent = label;
        sel.appendChild(opt);
    });
}

// ===== FILTERING & SORTING =====
function applyFilters() {
    let models = state.allModels;

    // Search
    if (state.filters.search) {
        models = models.filter(m =>
            m.model.toLowerCase().includes(state.filters.search) ||
            m.family_label.toLowerCase().includes(state.filters.search)
        );
    }

    // Family
    if (state.filters.family !== 'all') {
        models = models.filter(m => m.family === state.filters.family);
    }

    // Min trades
    models = models.filter(m => (m.n_trades || 0) >= state.filters.minTrades);

    // Champions
    if (state.filters.championsOnly) {
        models = models.filter(m => m.is_champion);
    }

    // Profitable
    if (state.filters.profitableOnly) {
        const slip = state.filters.slippage;
        models = models.filter(m => {
            const ret = m.summary?.[slip]?.cum_return || 0;
            return ret > 0;
        });
    }

    // Has trades
    if (state.filters.hasTradesOnly) {
        models = models.filter(m => (m.n_trades || 0) >= 1);
    }

    state.filteredModels = models;
    sortTable(state.sortColumn, true);  // Re-sort after filtering
    renderTable();
    renderSummaryPanel();
    renderScatterPlots();
}

function sortTable(col, skipRender = false) {
    if (col === state.sortColumn) {
        state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        state.sortColumn = col;
        state.sortDir = (col === 'model' || col === 'family') ? 'asc' : 'desc';
    }

    const slip = state.filters.slippage;
    state.filteredModels.sort((a, b) => {
        let aVal, bVal;
        switch (col) {
            case 'family':
                aVal = a.family_label || '';
                bVal = b.family_label || '';
                break;
            case 'model':
                aVal = a.model || '';
                bVal = b.model || '';
                break;
            case 'champion':
                aVal = a.is_champion ? 1 : 0;
                bVal = b.is_champion ? 1 : 0;
                break;
            case 'return':
                aVal = a.summary?.[slip]?.cum_return || -999;
                bVal = b.summary?.[slip]?.cum_return || -999;
                break;
            case 'sharpe':
                aVal = a.summary?.[slip]?.sharpe || -999;
                bVal = b.summary?.[slip]?.sharpe || -999;
                break;
            case 'max_dd':
                aVal = a.summary?.[slip]?.max_dd || -999;
                bVal = b.summary?.[slip]?.max_dd || -999;
                break;
            case 'trades':
                aVal = a.n_trades || 0;
                bVal = b.n_trades || 0;
                break;
            case 'hit_rate':
                aVal = a.hit_rate || 0;
                bVal = b.hit_rate || 0;
                break;
            case 'avg_hold':
                aVal = a.avg_hold_trading_minutes || 0;
                bVal = b.avg_hold_trading_minutes || 0;
                break;
            case 'status':
                aVal = (a.n_trades || 0) > 0 ? 1 : 0;
                bVal = (b.n_trades || 0) > 0 ? 1 : 0;
                break;
            default:
                aVal = 0; bVal = 0;
        }

        if (typeof aVal === 'string') {
            return state.sortDir === 'asc'
                ? aVal.localeCompare(bVal)
                : bVal.localeCompare(aVal);
        }
        return state.sortDir === 'asc' ? aVal - bVal : bVal - aVal;
    });

    if (!skipRender) renderTable();
}

// ===== TABLE RENDERING =====
function renderTable() {
    const tbody = document.getElementById('models-tbody');
    tbody.innerHTML = '';

    if (state.filteredModels.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" style="text-align:center; padding:2rem; color:var(--text-dim);">No models match filters</td></tr>';
        updateFilterSummary();
        return;
    }

    const slip = state.filters.slippage;

    state.filteredModels.forEach(m => {
        const summary = m.summary?.[slip] || {};
        const ret = summary.cum_return || 0;
        const sharpe = summary.sharpe || 0;
        const maxDD = summary.max_dd || 0;

        const tr = document.createElement('tr');
        tr.dataset.modelId = m.id;
        tr.classList.add(ret > 0 ? 'profitable' : ret < 0 ? 'losing' : 'neutral');
        if ((m.n_trades || 0) === 0) tr.classList.add('no-trades');
        if (m.id === state.selectedModelId) tr.classList.add('selected');

        tr.innerHTML = `
            <td><span class="family-label">${m.family_label}</span></td>
            <td>
                <span class="model-name">${m.model}</span>
                ${m.horizon !== 'n/a' ? `<span class="model-horizon">${m.horizon}</span>` : ''}
            </td>
            <td class="center">${m.is_champion ? '<span class="champion-badge">★</span>' : ''}</td>
            <td class="numeric ${ret >= 0 ? 'value-positive' : 'value-negative'}">
                ${fmtPercent(ret)}
            </td>
            <td class="numeric ${sharpe >= 0 ? 'value-positive' : 'value-negative'}">
                ${fmtNum(sharpe, 2)}
            </td>
            <td class="numeric ${maxDD >= 0 ? 'value-neutral' : 'value-negative'}">
                ${fmtPercent(maxDD)}
            </td>
            <td class="numeric">
                ${m.n_trades || 0}
                <div class="trade-breakdown">L:${m.n_long || 0} / S:${m.n_short || 0}</div>
            </td>
            <td class="numeric">${fmtPercent(m.hit_rate || 0)}</td>
            <td class="numeric">${fmtHold(m.avg_hold_trading_minutes || 0)}</td>
            <td class="center">
                <span class="status-dot ${(m.n_trades || 0) > 0 ? 'status-active' : 'status-inactive'}"></span>
                ${(m.n_trades || 0) > 0 ? 'Active' : 'No trades'}
            </td>
        `;

        tr.addEventListener('click', () => selectModel(m.id));
        tbody.appendChild(tr);
    });

    updateFilterSummary();
}

function updateFilterSummary() {
    document.getElementById('filter-summary').innerHTML =
        `Showing <strong>${state.filteredModels.length}</strong> of <strong>${state.allModels.length}</strong> models`;
}

// ===== DETAIL PANEL =====
function selectModel(modelId) {
    state.selectedModelId = modelId;
    const model = state.allModels.find(m => m.id === modelId);
    if (!model) return;

    // Update table selection
    document.querySelectorAll('#models-tbody tr').forEach(tr => {
        tr.classList.toggle('selected', tr.dataset.modelId === modelId);
    });

    renderDetailPanel(model);
    document.getElementById('detail-panel').classList.add('open');
    document.getElementById('detail-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeDetailPanel() {
    state.selectedModelId = null;
    document.getElementById('detail-panel').classList.remove('open');
    document.querySelectorAll('#models-tbody tr.selected').forEach(tr => tr.classList.remove('selected'));
}

function navigatePrev() {
    const idx = state.filteredModels.findIndex(m => m.id === state.selectedModelId);
    if (idx > 0) selectModel(state.filteredModels[idx - 1].id);
}

function navigateNext() {
    const idx = state.filteredModels.findIndex(m => m.id === state.selectedModelId);
    if (idx < state.filteredModels.length - 1) selectModel(state.filteredModels[idx + 1].id);
}

function renderDetailPanel(model) {
    // Title
    document.getElementById('detail-title').textContent =
        `${model.model} ${model.horizon !== 'n/a' ? `[${model.horizon}]` : ''} — ${model.family_label}`;

    // Metrics
    const slip = state.filters.slippage;
    const summary = model.summary?.[slip] || {};
    const grossRet = model.summary?.gross?.cum_return || 0;
    const netRet = summary.cum_return || 0;

    document.getElementById('detail-metrics').innerHTML = `
        <div class="metric-item">
            <div class="metric-label">Cum Return (Net)</div>
            <div class="metric-value ${netRet >= 0 ? 'value-positive' : 'value-negative'}">${fmtPercent(netRet)}</div>
            <div class="metric-sub">Gross: ${fmtPercent(grossRet)}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Sharpe (Net)</div>
            <div class="metric-value">${fmtNum(summary.sharpe || 0, 2)}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Max Drawdown</div>
            <div class="metric-value value-negative">${fmtPercent(summary.max_dd || 0)}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Trades</div>
            <div class="metric-value">${model.n_trades || 0}</div>
            <div class="metric-sub">L: ${model.n_long || 0} · S: ${model.n_short || 0} · Open: ${model.n_open_at_end || 0}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Hit Rate</div>
            <div class="metric-value">${fmtPercent(model.hit_rate || 0)}</div>
        </div>
        <div class="metric-item">
            <div class="metric-label">Avg Hold</div>
            <div class="metric-value">${fmtHold(model.avg_hold_trading_minutes || 0)}</div>
        </div>
    `;

    // Render charts
    renderEquityChart(model);
    renderDrawdownChart(model);
    renderCalibrationChart(model);
    renderRollingICChart(model);
    renderTickersChart(model);
    renderMonthlyChart(model);
}

// ===== CHART RENDERING =====
function renderEquityChart(model) {
    const slip = state.filters.slippage;
    const netKey = `equity_net_${slip}`;
    const gross = model.equity_gross || [];
    const net = model[netKey] || [];

    const traces = [];
    if (gross.length) {
        traces.push({
            x: gross.map(p => p.date),
            y: gross.map(p => p.value),
            mode: 'lines',
            name: 'Gross alpha',
            line: { color: '#c8a84e', width: 1.5, dash: 'dot' },
            hovertemplate: '%{x}<br>Gross: %{y:.4f}<extra></extra>'
        });
    }
    if (net.length) {
        traces.push({
            x: net.map(p => p.date),
            y: net.map(p => p.value),
            mode: 'lines',
            name: `Net (${slip})`,
            line: { color: '#22c55e', width: 2.5 },
            hovertemplate: '%{x}<br>Net: %{y:.4f}<extra></extra>'
        });
    }

    Plotly.react('chart-equity', traces, getPlotLayout(), { displayModeBar: false, responsive: true });
}

function renderDrawdownChart(model) {
    const slip = state.filters.slippage;
    const netKey = `equity_net_${slip}`;
    const net = model[netKey] || [];
    if (!net.length) { Plotly.purge('chart-dd'); return; }

    let peak = net[0].value;
    const dd = net.map(p => {
        if (p.value > peak) peak = p.value;
        return { date: p.date, dd: (p.value / peak - 1) * 100 };
    });

    Plotly.react('chart-dd', [{
        x: dd.map(p => p.date),
        y: dd.map(p => p.dd),
        fill: 'tozeroy',
        mode: 'lines',
        fillcolor: '#ef444440',
        line: { color: '#ef4444', width: 1.5 },
        hovertemplate: '%{x}<br>DD: %{y:.2f}%<extra></extra>',
        name: 'Drawdown'
    }], getPlotLayout({ ticksuffix: '%' }), { displayModeBar: false, responsive: true });
}

function renderCalibrationChart(model) {
    const cal = model.calibration || [];
    if (!cal.length || cal.every(b => b.n === 0)) {
        Plotly.purge('chart-calib');
        document.getElementById('calib-note').textContent = 'No calibration data — model scores never reached trading thresholds.';
        return;
    }

    const colors = cal.map(b => {
        if (b.avg_return_pct == null) return '#64748b';
        return b.avg_return_pct >= 0 ? '#22c55e' : '#ef4444';
    });

    Plotly.react('chart-calib', [{
        x: cal.map(b => b.bucket),
        y: cal.map(b => b.avg_return_pct == null ? 0 : b.avg_return_pct),
        type: 'bar',
        marker: { color: colors },
        text: cal.map(b => b.n > 0
            ? `n=${b.n} hit=${b.hit_rate != null ? (b.hit_rate * 100).toFixed(0) + '%' : '—'}`
            : ''),
        textposition: 'auto',
        textfont: { size: 9, color: '#0a1628' },
        hovertemplate: 'Bucket %{x}<br>Avg ret: %{y:+.4f}%<br>%{text}<extra></extra>'
    }], getPlotLayout({ ticksuffix: '%', tickangle: -45 }), { displayModeBar: false, responsive: true });

    document.getElementById('calib-note').textContent =
        'Buckets show n + hit rate. Hit = sign(score) matches sign(return), zero-return obs excluded.';
}

function renderRollingICChart(model) {
    const series = (model.rolling_ic_30d || []).filter(p => p.ic_dir != null);
    if (!series.length) { Plotly.purge('chart-rolling-ic'); return; }

    Plotly.react('chart-rolling-ic', [{
        x: series.map(p => p.date),
        y: series.map(p => p.ic_dir),
        mode: 'lines+markers',
        name: 'IC dir (30d)',
        line: { color: '#c8a84e', width: 2 },
        marker: { color: '#c8a84e', size: 5 },
        hovertemplate: '%{x}<br>IC: %{y:+.4f}<br>n=%{customdata}<extra></extra>',
        customdata: series.map(p => p.n)
    }], getPlotLayout({ tickformat: '+.3f', zeroline: true }), { displayModeBar: false, responsive: true });
}

function renderTickersChart(model) {
    const tickers = (model.per_ticker || []).filter(t => t.cum_pnl != null && t.cum_pnl !== 0);
    if (!tickers.length) {
        Plotly.purge('chart-tickers');
        document.getElementById('ticker-note').textContent = 'No ticker contribution data yet.';
        return;
    }

    const sorted = tickers
        .sort((a, b) => Math.abs(b.cum_pnl) - Math.abs(a.cum_pnl))
        .slice(0, 20)
        .sort((a, b) => b.cum_pnl - a.cum_pnl);

    Plotly.react('chart-tickers', [{
        type: 'bar',
        orientation: 'h',
        x: sorted.map(t => t.cum_pnl * 100),
        y: sorted.map(t => t.ticker),
        marker: { color: sorted.map(t => t.cum_pnl >= 0 ? '#22c55e' : '#ef4444') },
        text: sorted.map(t => `n=${t.n_trades} L:${t.n_long}/S:${t.n_short}`),
        textposition: 'outside',
        textfont: { size: 9, color: '#e2e8f0' },
        hovertemplate: '%{y}: %{x:+.3f}% cum<br>%{text}<extra></extra>'
    }], getPlotLayout({ ticksuffix: '%', xaxis: { tickformat: '+.2f' } }), { displayModeBar: false, responsive: true });

    document.getElementById('ticker-note').textContent = 'Top 20 by |contribution|. Net P&L after slippage.';
}

function renderMonthlyChart(model) {
    // Placeholder for now — will implement in Day 2 after backend adds monthly_pnl data
    const el = document.getElementById('chart-monthly');
    el.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-dim);">Monthly breakdown coming in Day 2 (backend data needed)</div>';
}

// ===== CORRELATION MATRIX =====
function renderCorrelationMatrix() {
    const corr = state.data?.correlation || {};
    if (!corr.labels || corr.labels.length < 2 || !corr.matrix) {
        document.getElementById('chart-correlation').innerHTML =
            '<div style="padding:2rem; text-align:center; color:var(--text-dim);">Correlation matrix needs ≥2 champion models with overlapping returns.</div>';
        return;
    }

    Plotly.react('chart-correlation', [{
        type: 'heatmap',
        z: corr.matrix,
        x: corr.labels,
        y: corr.labels,
        zmin: -1,
        zmax: 1,
        colorscale: [
            [0, '#ef4444'], [0.4, '#1e3050'], [0.5, '#162240'],
            [0.6, '#1e3050'], [1, '#22c55e']
        ],
        hovertemplate: '%{y} ↔ %{x}<br>ρ = %{z:.2f}<extra></extra>',
        showscale: true,
        colorbar: { thickness: 12, len: 0.8, tickfont: { size: 9, color: '#e2e8f0' } }
    }], {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { family: 'DM Sans, sans-serif', color: '#e2e8f0', size: 10 },
        margin: { l: 100, r: 40, t: 10, b: 100 },
        xaxis: { tickangle: -45, tickfont: { size: 9 }, automargin: true },
        yaxis: { tickfont: { size: 9 }, automargin: true }
    }, { displayModeBar: false, responsive: true });
}

// ===== SUMMARY STATISTICS PANEL =====
function renderSummaryPanel() {
    const slip = state.filters.slippage;
    const withTrades = state.allModels.filter(m => (m.n_trades || 0) >= 1);
    
    // Data Inventory
    document.getElementById('stat-total-models').textContent = state.allModels.length;
    document.getElementById('stat-with-trades').textContent = withTrades.length;
    document.getElementById('stat-champions').textContent = state.allModels.filter(m => m.is_champion).length;
    
    // Performance Aggregate
    const profitable = withTrades.filter(m => (m.summary?.[slip]?.cum_return || 0) > 0);
    document.getElementById('stat-profitable').textContent = profitable.length;
    
    const returns = withTrades.map(m => m.summary?.[slip]?.cum_return || 0);
    const avgReturn = returns.length ? (returns.reduce((a, b) => a + b, 0) / returns.length) : 0;
    const avgRetEl = document.getElementById('stat-avg-return');
    avgRetEl.textContent = fmtPercent(avgReturn);
    avgRetEl.className = 'summary-value ' + (avgReturn >= 0 ? 'positive' : 'negative');
    
    // Risk Aggregate
    const sharpes = withTrades.map(m => m.summary?.[slip]?.sharpe || 0);
    const avgSharpe = sharpes.length ? (sharpes.reduce((a, b) => a + b, 0) / sharpes.length) : 0;
    const sharpeEl = document.getElementById('stat-avg-sharpe');
    sharpeEl.textContent = fmtNum(avgSharpe, 2);
    sharpeEl.className = 'summary-value ' + (avgSharpe >= 0 ? 'positive' : 'negative');
    
    // Execution Stats
    const totalTrades = state.allModels.reduce((sum, m) => sum + (m.n_trades || 0), 0);
    const totalLong = state.allModels.reduce((sum, m) => sum + (m.n_long || 0), 0);
    const totalShort = state.allModels.reduce((sum, m) => sum + (m.n_short || 0), 0);
    document.getElementById('stat-total-trades').textContent = totalTrades;
    document.getElementById('stat-long-short').textContent = `L:${totalLong} / S:${totalShort}`;
    
    // Average hold time (weighted by trades)
    let totalHoldMinutes = 0;
    let totalTradeCount = 0;
    withTrades.forEach(m => {
        const trades = m.n_trades || 0;
        const hold = m.avg_hold_trading_minutes || 0;
        totalHoldMinutes += trades * hold;
        totalTradeCount += trades;
    });
    const avgHold = totalTradeCount > 0 ? totalHoldMinutes / totalTradeCount : 0;
    document.getElementById('stat-avg-hold').textContent = fmtHold(avgHold);
}

// ===== SCATTER PLOTS =====
function renderScatterPlots() {
    const slip = state.filters.slippage;
    const models = state.filteredModels.filter(m => (m.n_trades || 0) >= 1);
    
    if (models.length === 0) {
        ['chart-scatter-return-hit', 'chart-scatter-sharpe-dd', 'chart-scatter-trades-hold'].forEach(id => {
            document.getElementById(id).innerHTML = 
                '<div style="padding:2rem; text-align:center; color:var(--text-dim);">No models with trades matching filters</div>';
        });
        return;
    }
    
    // Scatter 1: Return vs Hit Rate
    renderReturnHitScatter(models, slip);
    
    // Scatter 2: Sharpe vs Max DD
    renderSharpeDrawdownScatter(models, slip);
    
    // Scatter 3: Trades vs Hold Time
    renderTradesHoldScatter(models);
}

function renderReturnHitScatter(models, slip) {
    const trace = {
        x: models.map(m => (m.hit_rate || 0) * 100),
        y: models.map(m => (m.summary?.[slip]?.cum_return || 0) * 100),
        mode: 'markers',
        type: 'scatter',
        marker: {
            size: models.map(m => Math.sqrt(m.n_trades || 1) * 3 + 5),
            color: models.map(m => FAMILY_COLORS[m.family] || '#64748b'),
            opacity: 0.7,
            line: { width: 1, color: '#0a1628' }
        },
        text: models.map(m => 
            `<b>${m.model}</b><br>` +
            `Family: ${m.family_label}<br>` +
            `Return: ${fmtPercent(m.summary?.[slip]?.cum_return || 0)}<br>` +
            `Hit Rate: ${fmtPercent(m.hit_rate || 0)}<br>` +
            `Trades: ${m.n_trades}<br>` +
            `Sharpe: ${fmtNum(m.summary?.[slip]?.sharpe || 0, 2)}`
        ),
        hovertemplate: '%{text}<extra></extra>',
        customdata: models.map(m => m.id)
    };
    
    const layout = {
        ...getPlotLayout(),
        xaxis: { 
            title: 'Hit Rate (%)', 
            ticksuffix: '%', 
            range: [0, 100],
            gridcolor: '#1e3050',
            zerolinecolor: '#1e3050'
        },
        yaxis: { 
            title: 'Cumulative Return (%)', 
            ticksuffix: '%', 
            tickformat: '+.1f', 
            zeroline: true,
            gridcolor: '#1e3050',
            zerolinecolor: '#64748b'
        },
        shapes: [
            { 
                type: 'line', 
                x0: 50, x1: 50, 
                y0: 0, y1: 1, 
                yref: 'paper', 
                line: { color: '#64748b', width: 1, dash: 'dash' } 
            },
            { 
                type: 'line', 
                x0: 0, x1: 1, 
                xref: 'paper', 
                y0: 0, y1: 0, 
                line: { color: '#64748b', width: 1, dash: 'dash' } 
            }
        ],
        annotations: [
            { 
                x: 0.75, y: 0.95, 
                xref: 'paper', yref: 'paper',
                text: 'Winners', 
                showarrow: false, 
                font: { size: 11, color: '#22c55e' },
                bgcolor: 'rgba(34, 197, 94, 0.1)',
                borderpad: 4
            },
            { 
                x: 0.25, y: 0.05, 
                xref: 'paper', yref: 'paper',
                text: 'Losers', 
                showarrow: false, 
                font: { size: 11, color: '#ef4444' },
                bgcolor: 'rgba(239, 68, 68, 0.1)',
                borderpad: 4
            }
        ]
    };
    
    Plotly.react('chart-scatter-return-hit', [trace], layout, { displayModeBar: false, responsive: true });
    
    // Add click handler
    const chartEl = document.getElementById('chart-scatter-return-hit');
    chartEl.removeAllListeners && chartEl.removeAllListeners('plotly_click');
    chartEl.on('plotly_click', data => {
        const modelId = data.points[0].customdata;
        selectModel(modelId);
    });
}

function renderSharpeDrawdownScatter(models, slip) {
    const trace = {
        x: models.map(m => (m.summary?.[slip]?.max_dd || 0) * 100),
        y: models.map(m => m.summary?.[slip]?.sharpe || 0),
        mode: 'markers',
        type: 'scatter',
        marker: {
            size: models.map(m => Math.sqrt(m.n_trades || 1) * 3 + 5),
            color: models.map(m => FAMILY_COLORS[m.family] || '#64748b'),
            opacity: 0.7,
            line: { width: 1, color: '#0a1628' }
        },
        text: models.map(m => 
            `<b>${m.model}</b><br>` +
            `Family: ${m.family_label}<br>` +
            `Sharpe: ${fmtNum(m.summary?.[slip]?.sharpe || 0, 2)}<br>` +
            `Max DD: ${fmtPercent(m.summary?.[slip]?.max_dd || 0)}<br>` +
            `Return: ${fmtPercent(m.summary?.[slip]?.cum_return || 0)}<br>` +
            `Trades: ${m.n_trades}`
        ),
        hovertemplate: '%{text}<extra></extra>',
        customdata: models.map(m => m.id)
    };
    
    const layout = {
        ...getPlotLayout(),
        xaxis: { 
            title: 'Max Drawdown (%)', 
            ticksuffix: '%',
            tickformat: '.1f',
            gridcolor: '#1e3050',
            zerolinecolor: '#1e3050'
        },
        yaxis: { 
            title: 'Sharpe Ratio', 
            tickformat: '.2f',
            zeroline: true,
            gridcolor: '#1e3050',
            zerolinecolor: '#64748b'
        },
        shapes: [
            { 
                type: 'line', 
                x0: -10, x1: -10, 
                y0: 0, y1: 1, 
                yref: 'paper', 
                line: { color: '#64748b', width: 1, dash: 'dash' } 
            },
            { 
                type: 'line', 
                x0: 0, x1: 1, 
                xref: 'paper', 
                y0: 1.0, y1: 1.0, 
                line: { color: '#64748b', width: 1, dash: 'dash' } 
            }
        ],
        annotations: [
            { 
                x: 0.25, y: 0.95, 
                xref: 'paper', yref: 'paper',
                text: 'Smooth', 
                showarrow: false, 
                font: { size: 11, color: '#22c55e' },
                bgcolor: 'rgba(34, 197, 94, 0.1)',
                borderpad: 4
            }
        ]
    };
    
    Plotly.react('chart-scatter-sharpe-dd', [trace], layout, { displayModeBar: false, responsive: true });
    
    const chartEl = document.getElementById('chart-scatter-sharpe-dd');
    chartEl.removeAllListeners && chartEl.removeAllListeners('plotly_click');
    chartEl.on('plotly_click', data => {
        const modelId = data.points[0].customdata;
        selectModel(modelId);
    });
}

function renderTradesHoldScatter(models) {
    const trace = {
        x: models.map(m => m.n_trades || 0),
        y: models.map(m => m.avg_hold_trading_minutes || 0),
        mode: 'markers',
        type: 'scatter',
        marker: {
            size: 12,
            color: models.map(m => FAMILY_COLORS[m.family] || '#64748b'),
            opacity: 0.7,
            line: { width: 1, color: '#0a1628' }
        },
        text: models.map(m => 
            `<b>${m.model}</b><br>` +
            `Family: ${m.family_label}<br>` +
            `Trades: ${m.n_trades}<br>` +
            `Avg Hold: ${fmtHold(m.avg_hold_trading_minutes || 0)}<br>` +
            `L:${m.n_long || 0} / S:${m.n_short || 0}`
        ),
        hovertemplate: '%{text}<extra></extra>',
        customdata: models.map(m => m.id)
    };
    
    const layout = {
        ...getPlotLayout(),
        xaxis: { 
            title: 'Number of Trades', 
            type: 'log',
            gridcolor: '#1e3050',
            zerolinecolor: '#1e3050'
        },
        yaxis: { 
            title: 'Avg Hold Time (minutes)', 
            tickformat: '.0f',
            gridcolor: '#1e3050',
            zerolinecolor: '#1e3050'
        },
        shapes: [
            { 
                type: 'line', 
                x0: 10, x1: 10, 
                y0: 0, y1: 1, 
                yref: 'paper', 
                line: { color: '#64748b', width: 1, dash: 'dash' } 
            },
            { 
                type: 'line', 
                x0: 0, x1: 1, 
                xref: 'paper', 
                y0: 60, y1: 60, 
                line: { color: '#64748b', width: 1, dash: 'dot' } 
            },
            { 
                type: 'line', 
                x0: 0, x1: 1, 
                xref: 'paper', 
                y0: 240, y1: 240, 
                line: { color: '#64748b', width: 1, dash: 'dot' } 
            }
        ],
        annotations: [
            { 
                x: 0.05, y: 60, 
                xref: 'paper', yref: 'y',
                text: '60m (2 fires)', 
                showarrow: false, 
                font: { size: 9, color: '#64748b' },
                xanchor: 'left'
            },
            { 
                x: 0.05, y: 240, 
                xref: 'paper', yref: 'y',
                text: '240m (8 fires)', 
                showarrow: false, 
                font: { size: 9, color: '#64748b' },
                xanchor: 'left'
            }
        ]
    };
    
    Plotly.react('chart-scatter-trades-hold', [trace], layout, { displayModeBar: false, responsive: true });
    
    const chartEl = document.getElementById('chart-scatter-trades-hold');
    chartEl.removeAllListeners && chartEl.removeAllListeners('plotly_click');
    chartEl.on('plotly_click', data => {
        const modelId = data.points[0].customdata;
        selectModel(modelId);
    });
}

// ===== EXPORT =====
function exportCSV() {
    const slip = state.filters.slippage;
    const model = state.allModels.find(m => m.id === state.selectedModelId);
    if (!model) return;

    const summary = model.summary?.[slip] || {};
    const rows = [
        ['Model', model.model],
        ['Horizon', model.horizon],
        ['Family', model.family_label],
        ['Cum Return (Net)', fmtPercent(summary.cum_return || 0)],
        ['Cum Return (Gross)', fmtPercent(model.summary?.gross?.cum_return || 0)],
        ['Sharpe', fmtNum(summary.sharpe || 0, 2)],
        ['Max DD', fmtPercent(summary.max_dd || 0)],
        ['Trades', model.n_trades || 0],
        ['Long', model.n_long || 0],
        ['Short', model.n_short || 0],
        ['Hit Rate', fmtPercent(model.hit_rate || 0)],
        ['Avg Hold (min)', Math.round(model.avg_hold_trading_minutes || 0)]
    ];

    const csv = rows.map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${model.model}_${slip}_markout.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// ===== UTILITIES =====
function fmtPercent(v) {
    if (v == null) return '—';
    const val = v * 100;
    return (val >= 0 ? '+' : '') + val.toFixed(2) + '%';
}

function fmtNum(v, dp = 2) {
    return v == null ? '—' : v.toFixed(dp);
}

function fmtHold(minutes) {
    if (minutes < 60) return Math.round(minutes) + 'm';
    return (minutes / 60).toFixed(1) + 'h';
}

function getPlotLayout(opts = {}) {
    return {
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { family: 'DM Sans, sans-serif', color: '#e2e8f0', size: 11 },
        margin: { l: 60, r: 30, t: 10, b: 50 },
        xaxis: {
            gridcolor: '#1e3050',
            zerolinecolor: opts.zeroline ? '#64748b' : '#1e3050',
            tickfont: { size: 10 },
            tickangle: opts.tickangle || 0
        },
        yaxis: {
            gridcolor: '#1e3050',
            zerolinecolor: opts.zeroline ? '#64748b' : '#1e3050',
            tickformat: opts.tickformat || '.3f',
            ticksuffix: opts.ticksuffix || '',
            tickfont: { size: 10 }
        },
        showlegend: false,
        hovermode: 'x unified'
    };
}
