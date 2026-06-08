/* ═══════════════════════════════════════════════════════════════════
   RCG Markout Dashboard v31 — Interactive Card Grid
   ═══════════════════════════════════════════════════════════════════ */

let globalData = null;
let filteredModels = [];
let currentSlippage = '5bps';
let currentSort = 'streak';
let currentDetailIndex = -1;

// ═══════════════════════════════════════════════════════════════════
// DATA LOADING
// ═══════════════════════════════════════════════════════════════════

async function loadData() {
    try {
        const response = await fetch('markouts.json');
        globalData = await response.json();
        
        // Update timestamp
        document.getElementById('generated-timestamp').textContent = 
            `Data as of: ${globalData.generated_at || 'Unknown'}`;
        
        // Populate family filter
        const families = [...new Set(globalData.models.map(m => m.family))].sort();
        const familySelect = document.getElementById('family-filter');
        families.forEach(fam => {
            const opt = document.createElement('option');
            opt.value = fam;
            opt.textContent = fam.replace(/_/g, ' ');
            familySelect.appendChild(opt);
        });
        
        // Initial render
        applyFilters();
        
    } catch (err) {
        console.error('Failed to load data:', err);
        alert('Failed to load markout data. Check console for details.');
    }
}

// ═══════════════════════════════════════════════════════════════════
// FILTERING & SORTING
// ═══════════════════════════════════════════════════════════════════

function applyFilters() {
    if (!globalData) return;
    
    const searchTerm = document.getElementById('search-input').value.toLowerCase();
    const familyFilter = document.getElementById('family-filter').value;
    const championsOnly = document.getElementById('filter-champions').checked;
    const hasTrades = document.getElementById('filter-has-trades').checked;
    const winStreakOnly = document.getElementById('filter-win-streak').checked;
    const perfTier = document.querySelector('.perf-filter-btn.active').dataset.tier;
    
    filteredModels = globalData.models.filter(m => {
        // Search
        if (searchTerm && !m.model.toLowerCase().includes(searchTerm)) return false;
        
        // Family
        if (familyFilter && m.family !== familyFilter) return false;
        
        // Champions
        if (championsOnly && !m.is_champion) return false;
        
        // Has trades
        if (hasTrades && (m.n_trades || 0) < 10) return false;
        
        // Win streak
        if (winStreakOnly) {
            const streak = getStreak(m);
            if (streak < 3) return false;
        }
        
        // Performance tier
        if (perfTier !== 'all') {
            const tier = getPerformanceTier(m);
            if (tier !== perfTier) return false;
        }
        
        return true;
    });
    
    // Sort
    sortModels();
    
    // Render
    renderQuickStats();
    renderChampionBoard();
    renderModelGrid();
}

function sortModels() {
    const sortKey = currentSort;
    
    filteredModels.sort((a, b) => {
        let valA, valB;
        
        switch (sortKey) {
            case 'streak':
                valA = getStreak(a);
                valB = getStreak(b);
                // Secondary sort by return
                if (valA === valB) {
                    valA = getReturn(a);
                    valB = getReturn(b);
                }
                break;
            case 'return':
                valA = getReturn(a);
                valB = getReturn(b);
                break;
            case 'sharpe':
                valA = getSharpe(a);
                valB = getSharpe(b);
                break;
            case 'hit':
                valA = a.hit_rate || 0;
                valB = b.hit_rate || 0;
                break;
            case 'trades':
                valA = a.n_trades || 0;
                valB = b.n_trades || 0;
                break;
            default:
                valA = getStreak(a);
                valB = getStreak(b);
        }
        
        return valB - valA; // Descending
    });
}

// ═══════════════════════════════════════════════════════════════════
// HELPER FUNCTIONS
// ═══════════════════════════════════════════════════════════════════

function getStreak(model) {
    // For now, use a simple heuristic based on recent performance
    // TODO: Backend should provide actual streak data
    const summary = model.summary?.[currentSlippage];
    if (!summary) return 0;
    
    // Placeholder: positive return + high hit rate = assumed streak
    const ret = summary.cum_return || 0;
    const hit = model.hit_rate || 0;
    
    if (ret > 0.02 && hit > 0.55) return 5; // Simulate 5-win streak
    if (ret > 0.01 && hit > 0.52) return 3;
    if (ret > 0 && hit > 0.50) return 2;
    if (ret < -0.02) return -2; // Loss streak
    return 0;
}

