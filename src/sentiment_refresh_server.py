"""
sentiment_refresh_server.py
Lightweight API server that triggers Bloomberg price refresh + sentiment rerun.
Runs on port 8085 on NixOS, called by button on sentiment HTML dashboard.

Endpoints:
  GET    /refresh                    → trigger full pipeline (BBG pull + sentiment)
  GET    /status                     → last-refresh time + current state
  GET    /predictions/<TICKER>       → captured prediction history for one ticker
  GET    /pinned                     → list of user-pinned tickers
  POST   /pinned/<TICKER>            → pin (force into BBG pull immediately)
  DELETE /pinned/<TICKER>            → unpin
  GET    /assumptions/<TICKER>       → trailing 6q baseline + stored overrides
                                       + recomputed PT with overrides applied
  POST   /assumptions/<TICKER>       → save overrides + recompute PT
                                       body: {rev_growth_ann_pct, fcf_growth_ann_pct,
                                              ebitda_margin_now_pct, debt_paydown_ann_pct}
                                       (null values = follow baseline)
  DELETE /assumptions/<TICKER>       → clear overrides for this ticker
  GET    /report/<TICKER>            → deterministic valuation rubric + cached LLM summary
                                       (regenerates LLM narration if assumptions changed)
"""

import subprocess
import json
import os
import re
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PORT = 8085
SENTIMENT_SCRIPT = "/home/nixos/Prod/V1/src/market_sentiment_bbg.py"
VENV_PYTHON = "/home/nixos/venv-sentiment/bin/python"
FINNHUB_KEY = "d6ivnd1r01qleu95pan0d6ivnd1r01qleu95pang"
STATUS_FILE = Path("/home/nixos/Prod/V1/src/refresh_status.json")

# ─── User-pinned ticker store ──────────────────────────────────────────────
# Tickers in this file persist across screener regenerations and are always
# force-included in the BBG pull. Same mechanism powers both the dashboard's
# ★ favorites button and the ad-hoc ticker entry — any ticker in here gets
# the full analytics pipeline treatment.
PINNED_PATH = Path("/home/nixos/Prod/V1/src/user_pinned.json")
ASSUMPTIONS_PATH = Path("/home/nixos/Prod/V1/src/user_assumptions.json")
WATCHLIST_PATH = Path("/home/nixos/Prod/V1/outputs/watchlist.json")
WATCHLIST_SCP_DEST = "ndiaz@rcg-base:C:/Users/ndiaz/Dropbox/RCG_2020/watchlist.json"
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")  # stocks/ETFs, allow .B / -A

state = {
    "status": "idle",
    "last_refresh": None,
    "last_error": None,
    "running": False,
}


# ─── Pin store helpers ─────────────────────────────────────────────────────
_pin_lock = threading.Lock()


def load_pinned() -> list[str]:
    if not PINNED_PATH.exists():
        return []
    try:
        d = json.loads(PINNED_PATH.read_text())
        return [t.upper() for t in (d.get("pinned") or []) if isinstance(t, str)]
    except Exception:
        return []


def save_pinned(pinned: list[str]) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pinned":     sorted(set(pinned)),
    }
    PINNED_PATH.write_text(json.dumps(payload, indent=2))


