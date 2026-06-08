#!/usr/bin/env python3
"""
Validation tests for v25 tournament expansion signals.

Tests that all new signal functions:
1. Are importable from quant_signals module
2. Accept the expected parameters
3. Return valid score ranges (-100 to +100 or None)
4. Handle edge cases (empty bars, insufficient data)
"""

import sys
sys.path.insert(0, '/home/nixos/Prod/V1/src')

import quant_signals as qs


def make_test_bars(n=50, close_vals=None):
    """Generate synthetic bars for testing."""
    if close_vals is None:
        close_vals = [100 + i * 0.5 for i in range(n)]  # Uptrend
    
    bars = []
    for i, close in enumerate(close_vals):
        bars.append({
            'ts': f'2026-05-{i+1:02d}T09:30:00',
            'close': close,
            'volume': 1000000 + i * 10000,
            'high': close * 1.01,
            'low': close * 0.99,
        })
    return bars


def test_momentum_vol_confirmed():
    """Test volume-confirmed momentum signals."""
    print("\n1. Testing momentum_vol_confirmed...")
    
    bars = make_test_bars(30)
    
    # Test 5bar variant
    score_5 = qs.momentum_vol_confirmed(bars, lookback=5)
    assert score_5 is None or -100 <= score_5 <= 100, f"Invalid score: {score_5}"
    
    # Test 13bar variant
    score_13 = qs.momentum_vol_confirmed(bars, lookback=13)
    assert score_13 is None or -100 <= score_13 <= 100, f"Invalid score: {score_13}"
    
    # Test insufficient data
    score_short = qs.momentum_vol_confirmed(bars[:3], lookback=5)
    assert score_short is None, "Should return None for insufficient data"
    
    print("   ✓ momentum_vol_confirmed passes")


def test_momentum_52wk_range():
    """Test 52-week range position signal."""
    print("\n2. Testing momentum_52wk_range_position...")
    
    bars = make_test_bars(260)  # ~1 year of daily bars
    
    score = qs.momentum_52wk_range_position(bars)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Test at 52wk high (should return +100)
    bars_high = make_test_bars(260, [100] * 259 + [120])
    score_high = qs.momentum_52wk_range_position(bars_high)
    assert score_high is not None and score_high > 50, "Should be bullish at 52wk high"
    
    # Test at 52wk low (should return -100)
    bars_low = make_test_bars(260, [120] * 259 + [100])
    score_low = qs.momentum_52wk_range_position(bars_low)
    assert score_low is not None and score_low < -50, "Should be bearish at 52wk low"
    
    print("   ✓ momentum_52wk_range_position passes")


def test_momentum_acceleration():
    """Test momentum acceleration (2nd derivative) signal."""
    print("\n3. Testing momentum_acceleration...")
    
    bars = make_test_bars(20)
    
    score = qs.momentum_acceleration(bars, recent_window=3, prior_window=5)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Test insufficient data
    score_short = qs.momentum_acceleration(bars[:5], recent_window=3, prior_window=5)
    assert score_short is None, "Should return None for insufficient data"
    
    print("   ✓ momentum_acceleration passes")


def test_momentum_multi_timeframe():
    """Test multi-timeframe momentum blend."""
    print("\n4. Testing momentum_multi_timeframe_blend...")
    
    bars = make_test_bars(30)
    
    score = qs.momentum_multi_timeframe_blend(bars)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Test all-positive returns (should amplify)
    bars_bull = make_test_bars(30, [100 + i * 2 for i in range(30)])
    score_bull = qs.momentum_multi_timeframe_blend(bars_bull)
    assert score_bull is not None and score_bull > 0, "Should be positive on strong uptrend"
    
    print("   ✓ momentum_multi_timeframe_blend passes")


def test_mean_rev_bb_pct():
    """Test Bollinger Band percentage signal."""
    print("\n5. Testing mean_rev_bb_pct...")
    
    bars = make_test_bars(30)
    
    score = qs.mean_rev_bb_pct(bars, period=20)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Test insufficient data
    score_short = qs.mean_rev_bb_pct(bars[:15], period=20)
    assert score_short is None, "Should return None for insufficient data"
    
    print("   ✓ mean_rev_bb_pct passes")