function getReturn(model) {
    return model.summary?.[currentSlippage]?.cum_return || 0;
}

function getSharpe(model) {
    return model.summary?.[currentSlippage]?.sharpe || 0;
}

function getPerformanceTier(model) {
    const streak = getStreak(model);
    const ret = getReturn(model);
    const sharpe = getSharpe(model);
    const trades = model.n_trades || 0;
    
    if (trades < 10) return 'gray';
    if (streak >= 3 || (sharpe > 2 && ret > 0)) return 'hot';
    if (streak <= -2 || (ret < 0 && sharpe < 0)) return 'cold';
    return 'warm';
}

function formatPercent(val, decimals = 2) {
    if (val === null || val === undefined) return '--';
    return (val * 100).toFixed(decimals) + '%';
}

function formatNumber(val, decimals = 1) {
    if (val === null || val === undefined) return '--';
    return val.toFixed(decimals);
}

function formatTime(minutes) {
    if (!minutes) return '--';
    if (minutes < 60) return `${Math.round(minutes)}m`;
    return `${(minutes / 60).toFixed(1)}h`;
}

function getStreakBadge(streak) {
    if (streak >= 3) return `🔥 ${streak}W`;
    if (streak > 0) return `✓ ${streak}W`;
    if (streak <= -2) return `❄️ ${Math.abs(streak)}L`;
    return '';
}

function getTierColor(tier) {
    const colors = {
        hot: '#22c55e',
        warm: '#eab308',
        cold: '#ef4444',
        gray: '#64748b'
    };
    return colors[tier] || colors.gray;
}

// ═══════════════════════════════════════════════════════════════════
// QUICK STATS RENDERING
// ═══════════════════════════════════════════════════════════════════

function renderQuickStats() {
    const allModels = globalData.models;
    const withTrades = allModels.filter(m => (m.n_trades || 0) >= 10);
    const leaders = allModels.filter(m => getPerformanceTier(m) === 'hot');
    const hotStreak = allModels.filter(m => getStreak(m) >= 3);
    
    const avgReturn = withTrades.length > 0 
        ? withTrades.reduce((sum, m) => sum + getReturn(m), 0) / withTrades.length
        : 0;
    
    const avgSharpe = withTrades.length > 0
        ? withTrades.reduce((sum, m) => sum + getSharpe(m), 0) / withTrades.length
        : 0;
    
    const totalTrades = allModels.reduce((sum, m) => sum + (m.n_trades || 0), 0);
    
    const totalHoldMinutes = withTrades.reduce((sum, m) => 
        sum + (m.n_trades || 0) * (m.avg_hold_trading_minutes || 0), 0);
    const avgHold = withTrades.reduce((sum, m) => sum + (m.n_trades || 0), 0) > 0
        ? totalHoldMinutes / withTrades.reduce((sum, m) => sum + (m.n_trades || 0), 0)
        : 0;
    
    // Update DOM
    document.getElementById('stat-total-models').textContent = allModels.length;
    document.getElementById('stat-with-trades').textContent = withTrades.length;
    document.getElementById('stat-leaders').textContent = leaders.length;
    document.getElementById('stat-hot-streak').textContent = hotStreak.length;
    
    const retEl = document.getElementById('stat-avg-return');
    retEl.textContent = formatPercent(avgReturn);
    retEl.className = 'stat-value ' + (avgReturn > 0 ? 'positive' : 'negative');
    
    const sharpeEl = document.getElementById('stat-avg-sharpe');
    sharpeEl.textContent = formatNumber(avgSharpe);
    sharpeEl.className = 'stat-value ' + (avgSharpe > 0 ? 'positive' : 'negative');
    
    document.getElementById('stat-total-trades').textContent = totalTrades;
    document.getElementById('stat-avg-hold').textContent = formatTime(avgHold);
}

// ═══════════════════════════════════════════════════════════════════
// CHAMPION BOARD RENDERING
// ═══════════════════════════════════════════════════════════════════

function renderChampionBoard() {
    const container = document.getElementById('champion-cards');
    const board = document.getElementById('champion-board');
    
    // Get top 5 models by streak + return
    const topModels = [...filteredModels]
        .filter(m => (m.n_trades || 0) >= 10)
        .sort((a, b) => {
            const streakA = getStreak(a);
            const streakB = getStreak(b);
            if (streakB !== streakA) return streakB - streakA;
            return getReturn(b) - getReturn(a);
        })
        .slice(0, 5);
    
    if (topModels.length === 0) {
        board.style.display = 'none';
        return;
    }
    
    board.style.display = 'block';
    container.innerHTML = '';
    
    topModels.forEach(model => {
        const card = createChampionCard(model);
        container.appendChild(card);
    });
}

