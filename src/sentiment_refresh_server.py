"""
sentiment_refresh_server.py
Lightweight API server that triggers Bloomberg price refresh + sentiment rerun.
Runs on port 8085 on NixOS, called by button on sentiment HTML dashboard.

Endpoints:
  GET /refresh  → triggers full pipeline (BBG pull via SSH + sentiment rerun)
  GET /status   → returns last refresh time and current state
"""

import subprocess
import json
import os
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

# Track state
state = {
    "status": "idle",
    "last_refresh": None,
    "last_error": None,
    "running": False,
}


def run_refresh():
    """Execute the full refresh pipeline in a background thread."""
    global state
    if state["running"]:
        return

    state["running"] = True
    state["status"] = "refreshing"

    try:
        # Step 1: Trigger Bloomberg pull on Windows via SSH
        # The Windows machine runs bloomberg_prices.py which SCPs the result back
        state["status"] = "pulling bloomberg prices..."
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Triggering Bloomberg refresh...")

        # Try SSH to Windows to run the Bloomberg script
        # If this fails, the Bloomberg prices may still be fresh from the scheduled task
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
                # Continue anyway — use whatever prices we have
        except Exception as e:
            print(f"  Bloomberg SSH failed: {e} — using existing prices")

        # Step 2: Run sentiment script
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

    # Write status to file
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


class RefreshHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # CORS headers for cross-origin requests from the dashboard
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Content-Type": "application/json",
        }

        if self.path == "/refresh":
            if state["running"]:
                response = {"message": "Refresh already in progress", "state": state}
                self.send_response(200)
            else:
                # Launch refresh in background thread
                thread = threading.Thread(target=run_refresh, daemon=True)
                thread.start()
                response = {"message": "Refresh started", "state": state}
                self.send_response(202)

            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        elif self.path == "/status":
            response = {"state": state}
            self.send_response(200)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        elif self.path.startswith("/predictions/"):
            # GET /predictions/<TICKER>?hours=24
            #
            # Returns the captured live_prediction history for a single ticker
            # over the last N hours, pivoted into one row per snapshot with all
            # captured signal columns. Powers the per-ticker time-series chart
            # in the dashboard's expanded detail row.
            try:
                from urllib.parse import urlparse, parse_qs
                import psycopg
                parsed = urlparse(self.path)
                ticker = parsed.path.split("/")[-1].upper()
                qs = parse_qs(parsed.query)
                hours = int(qs.get("hours", ["24"])[0])
                hours = max(1, min(hours, 24 * 14))   # clamp 1h–14d

                # Query all signals for this ticker over the window, joined to
                # runs so we can pivot by run_id (each fire = one snapshot row).
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

                # Pivot: one record per run_id (each fire = one snapshot)
                by_run = {}
                for run_id, run_ts, name, val, sval in rows:
                    rec = by_run.setdefault(run_id, {"run_id": run_id, "ts": run_ts.isoformat()})
                    rec[name] = val if val is not None else sval

                # Sort chronologically and emit
                snapshots = sorted(by_run.values(), key=lambda r: r["ts"])
                response = {
                    "ticker": ticker,
                    "hours":  hours,
                    "rows":   snapshots,
                }
                self.send_response(200)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(json.dumps(response, default=str).encode())
            except Exception as e:
                self.send_response(500)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Quiet logging
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), RefreshHandler)
    print(f"Refresh server running on port {PORT}")
    print(f"  GET http://rcg-nixos:{PORT}/refresh  → trigger refresh")
    print(f"  GET http://rcg-nixos:{PORT}/status   → check status")
    server.serve_forever()


if __name__ == "__main__":
    main()