def update_watchlist_and_push(force_include: list[str]) -> tuple[bool, str]:
    """
    Append `force_include` tickers to outputs/watchlist.json (deduped, never
    cropped by the 120 cap), then SCP the file to Windows so the next BBG pull
    sees them. Returns (ok, message).
    """
    if not WATCHLIST_PATH.exists():
        return False, f"watchlist file missing: {WATCHLIST_PATH}"
    try:
        wl = json.loads(WATCHLIST_PATH.read_text())
        tickers = list(wl.get("tickers") or [])
        added = []
        for t in force_include:
            if t not in tickers:
                tickers.append(t)
                added.append(t)
        wl["tickers"] = tickers
        notes = wl.get("notes") or {}
        for t in force_include:
            if t not in notes:
                notes[t] = "user-pinned"
        wl["notes"] = notes
        wl["generated_at"] = datetime.now(timezone.utc).isoformat()
        WATCHLIST_PATH.write_text(json.dumps(wl, indent=2, default=str))
    except Exception as e:
        return False, f"watchlist update failed: {e}"

    # SCP to Windows so the next BBG pull picks it up
    try:
        result = subprocess.run(
            ["scp", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
             "-o", "StrictHostKeyChecking=accept-new",
             str(WATCHLIST_PATH), WATCHLIST_SCP_DEST],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return False, f"scp failed: {result.stderr.strip()[:200]}"
    except Exception as e:
        return False, f"scp exception: {e}"

    return True, f"watchlist updated · added={added or '[already present]'}"


# ─── Per-ticker assumptions store ──────────────────────────────────────────
# Storage shape (src/user_assumptions.json):
# {
#   "AAPL": {
#     "overrides": {                          (null values = follow engine default)
#       "rev_growth_ann_pct":     8.5,
#       "fcf_growth_ann_pct":     null,
#       "ebitda_margin_now_pct":  null,
#       "debt_paydown_ann_pct":   12.0
#     },
#     "updated_at":  "2026-05-11T...",
#     "llm_summary": null,                    (filled by report generator)
#     "llm_rating":  null
#   }
# }
_assumptions_lock = threading.Lock()

# Override keys must match the keyword names in price_targets.compute_growth_baseline()
# and price_targets._apply_growth_override() so the engine accepts them unchanged.
_OVERRIDE_KEYS = (
    "rev_growth_ann_pct",
    "fcf_growth_ann_pct",
    "ebitda_margin_now_pct",
    "debt_paydown_ann_pct",
)


def load_assumptions() -> dict:
    if not ASSUMPTIONS_PATH.exists():
        return {}
    try:
        return json.loads(ASSUMPTIONS_PATH.read_text())
    except Exception:
        return {}


def save_assumptions(data: dict) -> None:
    ASSUMPTIONS_PATH.write_text(json.dumps(data, indent=2, default=str))


def compute_pt_payload(ticker: str, overrides: dict | None) -> dict:
    """
    Fetch fundamentals + run the PT engine with `overrides`, return a JSON
    payload with the engine baseline, the user overrides, and the resulting PT
    + per-model breakdown. Used by GET /assumptions/<T> and POST.
    """
    # Imported lazily so the server starts even if polars/parquet not present
    import sys
    sys.path.insert(0, "/home/nixos/Prod/V1/src")
    from fundamentals_lookup import fetch_fundamentals
    from price_targets import compute_target_price, compute_growth_baseline

    f = fetch_fundamentals(ticker)
    if not f:
        return {"ticker": ticker, "error": "ticker not in SF1 or insufficient history"}

    base = compute_growth_baseline(
        ebitda_series=f["ebitda_series"],
        revenue_series=f["revenue_series"],
        fcf_series=f["fcf_series"],
        debt_series=f["debt_series"],
    )

    # Resolve live price via 3-tier fallback:
    #   1. Bloomberg watchlist snapshot (most current — intraday)
    #   2. Screener CSV last_price (EOD close from Sharadar SEP, ~1 day stale)
    #   3. None — engine still runs but PT/upside will be marked unavailable
    live_price = None
    price_source = None
    try:
        bbg = json.loads(Path("/home/nixos/Prod/V1/src/bloomberg_prices.json").read_text())
        w = (bbg.get("watchlist") or {}).get(ticker.upper(), {})
        p = w.get("price")
        if p and p > 0:
            live_price = float(p)
            price_source = "bbg_live"
    except Exception:
        pass
    if not live_price:
        try:
            import csv as _csv
            with open("/home/nixos/Prod/V1/outputs/screener_universe.csv") as fh:
                for row in _csv.DictReader(fh):
                    if (row.get("ticker") or "").upper() == ticker.upper():
                        lp = row.get("last_price")
                        if lp:
                            live_price = float(lp)
                            price_source = "screener_eod"
                        break
        except Exception:
            pass
    if not live_price:
        # Absolute last resort — engine needs SOMETHING. Mark explicitly so the
        # dashboard / report can flag the PT as unreliable.
        live_price = 100.0
        price_source = "placeholder"

    # Run engine — with AND without overrides, so the response shows both
    r_default = compute_target_price(
        ebitda_series=f["ebitda_series"], revenue_series=f["revenue_series"],
        fcf_series=f["fcf_series"], debt_series=f["debt_series"],
        marketcap=f["marketcap"], last_price=live_price,
        cash_on_hand=f["cash_on_hand"], sector=f.get("sector"),
    )

    r_user = None
    if overrides and any(v is not None for v in overrides.values()):
        r_user = compute_target_price(
            ebitda_series=f["ebitda_series"], revenue_series=f["revenue_series"],
            fcf_series=f["fcf_series"], debt_series=f["debt_series"],
            marketcap=f["marketcap"], last_price=live_price,
            cash_on_hand=f["cash_on_hand"], sector=f.get("sector"),
            growth_overrides=overrides,
        )

    return {
        "ticker":          ticker.upper(),
        "latest_datekey":  f["latest_datekey"],
        "n_quarters":      f["n_quarters"],
        "live_price":      live_price,
        "price_source":    price_source,   # 'bbg_live' | 'screener_eod' | 'placeholder'
        "baseline":        base,
        "overrides":       overrides or {k: None for k in _OVERRIDE_KEYS},
        "pt_engine_default": {
            "target_price":  r_default.target_price,
            "upside_pct":    round(r_default.upside_pct * 100, 2) if r_default.upside_pct is not None else None,
            "pt_source":     r_default.pt_source,
            "quality_score": r_default.quality_score,
            "breakdown":     r_default.breakdown,
            "gates_fired":   r_default.gates_fired,
        },
        "pt_with_overrides": None if r_user is None else {
            "target_price":  r_user.target_price,
            "upside_pct":    round(r_user.upside_pct * 100, 2) if r_user.upside_pct is not None else None,
            "pt_source":     r_user.pt_source,
            "quality_score": r_user.quality_score,
            "breakdown":     r_user.breakdown,
            "gates_fired":   r_user.gates_fired,
        },
    }


def sanitize_overrides(raw: dict) -> dict:
    """Coerce values to float | None and drop unknown keys."""
    out = {}
    for k in _OVERRIDE_KEYS:
        v = raw.get(k)
        if v is None or v == "" or v == "null":
            out[k] = None
        else:
            try:
                f = float(v)
                # Clip to sane ranges so a typo doesn't blow up the engine
                if k.endswith("_ann_pct"):
                    f = max(-50.0, min(200.0, f))
                elif k == "ebitda_margin_now_pct":
                    f = max(-50.0, min(80.0, f))
                out[k] = round(f, 3)
            except (TypeError, ValueError):
                out[k] = None
    return out


# ─── Refresh runner ────────────────────────────────────────────────────────
def run_refresh():
    global state
    if state["running"]:
        return

    state["running"] = True
    state["status"] = "refreshing"

    try:
        state["status"] = "pulling bloomberg prices..."
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Triggering Bloomberg refresh...")

        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                 "ndiaz@100.86.90.78",
                 "python C:\\Users\\ndiaz\\Downloads\\bloomberg_prices.py"],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                print(f"  Bloomberg pull OK")
            else:
                print(f"  Bloomberg pull failed (may not be reachable): {result.stderr[:100]}")
        except Exception as e:
            print(f"  Bloomberg SSH failed: {e} — using existing prices")

        state["status"] = "running sentiment analysis..."
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Running sentiment analysis...")

        env = os.environ.copy()
        env["FINNHUB_API_KEY"] = FINNHUB_KEY

        result = subprocess.run(
            [VENV_PYTHON, SENTIMENT_SCRIPT],
            capture_output=True, text=True, timeout=120,
            cwd="/home/nixos/Prod/V1/src",
            env=env,
        )

        if result.returncode == 0:
            state["status"] = "complete"
            state["last_refresh"] = datetime.now(timezone.utc).isoformat()
            state["last_error"] = None
            print(f"  Sentiment analysis complete")
            print(result.stdout[-200:] if result.stdout else "")
        else:
            state["status"] = "error"
            state["last_error"] = result.stderr[:500]
            print(f"  Sentiment error: {result.stderr[:200]}")

    except Exception as e:
        state["status"] = "error"
        state["last_error"] = str(e)
        print(f"  Exception: {e}")

    finally:
        state["running"] = False

    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


