"""
screener_capture_patch.py  —  Hooks for capturing screener signals to Postgres
==============================================================================
Drop-in patch for run_screener.py that intercepts the screener pipeline at
three points and writes everything we want to remember to the rcg_signals
Postgres database via signals_db.

DESIGN
------
- Best-effort: Postgres unreachable → log + continue. Screener never fails.
- Captures the post-Finnhub-fetch top 80 (not just the final 40), so we
  have data on names that almost-but-not-quite ranked.
- One row per (run, ticker, signal_name). ~20 signals per ticker × 80 = ~1600
  per run. Bulk insert keeps wall-clock under 3 seconds.
- Run-level signals (regime classifications, market bias, dynamic weights)
  use ticker = '_MARKET' as a sentinel for whole-market signals.

INTEGRATION
-----------
In run_screener.py, add this gate near the top of the CONFIG section:

    CAPTURE_SIGNALS = True   # Phase 2A: write all signals to rcg_signals DB

Then, after all the existing monkeypatches (envelope, sentiment, etc.)
and before `screener.main(...)`, add:

    if CAPTURE_SIGNALS:
        import screener_capture_patch as cap
        cap.install(
            screener_module = screener,
            config = {
                'sentiment_override': SENTIMENT_OVERRIDE,
                'override_factor':    OVERRIDE_FACTOR,
                'cap_preset':         CAP_PRESET,
                'use_new_pt':         USE_NEW_PRICE_TARGETS,
                'pt_envelope':        PT_APPLY_ENVELOPE,
                'pt_r2_floor':        PT_R2_FLOOR,
                'pt_r2_full':         PT_R2_FULL,
                'excl_adrs':          EXCL_ADRS,
                'excl_biotech':       EXCL_BIOTECH,
                'sector_cap':         SECTOR_CAP,
                'max_per_sector':     MAX_PER_SECTOR,
                'fed_target_rate':    FED_TARGET_RATE,
            },
        )

That's the entire integration. install() wraps the screener module's
relevant functions in-place; on completion (or any error) it logs to the
signals DB.

Author: RCG / Nick Diaz
Version: 1.0  (2026-04-29)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("rcg.capture")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


# ============================================================
# Known on-disk output paths (the screener always writes these)
# ============================================================
OUTPUT_PATHS = {
    "long_screener_csv":   "/home/nixos/Prod/V1/outputs/long_screener_results.csv",
    "screener_universe":   "/home/nixos/Prod/V1/outputs/screener_universe.csv",
    "factor_signals_json": "/home/nixos/Prod/V1/outputs/factor_signals.json",
    "html_report":         "/home/nixos/Prod/V1/outputs/dynamic_factor_screener.html",
}


# ============================================================
# Per-run state (process-local — one screener run per process)
# ============================================================
_run_state = {
    "run_id":    None,
    "started":   None,
    "config":    None,
    "n_in":      None,
    "n_out":     None,
}


def _try_import_signals_db():
    """Imports signals_db lazily so a missing DB doesn't crash the import."""
    try:
        # Ensure src dir is on path
        for p in ('/home/nixos/Prod/V1/src',):
            if p not in sys.path:
                sys.path.insert(0, p)
        import signals_db
        return signals_db
    except Exception as e:
        logger.warning(f"signals_db import failed: {e}")
        return None


# ============================================================
# Signal extraction from screener row
# ============================================================
# Every per-ticker DataFrame column that we want to persist as a signal.
# Format: (column_name, signal_name, value_or_string)
#   - value_or_string = 'value' for numeric columns
#   - value_or_string = 'string' for categorical/text columns
#   - value_or_string = 'json'   for parsed JSON dicts
PER_TICKER_NUMERIC = [
    "composite_score",
    "fund_score", "tech_score", "valn_score", "sent_score",
    "revenue_trend", "ebitda_trend", "fcf_trend", "debt_trend",
    "price_momentum", "rsi_score", "sma_cross_score",
    "upside_score", "sentiment_score",
    "target_price", "upside_pct",
    "analyst_target_mean", "analyst_count",
    "last_price", "marketcap",
    "rsi_raw_14d", "sma20", "sma50",
]

PER_TICKER_STRING = [
    "pt_source", "sector", "industry",
]

# Which columns to also capture as full JSON (their content is structured)
PER_TICKER_JSON = [
    "pt_detail_json",   # gates_fired, model breakdown, sector anchors, etc.
]


