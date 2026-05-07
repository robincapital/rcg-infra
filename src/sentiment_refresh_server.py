"""
sentiment_refresh_server.py
Lightweight API server that triggers Bloomberg price refresh + sentiment rerun.
Runs on port 8085 on NixOS, called by button on sentiment HTML dashboard.

Endpoints:
  GET    /refresh                 → trigger full pipeline (BBG pull + sentiment)
  GET    /status                  → last-refresh time + current state
  GET    /predictions/<TICKER>    → captured prediction history for one ticker
  GET    /pinned                  → list of user-pinned ("starred"/ad-hoc) tickers
  POST   /pinned/<TICKER>         → pin a ticker (immediate effect: watchlist + BBG pull)
  DELETE /pinned/<TICKER>         → unpin a ticker (next screener cycle drops it)
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
    print(f"  GET    http://rcg-nixos:{PORT}/refresh           → trigger refresh")
    print(f"  GET    http://rcg-nixos:{PORT}/status            → check status")
    print(f"  GET    http://rcg-nixos:{PORT}/pinned            → list pinned tickers")
    print(f"  POST   http://rcg-nixos:{PORT}/pinned/<TICKER>   → pin (force into BBG pull)")
    print(f"  DELETE http://rcg-nixos:{PORT}/pinned/<TICKER>   → unpin")
    server.serve_forever()


if __name__ == "__main__":
    main()