# ─── Per-ticker valuation report ───────────────────────────────────────────
# Deterministic rubric → BUY/HOLD/SELL rating + structured bullet points.
# Optional LLM narration (Claude Haiku 4.5) writes a 2-sentence summary on top.
#
# The LLM call is ONLY made if an Anthropic API key is present
# (~/.anthropic_api_key or $ANTHROPIC_API_KEY). Otherwise the report ships
# with the deterministic part only and a placeholder summary.

ANTHROPIC_KEY_FILE = Path.home() / ".anthropic_api_key"


def _get_anthropic_key() -> Optional[str]:
    """Read API key from env var or ~/.anthropic_api_key. None if neither set."""
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k: return k.strip()
    if ANTHROPIC_KEY_FILE.exists():
        try:
            v = ANTHROPIC_KEY_FILE.read_text().strip()
            return v or None
        except Exception:
            return None
    return None


def compute_valuation_rubric(pt_payload: dict) -> dict:
    """
    Deterministic scoring from the PT engine output. Maps upside, quality,
    gates, and PT source flag to one of:
      STRONG BUY · BUY · HOLD · REDUCE · SELL
    plus a confidence score (0.0–1.0) and a list of bullet-point drivers.

    pt_payload is the dict returned by compute_pt_payload() — uses
    pt_with_overrides if present, otherwise pt_engine_default.
    """
    pt_block = pt_payload.get("pt_with_overrides") or pt_payload.get("pt_engine_default") or {}
    upside_pct  = pt_block.get("upside_pct")
    pt_source   = pt_block.get("pt_source") or "N/A"
    gates       = pt_block.get("gates_fired") or []
    quality     = pt_block.get("quality_score") or (pt_block.get("breakdown") or {}).get("quality_score")

    score = 0
    drivers = []

    # Upside score (-3 to +3)
    if upside_pct is None:
        drivers.append("No upside computed — engine could not produce a PT")
    elif upside_pct >= 25:
        score += 3; drivers.append(f"Strong upside: +{upside_pct:.1f}% to target")
    elif upside_pct >= 10:
        score += 2; drivers.append(f"Material upside: +{upside_pct:.1f}% to target")
    elif upside_pct >= 0:
        score += 1; drivers.append(f"Modest upside: +{upside_pct:.1f}% to target")
    elif upside_pct >= -10:
        score += 0; drivers.append(f"Roughly fair value: {upside_pct:+.1f}% to target")
    elif upside_pct >= -25:
        score -= 2; drivers.append(f"Trading rich: {upside_pct:+.1f}% to target")
    else:
        score -= 3; drivers.append(f"Trading very rich: {upside_pct:+.1f}% to target")

    # Quality score (-1 / 0 / +1)
    if quality is not None:
        if quality >= 0.85:
            score += 1; drivers.append(f"High fundamental quality ({quality:.2f})")
        elif quality < 0.55:
            score -= 1; drivers.append(f"Weak fundamental quality ({quality:.2f})")

    # PT source signal — M⚠clip means engine wanted higher but clipped to consensus
    if pt_source == "M⚠clip":
        score -= 1
        drivers.append("Engine PT clipped to analyst consensus (divergent view)")
    elif pt_source == "A":
        drivers.append("PT fallback to analyst consensus (low fundamental conviction)")

    # Gate penalties
    cap_gates = [g for g in gates if "CAP" in g.upper()]
    drop_gates = [g for g in gates if "DROP" in g.upper()]
    if drop_gates:
        score -= 1
        drivers.append(f"{len(drop_gates)} model(s) dropped by R² floor")
    if cap_gates:
        drivers.append(f"Sector-multiple cap applied to {len(cap_gates)} model(s)")

    # Map score → rating + confidence
    if   score >= 4: rating, confidence = "STRONG BUY", 0.85
    elif score >= 2: rating, confidence = "BUY",        0.70
    elif score >= -1: rating, confidence = "HOLD",      0.55
    elif score >= -3: rating, confidence = "REDUCE",    0.65
    else:            rating, confidence = "SELL",       0.80

    return {
        "rating":     rating,
        "score":      score,
        "confidence": confidence,
        "drivers":    drivers,
        "upside_pct": upside_pct,
        "pt_source":  pt_source,
        "quality":    quality,
    }