def _row_to_signal_rows(row: dict, run_id: int) -> list:
    """Convert one screener-output row to a list of dicts ready for record_signals_bulk()."""
    ticker = row.get("ticker")
    sector = row.get("sector")
    if not ticker:
        return []

    out = []

    # Numeric signals
    for col in PER_TICKER_NUMERIC:
        v = row.get(col)
        if v is None or (isinstance(v, float) and (v != v)):  # NaN check
            continue
        try:
            out.append({
                "ticker":      ticker,
                "signal_name": col,
                "value":       float(v),
                "sector":      sector,
            })
        except (TypeError, ValueError):
            pass

    # String/categorical signals
    for col in PER_TICKER_STRING:
        v = row.get(col)
        if v is None or v == "":
            continue
        out.append({
            "ticker":      ticker,
            "signal_name": col,
            "string":      str(v),
            "sector":      sector,
        })

    # JSON-payload signals
    for col in PER_TICKER_JSON:
        raw = row.get(col)
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        # Capture summary fields explicitly, full payload as JSON
        out.append({
            "ticker":      ticker,
            "signal_name": "pt_breakdown",
            "string":      parsed.get("dominant_model") or "unknown",
            "payload":     parsed,
            "sector":      sector,
        })
        # Also capture gates_fired as a separate signal for easy querying
        gates = parsed.get("gates_fired") or []
        if gates:
            out.append({
                "ticker":      ticker,
                "signal_name": "gates_fired",
                "string":      ";".join(str(g) for g in gates),
                "sector":      sector,
            })
        # Quality score and haircut — separate signals for trend analysis
        qs = parsed.get("quality_score")
        if qs is not None:
            try:
                out.append({
                    "ticker":      ticker,
                    "signal_name": "quality_score",
                    "value":       float(qs),
                    "sector":      sector,
                })
            except (TypeError, ValueError):
                pass

    return out


# ============================================================
# INSTALL — wires the patches
# ============================================================
def install(screener_module, config: Optional[dict] = None) -> None:
    """
    Wraps screener_module.main() and screener_module.apply_blended_targets()
    to capture signals to the rcg_signals DB. Idempotent — safe to call once
    per process.
    """
    sdb = _try_import_signals_db()
    if sdb is None:
        logger.warning("signals_db not available — capture disabled for this run")
        return

    # ── Wrap apply_blended_targets to capture top-80 per-ticker signals ──
    if not hasattr(screener_module, "_capture_patched_apply_blended"):
        _orig_apply = screener_module.apply_blended_targets
        def _capture_apply(screened, analyst_price_targets):
            result = _orig_apply(screened, analyst_price_targets)
            # Capture happens AFTER apply runs — result is the screener's
            # post-blend DataFrame with target_price, upside_pct, pt_source,
            # pt_detail_json all in place.
            try:
                rid = _run_state["run_id"]
                if rid is None:
                    logger.warning("apply_blended_targets ran without run_id set — skipping capture")
                    return result
                rows = []
                for i in range(result.height):
                    row = result.row(i, named=True)
                    rows.extend(_row_to_signal_rows(row, rid))
                if rows:
                    n = sdb.record_signals_bulk(rid, rows)
                    logger.info(f"Captured {n} signals for {result.height} tickers")
                _run_state["n_in"] = result.height
            except Exception as e:
                logger.error(f"Capture failed (non-blocking): {e}")
            return result
        screener_module.apply_blended_targets = _capture_apply
        screener_module._capture_patched_apply_blended = True

    # ── Wrap main() to start/finalize the run ──
    if not hasattr(screener_module, "_capture_patched_main"):
        _orig_main = screener_module.main
        def _capture_main(*args, **kwargs):
            _run_state["started"] = time.time()
            _run_state["config"]  = config or {}

            try:
                rid = sdb.record_run(
                    run_type="screener_daily",
                    config=config,
                    notes=f"automatic capture v1, run on {datetime.now(timezone.utc).isoformat()}",
                )
                _run_state["run_id"] = rid
                logger.info(f"Capture started for run_id={rid}")
            except Exception as e:
                logger.error(f"Failed to start capture run: {e}")
                _run_state["run_id"] = None

            try:
                result = _orig_main(*args, **kwargs)
            except Exception:
                # Mark the run as failed but don't swallow the exception
                try:
                    if _run_state["run_id"] is not None:
                        sdb.finalize_run(
                            _run_state["run_id"],
                            n_in=_run_state["n_in"],
                            n_out=None,
                            runtime_seconds=time.time() - _run_state["started"],
                            output_path="ERROR",
                        )
                except Exception:
                    pass
                raise

            # Capture run-level (market) signals — read from factor_signals.json
            # which the screener writes to disk on every run.
            try:
                if _run_state["run_id"] is not None:
                    _capture_run_level_signals_from_disk(
                        sdb, _run_state["run_id"],
                        factor_signals_path=OUTPUT_PATHS["factor_signals_json"],
                    )
            except Exception as e:
                logger.error(f"Run-level capture failed: {e}")

            # Finalize — read n_out from the screener's output CSV row count.
            try:
                n_out = _count_csv_rows(OUTPUT_PATHS["long_screener_csv"])
                output_path = OUTPUT_PATHS["long_screener_csv"]
                # Fallback: respect a structured return if main() ever provides one
                if isinstance(result, dict):
                    n_out = result.get("n_results", n_out)
                    output_path = result.get("output_csv_path") or output_path
                sdb.finalize_run(
                    _run_state["run_id"],
                    n_in=_run_state["n_in"],
                    n_out=n_out,
                    runtime_seconds=time.time() - _run_state["started"],
                    output_path=output_path,
                )
                logger.info(f"Capture finalized for run_id={_run_state['run_id']} "
                            f"(n_in={_run_state['n_in']}, n_out={n_out})")
            except Exception as e:
                logger.error(f"Finalize capture failed: {e}")
            return result
        screener_module.main = _capture_main
        screener_module._capture_patched_main = True

    logger.info("Signal capture patches installed")