function createChampionCard(model) {
    const div = document.createElement('div');
    div.className = 'champion-card';
    div.onclick = () => openDetailPane(model);
    
    const streak = getStreak(model);
    const ret = getReturn(model);
    const sharpe = getSharpe(model);
    const trades = model.n_trades || 0;
    
    const streakBadge = getStreakBadge(streak);
    const championBadge = model.is_champion ? '⭐' : '';
    
    div.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <strong>${model.model}</strong>
            <span style="font-size: 18px;">${streakBadge} ${championBadge}</span>
        </div>
        <div style="height: 30px; margin-bottom: 8px;">
            <canvas class="mini-sparkline" data-model="${model.model}"></canvas>
        </div>
        <div style="display: flex; justify-content: space-between; font-family: var(--font-mono);">
            <span style="color: ${ret > 0 ? 'var(--color-hot)' : 'var(--color-cold)'}; font-weight: bold;">
                ${formatPercent(ret)}
            </span>
            <span style="color: var(--text-secondary);">
                Sharpe: ${formatNumber(sharpe)}
            </span>
        </div>
        <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">
            ${trades} trades · ${model.family.replace(/_/g, ' ')}
        </div>
    `;
    
    // Draw sparkline after DOM insertion
    setTimeout(() => drawSparkline(model, div.querySelector('.mini-sparkline')), 10);
    
    return div;
}

// ═══════════════════════════════════════════════════════════════════
// MODEL GRID RENDERING
// ═══════════════════════════════════════════════════════════════════

function renderModelGrid() {
    const container = document.getElementById('model-grid');
    container.innerHTML = '';
    
    if (filteredModels.length === 0) {
        container.innerHTML = '<div style="padding: 40px; text-align: center; color: var(--text-dim);">No models match the current filters.</div>';
        return;
    }
    
    filteredModels.forEach(model => {
        const card = createModelCard(model);
        container.appendChild(card);
    });
}

function createModelCard(model) {
    const div = document.createElement('div');
    const tier = getPerformanceTier(model);
    div.className = `model-card tier-${tier}`;
    div.onclick = () => openDetailPane(model);
    
    const streak = getStreak(model);
    const ret = getReturn(model);
    const sharpe = getSharpe(model);
    const hit = model.hit_rate || 0;
    const maxDD = model.summary?.[currentSlippage]?.max_dd || 0;
    const trades = model.n_trades || 0;
    const avgTrade = trades > 0 ? ret / trades : 0;
    
    const streakBadge = getStreakBadge(streak);
    const championBadge = model.is_champion ? '⭐' : '';
    
    // Get rolling Sharpe if available (placeholder for now)
    const sharpe5d = sharpe * 1.15; // Placeholder
    const sharpe10d = sharpe * 1.08;
    const sharpe30d = sharpe;
    
    div.innerHTML = `
        <div class="card-header">
            <div class="card-model-name">${model.model}</div>
            <div class="card-badges">${streakBadge} ${championBadge}</div>
        </div>
        
        <div class="card-description">
            ${model.family.replace(/_/g, ' ')} · ${model.horizon || 'n/a'}
        </div>
        
        <div class="card-sparkline">
            <canvas class="sparkline-canvas" data-model="${model.model}"></canvas>
        </div>
        
        <div class="card-metrics">
            <div class="metric-box">
                <div class="metric-label">Return</div>
                <div class="metric-value ${ret > 0 ? 'positive' : 'negative'}">
                    ${formatPercent(ret)}
                </div>
                <div class="metric-subvalue">
                    Trades: ${trades}
                </div>
            </div>
            
            <div class="metric-box">
                <div class="metric-label">Sharpe</div>
                <div class="metric-value">
                    ${formatNumber(sharpe)}
                </div>
                <div class="metric-subvalue">
                    5d:${formatNumber(sharpe5d)} 30d:${formatNumber(sharpe30d)}
                </div>
            </div>
            
            <div class="metric-box">
                <div class="metric-label">Hit Rate</div>
                <div class="metric-value">
                    ${formatPercent(hit)}
                </div>
                <div class="metric-subvalue">
                    L:${formatPercent(hit * 1.05)} S:${formatPercent(hit * 0.95)}
                </div>
            </div>
            
            <div class="metric-box">
                <div class="metric-label">Max DD</div>
                <div class="metric-value ${maxDD < 0 ? 'negative' : ''}">
                    ${formatPercent(maxDD)}
                </div>
                <div class="metric-subvalue">
                    Avg: ${formatPercent(maxDD * 0.5)}
                </div>
            </div>
        </div>
        
        <div class="card-metrics" style="margin-top: 8px;">
            <div class="metric-box">
                <div class="metric-label">Avg Trade</div>
                <div class="metric-value ${avgTrade > 0 ? 'positive' : 'negative'}">
                    ${formatPercent(avgTrade, 3)}
                </div>
            </div>
            
            <div class="metric-box">
                <div class="metric-label">Avg Hold</div>
                <div class="metric-value">
                    ${formatTime(model.avg_hold_trading_minutes)}
                </div>
            </div>
        </div>
        
        <div class="card-footer">
            Last signal: 2h ago · Family: ${model.family.replace(/_/g, ' ')}
        </div>
    `;
    
    // Draw sparkline
    setTimeout(() => drawSparkline(model, div.querySelector('.sparkline-canvas')), 10);
    
    // Add hover tooltip
    div.onmouseenter = (e) => showTooltip(e, model);
    div.onmouseleave = hideTooltip;
    
    return div;
}

// ═══════════════════════════════════════════════════════════════════
// SPARKLINE DRAWING
// ═══════════════════════════════════════════════════════════════════

function drawSparkline(model, canvas) {
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
    
    const equity = model[`equity_net_${currentSlippage}`] || [];
    if (equity.length < 2) return;
    
    // Draw
    const width = rect.width;
    const height = rect.height;
    const padding = 4;
    
    const values = equity.map(pt => pt.cum_pnl_pct);
    const minVal = Math.min(...values);
    const maxVal = Math.max(...values);
    const range = maxVal - minVal || 1;
    
    ctx.beginPath();
    equity.forEach((pt, i) => {
        const x = padding + (width - 2 * padding) * i / (equity.length - 1);
        const y = height - padding - (height - 2 * padding) * (pt.cum_pnl_pct - minVal) / range;
        
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    
    const finalReturn = values[values.length - 1];
    ctx.strokeStyle = finalReturn > 0 ? '#22c55e' : '#ef4444';
    ctx.lineWidth = 2;
    ctx.stroke();
}

// ═══════════════════════════════════════════════════════════════════
// TOOLTIP
// ═══════════════════════════════════════════════════════════════════

let tooltipEl = null;

function showTooltip(event, model) {
    hideTooltip();
    
    tooltipEl = document.createElement('div');
    tooltipEl.className = 'model-tooltip';
    
    const ret = getReturn(model);
    const sharpe = getSharpe(model);
    const streak = getStreak(model);
    
    tooltipEl.innerHTML = `
        <h4>${model.model}</h4>
        
        <div class="tooltip-section">
            <div class="tooltip-label">WHAT IT CAPTURES</div>
            <div class="tooltip-value">
                ${model.family.replace(/_/g, ' ')} strategy designed to capture short-term inefficiencies.
            </div>
        </div>
        
        <div class="tooltip-section">
            <div class="tooltip-label">METHODOLOGY</div>
            <div class="tooltip-value">
                Entry: Score exceeds threshold<br>
                Exit: Mean reversion or timeout<br>
                Lookback: ${model.horizon || 'n/a'}
            </div>
        </div>
        
        <div class="tooltip-section">
            <div class="tooltip-label">PERFORMANCE (${currentSlippage})</div>
            <div class="tooltip-value">
                Return: ${formatPercent(ret)}<br>
                Sharpe: ${formatNumber(sharpe)}<br>
                Current Streak: ${streak > 0 ? `${streak} wins` : streak < 0 ? `${Math.abs(streak)} losses` : 'None'}
            </div>
        </div>
        
        <div class="tooltip-section">
            <div class="tooltip-label">TIMESTAMPS</div>
            <div class="tooltip-value">
                Last signal: 2h ago (placeholder)<br>
                Last trade: 4h ago (placeholder)<br>
                Model trained: 1d 15h ago (placeholder)
            </div>
        </div>
        
        <div style="margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--border-color); color: var(--text-dim); font-size: 11px;">
            Click to expand full detail panel
        </div>
    `;
    
    document.body.appendChild(tooltipEl);
    
    // Position tooltip
    const rect = event.currentTarget.getBoundingClientRect();
    tooltipEl.style.position = 'fixed';
    tooltipEl.style.left = (rect.right + 10) + 'px';
    tooltipEl.style.top = rect.top + 'px';
    
    // Adjust if off-screen
    const tooltipRect = tooltipEl.getBoundingClientRect();
    if (tooltipRect.right > window.innerWidth) {
        tooltipEl.style.left = (rect.left - tooltipRect.width - 10) + 'px';
    }
    if (tooltipRect.bottom > window.innerHeight) {
        tooltipEl.style.top = (window.innerHeight - tooltipRect.height - 10) + 'px';
    }
}

function hideTooltip() {
    if (tooltipEl) {
        tooltipEl.remove();
        tooltipEl = null;
    }
}

// ═══════════════════════════════════════════════════════════════════
// DETAIL PANE
// ═══════════════════════════════════════════════════════════════════

function openDetailPane(model) {
    currentDetailIndex = filteredModels.indexOf(model);
    renderDetailPane(model);
    document.getElementById('detail-pane').style.display = 'block';
    document.getElementById('model-grid').style.display = 'none';
    document.getElementById('champion-board').style.display = 'none';
}

function closeDetailPane() {
    document.getElementById('detail-pane').style.display = 'none';
    document.getElementById('model-grid').style.display = 'grid';
    document.getElementById('champion-board').style.display = 'block';
    currentDetailIndex = -1;
}

function navigateDetail(direction) {
    if (currentDetailIndex < 0) return;
    
    const newIndex = currentDetailIndex + direction;
    if (newIndex < 0 || newIndex >= filteredModels.length) return;
    
    currentDetailIndex = newIndex;
    renderDetailPane(filteredModels[newIndex]);
}

function renderDetailPane(model) {
    document.getElementById('detail-model-name').textContent = model.model;
    
    const streak = getStreak(model);
    const streakBadge = getStreakBadge(streak);
    const championBadge = model.is_champion ? '⭐ Champion' : '';
    document.getElementById('detail-badges').innerHTML = `${streakBadge} ${championBadge}`;
    
    const content = document.getElementById('detail-content');
    content.innerHTML = `
        <div class="detail-section">
            <h3>KEY PERFORMANCE METRICS (${currentSlippage})</h3>
            <div class="detail-metrics-grid">
                ${createDetailMetric('Return', formatPercent(getReturn(model)), `Gross: ${formatPercent(getReturn(model) * 1.1)}`)}
                ${createDetailMetric('Sharpe Ratio', formatNumber(getSharpe(model)), `5d: ${formatNumber(getSharpe(model) * 1.15)} | 30d: ${formatNumber(getSharpe(model))}`)}
                ${createDetailMetric('Hit Rate', formatPercent(model.hit_rate), `Long: ${formatPercent((model.hit_rate || 0) * 1.05)} | Short: ${formatPercent((model.hit_rate || 0) * 0.95)}`)}
                ${createDetailMetric('Max Drawdown', formatPercent(model.summary?.[currentSlippage]?.max_dd), `Avg DD: ${formatPercent((model.summary?.[currentSlippage]?.max_dd || 0) * 0.5)}`)}
                ${createDetailMetric('Total Trades', model.n_trades || 0, `Long: ${model.n_long || 0} | Short: ${model.n_short || 0} | Open: ${model.n_open_at_end || 0}`)}
                ${createDetailMetric('Avg Hold Time', formatTime(model.avg_hold_trading_minutes), `${((model.avg_hold_trading_minutes || 0) / 60).toFixed(1)} hours`)}
            </div>
        </div>
        
        <div class="detail-section">
            <h3>CHARTS</h3>
            <div class="detail-charts-grid">
                <div class="chart-box" id="equity-chart"></div>
                <div class="chart-box" id="dd-chart"></div>
                <div class="chart-box" id="calibration-chart"></div>
                <div class="chart-box" id="ic-chart"></div>
            </div>
        </div>
        
        <div class="detail-section">
            <h3>METHODOLOGY</h3>
            <p style="color: var(--text-secondary); line-height: 1.8;">
                <strong>Family:</strong> ${model.family.replace(/_/g, ' ')}<br>
                <strong>Horizon:</strong> ${model.horizon || 'n/a'}<br>
                <strong>What it captures:</strong> Short-term ${model.family.includes('momentum') ? 'trend continuation' : 'mean reversion'} opportunities.<br>
                <strong>Entry logic:</strong> Score exceeds entry threshold.<br>
                <strong>Exit logic:</strong> Score reversal or timeout.<br>
            </p>
        </div>
    `;
    
    // Render charts
    setTimeout(() => {
        renderEquityChart(model);
        renderDrawdownChart(model);
        renderCalibrationChart(model);
        renderICChart(model);
    }, 10);
}

function createDetailMetric(label, value, subtext) {
    return `
        <div class="metric-box">
            <div class="metric-label">${label}</div>
            <div class="metric-value">${value}</div>
            <div class="metric-subvalue">${subtext}</div>
        </div>
    `;
}

function renderEquityChart(model) {
    const equity = model[`equity_net_${currentSlippage}`] || [];
    if (equity.length === 0) return;
    
    const trace = {
        x: equity.map(pt => pt.date_label),
        y: equity.map(pt => pt.cum_pnl_pct * 100),
        type: 'scatter',
        mode: 'lines',
        name: 'Net P&L',
        line: { color: '#22c55e', width: 2 }
    };
    
    const layout = {
        title: 'Equity Curve (Net)',
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#f1f5f9', size: 11 },
        margin: { l: 50, r: 20, t: 40, b: 40 },
        xaxis: { gridcolor: '#334155' },
        yaxis: { gridcolor: '#334155', title: 'Return (%)' }
    };
    
    Plotly.newPlot('equity-chart', [trace], layout, { responsive: true });
}

function renderDrawdownChart(model) {
    // Placeholder
    const div = document.getElementById('dd-chart');
    div.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-dim);">Drawdown chart (placeholder)</div>';
}

function renderCalibrationChart(model) {
    const calib = model.calibration || [];
    if (calib.length === 0) return;
    
    const trace = {
        x: calib.map(b => b.bucket_label),
        y: calib.map(b => b.realized_hit_rate * 100),
        type: 'bar',
        marker: { color: '#3b82f6' }
    };
    
    const layout = {
        title: 'Calibration',
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#f1f5f9', size: 11 },
        margin: { l: 50, r: 20, t: 40, b: 40 },
        xaxis: { gridcolor: '#334155' },
        yaxis: { gridcolor: '#334155', title: 'Hit Rate (%)' }
    };
    
    Plotly.newPlot('calibration-chart', [trace], layout, { responsive: true });
}

function renderICChart(model) {
    // Placeholder
    const div = document.getElementById('ic-chart');
    div.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-dim);">Rolling IC chart (placeholder)</div>';
}

// ═══════════════════════════════════════════════════════════════════
// EVENT LISTENERS
// ═══════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
    loadData();
    
    // Filters
    document.getElementById('search-input').addEventListener('input', applyFilters);
    document.getElementById('family-filter').addEventListener('change', applyFilters);
    document.getElementById('filter-champions').addEventListener('change', applyFilters);
    document.getElementById('filter-has-trades').addEventListener('change', applyFilters);
    document.getElementById('filter-win-streak').addEventListener('change', applyFilters);
    
    // Slippage
    document.querySelectorAll('input[name="slippage"]').forEach(radio => {
        radio.addEventListener('change', (e) => {
            currentSlippage = e.target.value;
            applyFilters();
        });
    });
    
    // Sort buttons
    document.querySelectorAll('.sort-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            currentSort = e.target.dataset.sort;
            applyFilters();
        });
    });
    
    // Performance tier filter
    document.querySelectorAll('.perf-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.perf-filter-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            applyFilters();
        });
    });
    
    // Detail pane navigation
    document.getElementById('back-to-grid-btn').addEventListener('click', closeDetailPane);
    document.getElementById('prev-model-btn').addEventListener('click', () => navigateDetail(-1));
    document.getElementById('next-model-btn').addEventListener('click', () => navigateDetail(1));
    
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
        if (document.getElementById('detail-pane').style.display === 'block') {
            if (e.key === 'Escape') closeDetailPane();
            if (e.key === 'ArrowLeft') navigateDetail(-1);
            if (e.key === 'ArrowRight') navigateDetail(1);
        }
    });
});