def call_anthropic_haiku(prompt: str) -> Optional[str]:
    """
    Call Claude Haiku 4.5 via the Anthropic Messages API for a short narrative.
    Returns the response text or None if no API key configured or call fails.
    Uses urllib so we don't need to add the `anthropic` SDK dependency.
    """
    key = _get_anthropic_key()
    if not key:
        return None
    import urllib.request, urllib.error
    body = json.dumps({
        "model":      "claude-haiku-4-5-20251001",
        "max_tokens": 200,
        "temperature": 0.1,
        "messages":   [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data    = body,
        method  = "POST",
        headers = {
            "x-api-key":         key,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode())
            blocks = d.get("content") or []
            for b in blocks:
                if b.get("type") == "text":
                    return b.get("text", "").strip()
            return None
    except urllib.error.HTTPError as e:
        print(f"[llm] HTTP {e.code}: {e.read()[:300]!r}")
        return None
    except Exception as e:
        print(f"[llm] call failed: {e}")
        return None


def narrate_rubric(ticker: str, rubric: dict, pt_payload: dict) -> Optional[str]:
    """
    Ask Haiku to write exactly 2 sentences explaining the rating, grounded in
    the deterministic drivers. The LLM is forbidden from inventing facts —
    rating + key inputs come from the rubric, not from its own analysis.
    """
    pt_block  = pt_payload.get("pt_with_overrides") or pt_payload.get("pt_engine_default") or {}
    overrides = pt_payload.get("overrides") or {}
    has_overrides = any(v is not None for v in overrides.values())
    pt = pt_block.get("target_price")
    breakdown = pt_block.get("breakdown") or {}
    dominant = breakdown.get("dominant_model", "unknown")

    drivers_str = "\n".join(f"  - {d}" for d in rubric["drivers"])
    overrides_str = "user assumption overrides applied" if has_overrides else "engine defaults only"

    prompt = (
        f"You are writing the recommendation summary for a quantitative valuation report.\n"
        f"\n"
        f"Ticker: {ticker}\n"
        f"Rating: {rubric['rating']} (confidence {rubric['confidence']:.0%})\n"
        f"Target price: ${pt}\n"
        f"Upside: {rubric.get('upside_pct')}%\n"
        f"Dominant valuation model: {dominant}\n"
        f"Fundamental quality: {rubric.get('quality')}\n"
        f"PT source: {rubric.get('pt_source')}\n"
        f"Inputs ({overrides_str}):\n{drivers_str}\n"
        f"\n"
        f"Write EXACTLY 2 sentences that explain the {rubric['rating']} rating. "
        f"Be specific about the drivers above. Do not invent facts not listed. "
        f"Do not include the words 'I' or 'we'. No preamble, no markdown — just the two sentences."
    )
    return call_anthropic_haiku(prompt)


def _build_trailing_series_view(f: dict) -> dict:
    """
    Extract the trailing 8 quarters of revenue / EBITDA / FCF / debt / EPS for
    display in the report, plus derived per-quarter margins so the reader
    can see the quality trajectory directly.
    """
    import sys
    sys.path.insert(0, "/home/nixos/Prod/V1/src")
    from price_targets import _lr_annualized_growth

    def tail(seq, n=8):
        return list(seq[-n:]) if seq else []
    rev = tail(f.get("revenue_series") or [])
    ebi = tail(f.get("ebitda_series") or [])
    fcf = tail(f.get("fcf_series") or [])
    dbt = tail(f.get("debt_series") or [])
    eps_q = tail(f.get("eps_series") or [])

    # Per-quarter margins (where defined)
    ebitda_margin = []
    fcf_margin    = []
    # Align by index from the END (most recent), since series can have
    # different lengths after the _clean() filter in fundamentals_lookup
    n = min(len(rev), max(len(ebi), len(fcf)))
    for i in range(n):
        r = rev[-(i+1)] if i < len(rev) else None
        e = ebi[-(i+1)] if i < len(ebi) else None
        c = fcf[-(i+1)] if i < len(fcf) else None
        if r and r > 0:
            if e is not None: ebitda_margin.append(e / r)
            if c is not None: fcf_margin.append(c / r)
    ebitda_margin = list(reversed(ebitda_margin))
    fcf_margin    = list(reversed(fcf_margin))

    # EPS TTM (sum of last 4q) — comparable to typical P/E quote
    eps_ttm = sum(eps_q[-4:]) if len(eps_q) >= 4 else (sum(eps_q) if eps_q else None)

    return {
        "revenue_8q":  rev,
        "ebitda_8q":   ebi,
        "fcf_8q":      fcf,
        "debt_8q":     dbt,
        "eps_8q":      eps_q,
        "eps_ttm":     round(eps_ttm, 2) if eps_ttm is not None else None,
        "ebitda_margin_8q": ebitda_margin,
        "fcf_margin_8q":    fcf_margin,
        "trend_growth": {
            "rev_full_lr":    round((_lr_annualized_growth(f.get("revenue_series") or []) or 0) * 100, 2),
            "ebitda_full_lr": round((_lr_annualized_growth(f.get("ebitda_series") or []) or 0) * 100, 2),
            "fcf_full_lr":    round((_lr_annualized_growth(f.get("fcf_series") or []) or 0) * 100, 2),
            "eps_full_lr":    round((_lr_annualized_growth(f.get("eps_series") or []) or 0) * 100, 2),
        },
    }


def _ticker_news_and_analysts(ticker: str) -> dict:
    """
    Pull recent news headlines from finnhub_signals.json + analyst price-target
    data from the screener_universe.csv. Both files refresh on the daily
    Finnhub + screener crons, so this is current within ~24h.
    """
    import csv
    out = {
        "news_count_24h":   None,
        "news_polarity":    None,
        "headlines":        [],
        "analyst_count":    None,
        "analyst_target":   None,
        "analyst_high":     None,
        "analyst_low":      None,
        "analyst_buy":      None,
        "analyst_hold":     None,
        "analyst_sell":     None,
        "analyst_divergence_flag": None,
    }
    # News from finnhub_signals.json
    try:
        d = json.loads(Path("/home/nixos/Prod/V1/src/finnhub_signals.json").read_text())
        rec = (d.get("tickers") or {}).get(ticker.upper(), {})
        n = rec.get("news") or {}
        out["news_count_24h"] = n.get("count_24h")
        out["news_polarity"]  = n.get("polarity")
        # Take last 4 headlines for the report's news card
        hl = n.get("headlines") or []
        out["headlines"] = [
            {
                "title": (h.get("h") or "")[:140],
                "source": h.get("src") or "",
                "ts":    h.get("ts"),
            } for h in hl[:4] if isinstance(h, dict)
        ]
    except Exception as e:
        out["news_error"] = str(e)[:120]
    # Analyst data from screener_universe.csv
    try:
        with open("/home/nixos/Prod/V1/outputs/screener_universe.csv") as fh:
            for row in csv.DictReader(fh):
                if (row.get("ticker") or "").upper() == ticker.upper():
                    def _f(k):
                        v = row.get(k)
                        if v in (None, "", "None"): return None
                        try: return float(v)
                        except ValueError: return None
                    def _i(k):
                        v = _f(k); return int(v) if v is not None else None
                    out["analyst_count"]  = _i("analyst_count")
                    out["analyst_target"] = _f("analyst_target_mean") or _f("target_price")
                    out["analyst_buy"]    = _i("analyst_buy")
                    out["analyst_hold"]   = _i("analyst_hold")
                    out["analyst_sell"]   = _i("analyst_sell")
                    fl = row.get("analyst_divergence_flag")
                    out["analyst_divergence_flag"] = (fl == "True" or fl == "1") if fl else None
                    break
    except Exception as e:
        out["analyst_error"] = str(e)[:120]
    return out


def _sector_comparison(sector: Optional[str], pt_block: dict, f: dict) -> dict:
    """
    Compare the ticker's effective (trailing) multiples to the sector anchor
    multiples used by the PT engine. Lets the reader see whether the stock
    trades rich or cheap on each axis vs its sector peers.
    """
    import sys
    sys.path.insert(0, "/home/nixos/Prod/V1/src")
    from price_targets import _get_sector_multiples
    sm = _get_sector_multiples(sector, 0.0425, 0.0250, apply_compression=True)

    rev = f.get("revenue_series") or []
    ebi = f.get("ebitda_series") or []
    fcf = f.get("fcf_series") or []
    debt = f.get("debt_series") or []
    mkt = f.get("marketcap") or 0
    cash = f.get("cash_on_hand") or 0
    latest_debt = debt[-1] if debt else 0
    ev = (mkt or 0) + latest_debt - cash

    eff_ev_ebitda = (ev / (ebi[-1] * 4)) if ebi and ebi[-1] > 0 else None
    eff_ev_rev    = (ev / (rev[-1] * 4)) if rev and rev[-1] > 0 else None
    eff_fcf_yield = ((fcf[-1] * 4) / mkt) if fcf and fcf[-1] is not None and mkt else None

    def discount(eff, anchor, higher_is_premium=True):
        """Returns 'rich'/'cheap'/'in-line' relative to anchor."""
        if eff is None or anchor is None or anchor == 0: return None
        ratio = eff / anchor
        if higher_is_premium:
            if ratio > 1.15: return "rich"
            if ratio < 0.85: return "cheap"
        else:
            if ratio > 1.15: return "cheap"   # higher fcf yield = cheaper
            if ratio < 0.85: return "rich"
        return "in-line"

    return {
        "sector":           sector or "_default",
        "anchor_ev_ebitda": round(sm["ev_ebitda"], 2),
        "anchor_ev_rev":    round(sm["ev_rev"], 2),
        "anchor_fcf_yield": round(sm["raw"]["fcf_yield"] * 100, 2),
        "eff_ev_ebitda":    round(eff_ev_ebitda, 2) if eff_ev_ebitda else None,
        "eff_ev_rev":       round(eff_ev_rev, 2) if eff_ev_rev else None,
        "eff_fcf_yield":    round(eff_fcf_yield * 100, 2) if eff_fcf_yield else None,
        "ev_ebitda_pos":    discount(eff_ev_ebitda, sm["ev_ebitda"]),
        "ev_rev_pos":       discount(eff_ev_rev, sm["ev_rev"]),
        "fcf_yield_pos":    discount(eff_fcf_yield, sm["raw"]["fcf_yield"], higher_is_premium=False),
    }


def build_report(ticker: str) -> dict:
    """Compose the full per-ticker valuation report payload."""
    import sys
    sys.path.insert(0, "/home/nixos/Prod/V1/src")
    from fundamentals_lookup import fetch_fundamentals

    all_a = load_assumptions()
    stored = all_a.get(ticker) or {}
    overrides = stored.get("overrides")
    pt_payload = compute_pt_payload(ticker, overrides)
    if pt_payload.get("error"):
        return {"ticker": ticker, "error": pt_payload["error"]}

    f = fetch_fundamentals(ticker) or {}
    pt_block = pt_payload.get("pt_with_overrides") or pt_payload.get("pt_engine_default") or {}
    sector = (pt_block.get("breakdown") or {}).get("sector")
    trailing = _build_trailing_series_view(f)
    sector_comp = _sector_comparison(sector, pt_block, f)
    catalysts = _ticker_news_and_analysts(ticker)

    rubric = compute_valuation_rubric(pt_payload)

    # LLM narration — cached on the assumptions record so we don't re-burn API
    # calls. Cache key = (rating, target_price, rounded live_price). Including
    # live_price invalidates the cache when the price feed becomes fresh
    # (e.g. ticker was hitting the $100 placeholder before BBG started
    # capturing it).
    pt_block = pt_payload.get("pt_with_overrides") or pt_payload.get("pt_engine_default") or {}
    _lp_round = round(pt_payload.get("live_price") or 0, 0)
    cache_key = f"{rubric['rating']}::{pt_block.get('target_price')}::{_lp_round}"
    cached_key = stored.get("llm_cache_key")
    cached_text = stored.get("llm_summary")

    if cached_text and cached_key == cache_key:
        summary = cached_text
        llm_used = "cache"
    else:
        summary = narrate_rubric(ticker, rubric, pt_payload)
        llm_used = "live" if summary else "none"
        if summary:
            with _assumptions_lock:
                cur = load_assumptions()
                rec = cur.get(ticker) or {}
                rec["llm_summary"]   = summary
                rec["llm_rating"]    = rubric["rating"]
                rec["llm_cache_key"] = cache_key
                cur[ticker] = rec
                save_assumptions(cur)

    return {
        "ticker":         ticker,
        "rubric":         rubric,
        "pt_payload":     pt_payload,
        "summary":        summary,
        "summary_src":    llm_used,         # 'live' | 'cache' | 'none'
        "trailing":       trailing,         # 8q series + derived margins
        "sector_comp":    sector_comp,      # effective vs anchor multiples
        "catalysts":      catalysts,        # news headlines + analyst targets
        "company_info":   {
            "marketcap":   f.get("marketcap"),
            "cash_on_hand": f.get("cash_on_hand"),
            "n_quarters":  f.get("n_quarters"),
            "latest_datekey": f.get("latest_datekey"),
        },
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }


# ─── HTTP handler ──────────────────────────────────────────────────────────
CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Content-Type":                 "application/json",
}


class RefreshHandler(BaseHTTPRequestHandler):
    def _send_json(self, code: int, body: dict):
        self.send_response(code)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(json.dumps(body, default=str).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    # ─── GET ────────────────────────────────────────────────────────
    def do_GET(self):
        if self.path == "/refresh":
            if state["running"]:
                self._send_json(200, {"message": "Refresh already in progress", "state": state})
            else:
                threading.Thread(target=run_refresh, daemon=True).start()
                self._send_json(202, {"message": "Refresh started", "state": state})
            return

        if self.path == "/status":
            self._send_json(200, {"state": state})
            return

        if self.path == "/pinned":
            self._send_json(200, {"pinned": load_pinned()})
            return

        if self.path.startswith("/assumptions/"):
            try:
                ticker = self.path.split("/")[-1].upper()
                if not TICKER_RE.match(ticker):
                    self._send_json(400, {"error": f"invalid ticker: {ticker!r}"})
                    return
                all_a = load_assumptions()
                stored = all_a.get(ticker) or {}
                overrides = stored.get("overrides")
                payload = compute_pt_payload(ticker, overrides)
                payload["updated_at"]  = stored.get("updated_at")
                payload["llm_summary"] = stored.get("llm_summary")
                payload["llm_rating"]  = stored.get("llm_rating")
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if self.path.startswith("/report/"):
            try:
                ticker = self.path.split("/")[-1].upper()
                if not TICKER_RE.match(ticker):
                    self._send_json(400, {"error": f"invalid ticker: {ticker!r}"})
                    return
                rep = build_report(ticker)
                self._send_json(200, rep)
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send_json(500, {"error": str(e)})
            return

        if self.path == "/assumptions":
            # List all tickers with stored overrides PLUS their recomputed PT
            # so the dashboard can replace the screener-CSV target price in
            # the Top 40 cell without doing a per-ticker GET. The recompute
            # is fast (parquet is lru_cached) so we just call it for each.
            try:
                all_a = load_assumptions()
                summary = {}
                for t, rec in all_a.items():
                    ov = rec.get("overrides") or {}
                    if not any(v is not None for v in ov.values()):
                        continue
                    try:
                        pt_payload = compute_pt_payload(t, ov)
                        u = (pt_payload.get("pt_with_overrides") or {})
                        e_pt = (pt_payload.get("pt_engine_default") or {}).get("target_price")
                        summary[t] = {
                            "updated_at":   rec.get("updated_at"),
                            "n_overrides":  sum(1 for v in ov.values() if v is not None),
                            "user_pt":      u.get("target_price"),
                            "engine_pt":    e_pt,
                            "upside_pct":   u.get("upside_pct"),
                        }
                    except Exception as ie:
                        summary[t] = {
                            "updated_at":   rec.get("updated_at"),
                            "n_overrides":  sum(1 for v in ov.values() if v is not None),
                            "error":        str(ie)[:120],
                        }
                self._send_json(200, {"assumptions": summary})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if self.path.startswith("/predictions/"):
            try:
                from urllib.parse import urlparse, parse_qs
                import psycopg
                parsed = urlparse(self.path)
                ticker = parsed.path.split("/")[-1].upper()
                qs = parse_qs(parsed.query)
                hours = int(qs.get("hours", ["24"])[0])
                hours = max(1, min(hours, 24 * 14))

                with psycopg.connect(
                    "host=/run/postgresql user=nixos dbname=rcg_signals"
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT s.run_id, r.run_timestamp,
                                   s.signal_name, s.signal_value, s.signal_string
                            FROM signals s
                            JOIN runs r ON s.run_id = r.run_id
                            WHERE r.run_type = 'live_prediction'
                              AND s.ticker = %s
                              AND r.run_timestamp > NOW() - (%s || ' hours')::interval
                            ORDER BY r.run_timestamp ASC, s.signal_name
                            """,
                            (ticker, str(hours)),
                        )
                        rows = cur.fetchall()

                by_run = {}
                for run_id, run_ts, name, val, sval in rows:
                    rec = by_run.setdefault(run_id, {"run_id": run_id, "ts": run_ts.isoformat()})
                    rec[name] = val if val is not None else sval

                snapshots = sorted(by_run.values(), key=lambda r: r["ts"])
                self._send_json(200, {"ticker": ticker, "hours": hours, "rows": snapshots})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        self.send_response(404)
        for k, v in CORS.items(): self.send_header(k, v)
        self.end_headers()

    # ─── POST ───────────────────────────────────────────────────────
    def do_POST(self):
        if self.path.startswith("/assumptions/"):
            try:
                ticker = self.path.split("/")[-1].upper()
                if not TICKER_RE.match(ticker):
                    self._send_json(400, {"error": f"invalid ticker: {ticker!r}"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length > 0 else "{}"
                try:
                    body = json.loads(raw or "{}")
                except json.JSONDecodeError as e:
                    self._send_json(400, {"error": f"invalid json: {e}"})
                    return

                overrides = sanitize_overrides(body)
                # Reject if all values are None — that's effectively a DELETE
                if not any(v is not None for v in overrides.values()):
                    self._send_json(400, {"error": "no overrides provided; use DELETE to clear"})
                    return

                with _assumptions_lock:
                    all_a = load_assumptions()
                    prev = all_a.get(ticker) or {}
                    # New overrides invalidate any cached LLM summary
                    all_a[ticker] = {
                        "overrides":   overrides,
                        "updated_at":  datetime.now(timezone.utc).isoformat(),
                        "llm_summary": None,
                        "llm_rating":  None,
                    }
                    save_assumptions(all_a)

                # Recompute PT with the new overrides and return it
                payload = compute_pt_payload(ticker, overrides)
                payload["updated_at"]  = all_a[ticker]["updated_at"]
                payload["llm_summary"] = None
                payload["llm_rating"]  = None
                print(f"[assumptions] +{ticker} {overrides}")
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if self.path.startswith("/pinned/"):
            ticker = self.path.split("/")[-1].upper().strip()
            if not TICKER_RE.match(ticker):
                self._send_json(400, {"error": f"invalid ticker: {ticker!r}"})
                return

            with _pin_lock:
                pinned = load_pinned()
                if ticker not in pinned:
                    pinned.append(ticker)
                    save_pinned(pinned)
                    newly_pinned = True
                else:
                    newly_pinned = False

            ok, msg = update_watchlist_and_push([ticker])
            print(f"[pin] +{ticker} (newly={newly_pinned}) · {msg}")

            # Trigger BBG pull + sentiment in background so the new ticker
            # gets data immediately. Don't block the HTTP response.
            if not state["running"]:
                threading.Thread(target=run_refresh, daemon=True).start()

            self._send_json(
                202,
                {
                    "ticker":         ticker,
                    "newly_pinned":   newly_pinned,
                    "pinned":         load_pinned(),
                    "watchlist_push": msg,
                    "refresh_kicked": not state["running"],
                },
            )
            return

        self.send_response(404)
        for k, v in CORS.items(): self.send_header(k, v)
        self.end_headers()

    # ─── DELETE ─────────────────────────────────────────────────────
    def do_DELETE(self):
        if self.path.startswith("/assumptions/"):
            try:
                ticker = self.path.split("/")[-1].upper()
                with _assumptions_lock:
                    all_a = load_assumptions()
                    existed = ticker in all_a
                    if existed:
                        del all_a[ticker]
                        save_assumptions(all_a)
                print(f"[assumptions] -{ticker} (removed={existed})")
                # Return the engine-default PT (no overrides) so the dashboard
                # can immediately show the reverted value
                payload = compute_pt_payload(ticker, None)
                payload["removed"] = existed
                self._send_json(200, payload)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if self.path.startswith("/pinned/"):
            ticker = self.path.split("/")[-1].upper().strip()
            with _pin_lock:
                pinned = load_pinned()
                if ticker in pinned:
                    pinned.remove(ticker)
                    save_pinned(pinned)
                    removed = True
                else:
                    removed = False

            print(f"[pin] -{ticker} (removed={removed})")
            self._send_json(200, {"ticker": ticker, "removed": removed,
                                  "pinned": load_pinned()})
            return

        self.send_response(404)
        for k, v in CORS.items(): self.send_header(k, v)
        self.end_headers()

    def log_message(self, format, *args):
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), RefreshHandler)
    print(f"Refresh server running on port {PORT}")
    print(f"  GET    http://rcg-nixos:{PORT}/refresh               → trigger refresh")
    print(f"  GET    http://rcg-nixos:{PORT}/status                → check status")
    print(f"  GET    http://rcg-nixos:{PORT}/pinned                → list pinned tickers")
    print(f"  POST   http://rcg-nixos:{PORT}/pinned/<TICKER>       → pin (force into BBG pull)")
    print(f"  DELETE http://rcg-nixos:{PORT}/pinned/<TICKER>       → unpin")
    print(f"  GET    http://rcg-nixos:{PORT}/assumptions           → list tickers with stored overrides")
    print(f"  GET    http://rcg-nixos:{PORT}/assumptions/<TICKER>  → baseline + overrides + recomputed PT")
    print(f"  POST   http://rcg-nixos:{PORT}/assumptions/<TICKER>  → save overrides (body: JSON of growth deltas)")
    print(f"  DELETE http://rcg-nixos:{PORT}/assumptions/<TICKER>  → clear overrides for this ticker")
    print(f"  GET    http://rcg-nixos:{PORT}/report/<TICKER>       → 1-page valuation report (rubric + LLM)")
    server.serve_forever()


if __name__ == "__main__":
    main()