def _count_csv_rows(path: str) -> Optional[int]:
    """Returns the row count of a CSV (excluding header), or None if file missing."""
    try:
        from pathlib import Path
        p = Path(path)
        if not p.exists():
            return None
        with p.open("r", encoding="utf-8", errors="replace") as f:
            n = sum(1 for _ in f)
        return max(0, n - 1)   # subtract header
    except Exception as e:
        logger.warning(f"Could not count rows in {path}: {e}")
        return None


def _capture_run_level_signals_from_disk(sdb, run_id: int,
                                           factor_signals_path: str) -> None:
    """
    Reads factor_signals.json from disk and captures regime classifications,
    market bias, and dynamic weights as _MARKET signals. The JSON file
    structure is what the screener writes to factor_signals.json each run.

    Expected structure (based on screener output):
        {
          "regimes": {
              "momentum": {"z": ..., "label": "...", "raw_value": ...},
              "volatility": {...}, ...
          },
          "market_bias": {"score": ..., "label": "...", "confidence": ...},
          "dynamic_weights": {"revenue_trend": 0.063, ...},
          ...
        }
    Older versions may use different top-level keys; we probe defensively.
    """
    from pathlib import Path
    p = Path(factor_signals_path)
    if not p.exists():
        logger.warning(f"factor_signals.json not found at {factor_signals_path} "
                       f"— skipping market signal capture")
        return

    try:
        data = json.loads(p.read_text())
    except Exception as e:
        logger.error(f"Failed to parse {factor_signals_path}: {e}")
        return

    rows = []

    # Regime classifications — try several known shapes
    factors = data.get("factors") or data.get("regimes") or {}
    if isinstance(factors, dict):
        for regime_name, regime_data in factors.items():
            if not isinstance(regime_data, dict):
                continue
            z = regime_data.get("z") or regime_data.get("z_score")
            label = regime_data.get("label") or regime_data.get("regime")
            raw = regime_data.get("raw_value") or regime_data.get("value")
            if z is not None:
                try:
                    rows.append({
                        "ticker":      "_MARKET",
                        "signal_name": f"regime_{regime_name}_z",
                        "value":       float(z),
                        "payload":     regime_data,
                    })
                except (TypeError, ValueError):
                    pass
            if label:
                rows.append({
                    "ticker":      "_MARKET",
                    "signal_name": f"regime_{regime_name}_label",
                    "string":      str(label),
                })
            if raw is not None:
                try:
                    rows.append({
                        "ticker":      "_MARKET",
                        "signal_name": f"regime_{regime_name}_raw",
                        "value":       float(raw),
                    })
                except (TypeError, ValueError):
                    pass

    # Market bias
    bias = data.get("market_bias") or data.get("bias") or {}
    if isinstance(bias, dict):
        if bias.get("score") is not None:
            try:
                rows.append({
                    "ticker":      "_MARKET",
                    "signal_name": "market_bias_score",
                    "value":       float(bias["score"]),
                    "payload":     bias,
                })
            except (TypeError, ValueError):
                pass
        if bias.get("label"):
            rows.append({
                "ticker":      "_MARKET",
                "signal_name": "market_bias_label",
                "string":      str(bias["label"]),
            })
        if bias.get("confidence") is not None:
            try:
                rows.append({
                    "ticker":      "_MARKET",
                    "signal_name": "market_bias_confidence",
                    "value":       float(bias["confidence"]),
                })
            except (TypeError, ValueError):
                pass

    # Dynamic weights
    weights = data.get("dynamic_weights") or data.get("weights") or {}
    if isinstance(weights, dict):
        for factor_name, weight_value in weights.items():
            try:
                rows.append({
                    "ticker":      "_MARKET",
                    "signal_name": f"weight_{factor_name}",
                    "value":       float(weight_value),
                })
            except (TypeError, ValueError):
                pass

    # SPY directional bias if present
    spy = data.get("spy_bias") or {}
    if isinstance(spy, dict):
        if spy.get("score") is not None:
            try:
                rows.append({
                    "ticker":      "_MARKET",
                    "signal_name": "spy_bias_score",
                    "value":       float(spy["score"]),
                    "payload":     spy,
                })
            except (TypeError, ValueError):
                pass
        if spy.get("label"):
            rows.append({
                "ticker":      "_MARKET",
                "signal_name": "spy_bias_label",
                "string":      str(spy["label"]),
            })

    if rows:
        sdb.record_signals_bulk(run_id, rows)
        logger.info(f"Captured {len(rows)} run-level signals from factor_signals.json")
    else:
        logger.warning(f"factor_signals.json parsed but produced 0 signals "
                       f"(top-level keys: {list(data.keys())})")