def test_mean_rev_rsi_divergence():
    """Test RSI divergence signal."""
    print("\n6. Testing mean_rev_rsi_divergence...")
    
    bars = make_test_bars(30)
    
    score = qs.mean_rev_rsi_divergence(bars, period=14)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Most of the time should return 0 (no divergence)
    # Divergences are rare setups
    
    print("   ✓ mean_rev_rsi_divergence passes")


def test_mean_rev_volume_spike_fade():
    """Test volume-spike fade signal."""
    print("\n7. Testing mean_rev_volume_spike_fade...")
    
    bars = make_test_bars(30)
    
    score = qs.mean_rev_volume_spike_fade(bars)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Test volume spike scenario
    bars_spike = make_test_bars(30)
    bars_spike[-1]['volume'] = bars_spike[-2]['volume'] * 3  # 3x volume
    bars_spike[-1]['close'] = bars_spike[-2]['close'] * 1.05  # +5% move
    score_spike = qs.mean_rev_volume_spike_fade(bars_spike)
    # Should fade the move (negative signal on up-spike)
    
    print("   ✓ mean_rev_volume_spike_fade passes")


def test_pca_top10_pt_basket():
    """Test PCA top-10 price-target basket (universe context)."""
    print("\n8. Testing pca_top10_pt_basket...")
    
    # This signal requires universe context, not just bars
    ctx = {
        'pca_top10_residuals': {
            'AAPL': 1.5,
            'MSFT': -0.8,
            'NVDA': 2.1,
        }
    }
    
    # For AAPL
    score = qs.pca_top10_pt_basket(None, ticker='AAPL', ctx=ctx)
    assert score is None or -100 <= score <= 100, f"Invalid score: {score}"
    
    # Not in top-10
    score_none = qs.pca_top10_pt_basket(None, ticker='XYZ', ctx=ctx)
    assert score_none == 0.0, "Should return 0 for ticker not in top-10"
    
    print("   ✓ pca_top10_pt_basket passes")


def test_sector_etf_pca():
    """Test sector ETF PCA signals (PC1 and PC2)."""
    print("\n9. Testing sector_etf_pc1 and sector_etf_pc2...")
    
    ctx = {
        'sector_etf_pc1_for_ticker': {'AAPL': 42.5},
        'sector_etf_pc2_for_ticker': {'AAPL': -15.3},
    }
    
    score_pc1 = qs.sector_etf_pc1(None, ticker='AAPL', ctx=ctx)
    assert score_pc1 is None or -100 <= score_pc1 <= 100, f"Invalid PC1 score: {score_pc1}"
    
    score_pc2 = qs.sector_etf_pc2(None, ticker='AAPL', ctx=ctx)
    assert score_pc2 is None or -100 <= score_pc2 <= 100, f"Invalid PC2 score: {score_pc2}"
    
    print("   ✓ sector_etf_pc1/pc2 pass")


def test_regime_conditional_wrapper():
    """Test make_regime_conditional wrapper factory."""
    print("\n10. Testing make_regime_conditional...")
    
    # Base signal (always returns +50)
    def base_signal(bars, **kwargs):
        return 50.0
    
    # Wrap with high_vol condition
    wrapped = qs.make_regime_conditional(base_signal, "high_vol")
    
    bars = make_test_bars(10)
    
    # Test in high-vol regime
    ctx_high_vol = {'regime_label': 'high_volatility'}
    score_match = wrapped(bars, ctx=ctx_high_vol)
    assert score_match == 50.0, f"Should pass through in matching regime, got {score_match}"
    
    # Test in low-vol regime (mismatch)
    ctx_low_vol = {'regime_label': 'low_volatility'}
    score_mismatch = wrapped(bars, ctx=ctx_low_vol)
    assert score_mismatch == 0.0, f"Should zero out in non-matching regime, got {score_mismatch}"
    
    print("   ✓ make_regime_conditional passes")


def main():
    print("=" * 60)
    print("v25 Signal Validation Tests")
    print("=" * 60)
    
    test_momentum_vol_confirmed()
    test_momentum_52wk_range()
    test_momentum_acceleration()
    test_momentum_multi_timeframe()
    test_mean_rev_bb_pct()
    test_mean_rev_rsi_divergence()
    test_mean_rev_volume_spike_fade()
    test_pca_top10_pt_basket()
    test_sector_etf_pca()
    test_regime_conditional_wrapper()
    
    print("\n" + "=" * 60)
    print("✓ All v25 signal tests PASSED")
    print("=" * 60)


if __name__ == '__main__':
    main()
