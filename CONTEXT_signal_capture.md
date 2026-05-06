# RCG Signal Capture — CONTEXT
**Last updated:** 2026-05-06
**Status:** Phase 2A Sessions 1–4 complete; Session 5 (shadow observation) ongoing; Live Trading dashboard + predictions capture live

---

## Why this exists
Phase 1 (price targets consolidation) shipped a working engine. Phase 2 builds the
analytics scaffolding — short-term alpha signals, intraday data, and a self-improving
feedback loop. None of that compounds without infrastructure to **capture every signal
the system emits** and join it to forward returns.

This is the prerequisite. Before any new alpha is layered on, we put a signal-capture
substrate in place. Every PT, regime classification, composite score, and (eventually)
short-term alpha score lands in a Postgres database and gets joined to forward returns
later for IC computation, regime-conditional weight calibration, and per-model performance
attribution.

This doc tracks the infrastructure build that enables that loop.

---

## End-state architecture

```
                                  ┌──────────────────────────────┐
                                  │   SIGNAL CAPTURE LAYER       │
                                  │  (every signal stored, dated, │
                                  │   labeled with forward window)│
                                  │                               │
                                  │   Postgres on NixOS           │
                                  │   nightly backup → GCS        │
                                  └──────────────┬───────────────┘
                                                 │
              ┌──────────────────────────────────┼──────────────────────────────────┐
              │                                  │                                  │
              ▼                                  ▼                                  ▼
   ┌────────────────────┐       ┌─────────────────────────┐       ┌─────────────────────┐
   │ TIER 1 — DAILY     │       │ TIER 2 — INTRADAY       │       │ TIER 3 — TICK/MR    │
   │                    │       │                         │       │                     │
   │ Sharadar SEP +     │       │ Bloomberg intraday      │       │ Bloomberg tick      │
   │ SF1 (full universe)│       │ bars on 50-200 names    │       │ (existing watchlist)│
   │ ─→ NixOS local     │       │ ─→ GCS                  │       │                     │
   │ ─→ GCS (mirror)    │       │                         │       │                     │
   └────────────────────┘       └─────────────────────────┘       └─────────────────────┘
                         │                  │                                │
                         └──────────────────┴────────────────┬───────────────┘
                                                             ▼
                                       ┌──────────────────────────────────┐
                                       │   PERFORMANCE ATTRIBUTION        │
                                       │   (Phase 2D — joins signals      │
                                       │    to forward returns)           │
                                       └──────────────────────────────────┘
```

Two books at the output layer (not yet built — Phase 2C):
- **CORE PORTFOLIO BOOK** — fundamentals 60-70%, technicals 20-30%, sentiment 10%
  - "What to own" — hold weeks to quarters
- **TRADING BOOK** — short-term alpha 50-60%, fundamentals 20-30%, MR/breakout 20%
  - "What to trade" — hold hours to days
- **Porous boundary**: same name can appear in both books with different signals + intent

---

## Cloud architecture (live)

```
GCP project:        rcg-prod-12508
GCS bucket:         gs://rcg-prod-data         (us-east1, public-access blocked, IAM-only)
Service account:    rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com
                    role: storage.objectAdmin on the bucket only
Auth mode:          Application Default Credentials (no JSON key files — org policy
                    blocks long-lived keys, which is the modern recommended posture)

NixOS box:          gcloud installed via nix-env, ADC at ~/.config/gcloud/
Windows laptop:     gcloud installed via Cloud SDK installer, ADC at AppData
Phase 2A venv:      /home/nixos/venv-rcg-prod/   (Python 3.12, has GCS SDK, BQ SDK, psycopg2)
```

GCS layout (planned):
```
gs://rcg-prod-data/
  ├── sharadar/
  │   ├── sf1/year=2026/month=04/day=29/sf1.parquet
  │   ├── sep/year=2026/month=04/day=29/sep.parquet
  │   └── tickers/year=2026/month=04/tickers.parquet
  ├── bloomberg/
  │   ├── intraday/date=2026-04-29/bbg.parquet
  │   └── eod/date=2026-04-29/bbg.parquet
  ├── finnhub/price_targets/date=2026-04-29/pt.parquet
  ├── outputs/
  │   ├── screener/date=2026-04-29/long_screener.csv
  │   └── reports/date=2026-04-29/TEM.pdf
  └── db_backups/signals_2026-04-29.sql.gz
```

**Sharadar approach (parallel mirror, not migration):** the existing NixOS cron that pulls
Sharadar to local disk stays unchanged. We add a sibling job that ALSO writes to GCS. The
existing screener keeps reading local. New code (Phase 2A+) reads from GCS. No risk to the
production daily.

---

## Phase 2A — broken into 5 sessions

| Session | Scope | Status |
|---|---|---|
| 1 | GCP project, bucket, service account, ADC on Windows + NixOS, Python venv | ✅ DONE |
| 2 | Postgres on NixOS, signals DB schema, `signals_db.py` API | ✅ DONE |
| 3 | `storage.py` GCS abstraction, `screener_capture_patch.py` for capture in screener | ✅ DONE |
| 4 | DB backup → GCS, lifecycle policy, Sharadar GCS mirror, GitHub repo, Bloomberg-to-GCS, refresh-button pipeline | ✅ DONE |
| 5 | End-to-end validation, 24h observation, shadow-run period begins | 🟡 IMPLICITLY ONGOING (live runs accumulating) |

After Session 5: shadow run for 1 week → Phase 2B (Tier 2 enriched signals + short-term alpha).

---

## Session 1 log (2026-04-29) — DONE

### Decisions made
- Cloud provider: **GCP** (BigQuery advantage for Phase 2D; service-account simplicity vs AWS)
- GCS region: **us-east1** (lowest latency from NixOS in Miami)
- Storage scope: **Sharadar runs in parallel to GCS** — existing NixOS local-disk cron stays put
- Repo strategy: **private GitHub repo** (URL TBD — to be set up in Session 4)
- Books: **porous boundary** between core and trading (same name, different signals)
- Cutover style: **shadow run for 1 week** before Phase 2B layers on top
- Postgres deployment: **NixOS service** (`services.postgresql.enable = true;`) — Session 2

### Steps completed
1. GCP project `rcg-prod-12508` created with billing enabled
2. APIs enabled: storage, storage-component, IAM, IAMcredentials
3. Bucket `gs://rcg-prod-data` created (us-east1, uniform-bucket-level-access, public-access-prevention)
4. Service account `rcg-prod-app@rcg-prod-12508.iam.gserviceaccount.com` created
5. IAM binding: `storage.objectAdmin` on `gs://rcg-prod-data` only
6. **Pivot from JSON key to ADC** — org policy `iam.disableServiceAccountKeyCreation` blocks
   long-lived keys (good thing — it's the modern security posture). Switched to gcloud ADC
   for both Windows and NixOS.
7. gcloud CLI installed and authenticated on Windows + NixOS
8. Smoke tests: write/list/read/delete from both Windows and NixOS — all pass
9. Phase 2A Python venv created at `/home/nixos/venv-rcg-prod/`
10. Installed: `google-cloud-storage 3.10.1`, `google-cloud-bigquery 3.41.0`, `psycopg2-binary 2.9.12`
11. Verified ADC reaches Python SDK in venv: `Bucket exists: True`

### Things to remember
- The Phase 2A venv is at `/home/nixos/venv-rcg-prod/`, **not** the same as `venv-sentiment`
- ADC creds are at `~/.config/gcloud/application_default_credentials.json` on NixOS
- Token refresh is automatic; no manual rotation needed
- All gcloud commands target `rcg-prod-12508` by default (set in both gcloud configs)

---

## Session 2 log (2026-04-29) — DONE

### Decisions made
- **Postgres 16** chosen (vs 15 / 17 — middle of currently-supported versions)
- **Unix socket only**, no TCP exposure (`enableTCPIP = false`). Code connects via
  `host=/run/postgresql` with peer authentication — no password storage anywhere.
- **`nixos` user owns `rcg_signals` database** — same user that runs the screener,
  natural permission boundary.
- **Driver: psycopg v3** (not psycopg2). The pip-installed `psycopg2-binary`
  failed with `ImportError: libz.so.1` because Nix doesn't put system libraries
  on FHS paths. Switched to `psycopg[binary]` v3 which bundles libpq statically.
- **Schema design: 4 tables** — `runs` (run metadata), `signals` (the workhorse,
  flexible schema with value/string/json columns), `forward_returns` (empty for
  now, populated by Phase 2D), `schema_version` (migration tracking).
- **Capture is best-effort, never blocking.** If the DB is down, `signals_db.py`
  logs the error and returns None / 0 / False as appropriate. The screener will
  never fail because capture failed.

### Steps completed
1. Added `services.postgresql` block to `/etc/nixos/claude-finance.nix`
   (Postgres 16, Unix socket, ensureDatabases, ensureUsers)
2. **Hit Nix assertion**: `ensureDBOwnership = true` + DB name ≠ user name → required
   the database name to match the user name. Fixed by dropping `ensureDBOwnership`
   and `ensureClauses` — peer auth and explicit `GRANT` cover the same ground.
3. **Hit `permission denied for schema public`** — Postgres 15+ default.
   Fixed by `ALTER SCHEMA public OWNER TO nixos; GRANT ALL ON SCHEMA public TO nixos;`
   run as the postgres superuser.
4. Schema applied from `/home/nixos/Prod/V1/sql/rcg_signals_schema.sql` —
   4 tables, 8 indexes (5 named on `signals`, 1 on `forward_returns`,
   1 each on `runs`, plus PKs).
5. `signals_db.py` written and deployed to `/home/nixos/Prod/V1/src/`.
   - Connection management: lazy, auto-reconnect, 60s backoff on failure
   - Public API: `record_run`, `finalize_run`, `record_signal`,
     `record_signals_bulk`, `get_signal_history`, `get_run_signals`,
     `get_recent_runs`, `health_check`
6. End-to-end smoke test: `record_run` → 3 single signals + 3 bulk →
   `finalize_run` → readback → all clean. `health_check` reports 6 signals,
   1 run, PostgreSQL 16.6, connected.
7. Smoke test data truncated. Schema file moved from `/tmp/` to
   `/home/nixos/Prod/V1/sql/rcg_signals_schema.sql` (stable location).

### Things to remember
- Postgres data dir is `/var/lib/postgresql/16/` (NixOS default for v16);
  survives `nixos-rebuild switch`.
- Schema is at `/home/nixos/Prod/V1/sql/rcg_signals_schema.sql` —
  to recreate: `psql -U nixos -d rcg_signals -h /run/postgresql -f <path>`.
- Connection string for code: `host=/run/postgresql user=nixos dbname=rcg_signals`
- `signals_db.py` reads env vars `RCG_SIGNALS_DB`, `RCG_SIGNALS_USER`,
  `RCG_SIGNALS_HOST` — overridable for testing without code changes.
- `RCG_SIGNALS_DISABLE=1` env var disables capture entirely (kill switch).
- Schema version is 1, recorded in `schema_version` table. Migrations append.

---

## Session 3 log (2026-04-29 → 2026-05-05) — DONE

### What got deployed
- **`storage.py`** — GCS abstraction with local fallback, cache at `/tmp/rcg-cache/` (24h TTL).
  Public API: `read_parquet`, `write_parquet`, `read_text`, `write_text`, `write_blob`,
  `write_local_file`, `exists`, `health_check`. Env vars `RCG_GCS_BUCKET`,
  `RCG_LOCAL_FALLBACK`, `RCG_CACHE_DIR`.
- **`screener_capture_patch.py`** — monkeypatches `screener.apply_blended_targets`
  (per-ticker capture after blend) and `screener.main` (record_run / finalize_run).
  Captures ~20 numeric + ~3 string + 1 JSON per ticker, plus `_MARKET` row with
  regime z-scores/labels and dynamic weights. Best-effort throughout, never blocks.
- **`run_screener.py`** — `CAPTURE_SIGNALS = True` flag + install block calling
  `cap.install(screener_module=screener, config={...})`.

### Key blocker resolved (Jupyter kernel rebuild)
Cell 4 runs in the JupyterLab kernel (not the Phase 2A venv). The kernel is built by a
uv2nix flake at `/home/nixos/nixos/jupyter/`. The kernel was Python 3.12 but didn't have
`psycopg` or `google-cloud-storage`. Pip-installing in the venv-rcg-prod was useless —
ABI mismatch (3.11 vs 3.12 wheels).

**Fix:** added `psycopg[binary]>=3.3.0`, `google-cloud-storage>=3.0.0`, and `anthropic`
to the flake's `pyproject.toml`, ran `nix flake update` (bumped nixpkgs from 2025-02-01
to 2026-04-27 to fix a `pep600.nix` manylinux-tag issue), `nix build`, then restarted
the JupyterLab process. Kernel now imports `psycopg 3.3.3` and `google.cloud.storage 3.10.1`
natively.

### Verification (2026-05-05)
- 7 runs captured in Postgres
- 100,520 total signals across all runs
- Latest run (run_id=7): 16,114 signals across 753 distinct tickers (full universe,
  not just top-40 — much wider than originally projected)
- ~21 signals per ticker; key fields: industry, sector, marketcap, revenue/ebitda/fcf/debt
  trends, RSI/SMA-cross/momentum/sentiment scores, quality_score, PT engine outputs
  (upside_score, upside_pct, pt_source, target_price, pt_breakdown, gates_fired)
- `_MARKET` row carries regime + dynamic weights

### Known data-quality issues (deferred — Phase 2D pre-work)
- `started_at`, `n_tickers_in` fields are `None` on every run — either `record_run`
  isn't setting them or `get_recent_runs` isn't selecting them. Doesn't affect capture
  correctness, just metadata completeness.
- `analyst_target_mean` populated on only 78 / 753 names (matches the known Phase 1
  limitation: only top-80 fundamental candidates get Finnhub fetches).
- Slight signal-count drift across rows (752 / 703 / 595) — likely tickers missing
  certain data series; needs a low-cost audit before performance attribution begins.

---

## Session 4 log (2026-04-30 → 2026-05-05) — IN PROGRESS

### What got deployed (May 5)

**1. Nightly Postgres → GCS backup, declarative**
- Added `signalsBackupScript` derivation + `systemd.services.rcg-signals-backup` +
  `systemd.timers.rcg-signals-backup` to `/etc/nixos/claude-finance.nix`.
- Schedule: `*-*-* 03:00:00 America/New_York`, `Persistent = true`.
- Script uses `pg_dump --format=custom --compress=9` piped directly to
  `gcloud storage cp -`, no local temp file.
- Log at `/var/log/rcg/signals-backup.log`.
- Script paths are explicit nix-store references (`${pkgs.postgresql_16}/bin/pg_dump`,
  `${pkgs.google-cloud-sdk}/bin/gcloud`) — no PATH dependency.
- The earlier ad-hoc copy at `/var/rcg/scripts/rcg-signals-backup.sh` was deleted
  to remove drift risk.
- First manual run: 11s, 1.55 MB dump landed at
  `gs://rcg-prod-data/db_backups/year=2026/month=05/rcg_signals_20260505T164630Z.dump`.

**2. GCS lifecycle policy on `db_backups/`**
- 30-day retention, applied via `gcloud storage buckets update --lifecycle-file=...`.
- Lifecycle config lives as `gcs-lifecycle-db-backups.json` in the staging dir;
  reapplied with `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT=` because lifecycle
  needs `storage.admin` (the SA only has `storage.objectAdmin`; user is project
  Owner so direct-auth bypass works).

**3. Sharadar parallel mirror to GCS**
- Extended the existing `downloadScript` in `claude-finance.nix` (the daily
  Mon–Fri 04:20 ET service) with a post-store loop that uploads each parquet to
  `gs://rcg-prod-data/sharadar/<table>/year=YYYY/month=MM/day=DD/<table>.parquet`.
- Loop is non-blocking (each `gcloud cp` wrapped in `if/else`, failures counted but
  don't kill the script).
- Backfilled today's 13 tables manually (1.57 GB) to validate before the next
  scheduled run; tomorrow's 04:20 ET run is the integrated test.

**4. Private GitHub repo**
- Repo: `https://github.com/robincapital/rcg-infra`.
- Scope: production code only (per user decision) — `/home/nixos/Prod/V1/src/`,
  `/sql/`, top-level `CONTEXT_*.md`, `docs/`, `jupyter_cell_4_v2.py`, `watchlist.json`.
  Excluded: notebooks, runtime JSON state (factor_signals*.json,
  bloomberg_prices.json, refresh_status.json), generated outputs, `.bak` files,
  `Decom.Old/`, `QuantxAI/`.
- Initial commit: `3dc2763` (22 files).
- Auth: fine-grained PAT stored at `~/.git-credentials` via `credential.helper store`.
  PAT scoped to `rcg-infra` only (Contents: read+write).

**5. Known acceptance: hardcoded Finnhub API key**
- `src/dynamic_factor_screener_v3.py:1034` and `src/sentiment_refresh_server.py:23`
  contain a hardcoded Finnhub key. User chose to commit as-is and defer the
  refactor. Same key is also in the user's crontab and bash history. Repo is
  private. Rotation + env-var refactor is a follow-up.

**6. Bloomberg-to-GCS pipeline + refresh button (May 5)**
- `bloomberg_prices.py` (lives on Windows at `C:\Users\ndiaz\Downloads\`) extended
  with a third destination: in addition to local Dropbox JSON + SCP to NixOS, it now
  uploads the same JSON to `gs://rcg-prod-data/bloomberg/intraday/year=YYYY/month=MM/day=DD/bloomberg_prices_TIMESTAMP.json`.
  Each stage is independent (try/except wrapped) — Dropbox + SCP keep working even
  if GCS upload fails.
- **Windows Task Scheduler** entry `RCG-Bloomberg-Prices` runs the script every 30
  min, 09:00–17:00 daily (Sat/Sun no-op since Bloomberg has no fresh data).
  Logon mode: Interactive only (Bloomberg API needs the user logged in).
- **NixOS → Windows SSH** set up: OpenSSH Server installed via
  `Add-WindowsCapability`, firewall rule scoped to the Tailscale interface, NixOS's
  `id_ed25519` pubkey added to `C:\ProgramData\ssh\administrators_authorized_keys`
  (admins file because `ndiaz` is a local Administrator). ACL locked down per
  Windows OpenSSH requirements. Windows Tailscale IP: `100.86.90.78`.
- **`sentiment_refresh_server.py`** now runs as a declarative systemd service
  (`rcg-sentiment-refresh.service`, port 8085, `wantedBy = multi-user.target` so
  auto-starts on boot). The wrapper script prepends `${pkgs.openssh}/bin` to PATH
  so subprocess `ssh` calls work, and uses `python3 -u` for unbuffered logs.
  Stale hardcoded Windows IP (was `100.87.212.98`) updated to `100.86.90.78`.
- **End-to-end smoke test passed**: refresh button → server → SSH-to-Windows →
  bloomberg_prices.py runs (16s) → SCP back + GCS upload → sentiment_bbg.py runs →
  fresh `factor_signals_bbg.json` + HTML in 20s total.

**7. Live Trading dashboard + predictions capture (May 6)**
- New page **`src/trade.html`** served at `http://rcg-nixos:8080/trade.html` (port-8080
  static server). Two-column layout: left = top movers (sortable, scrollable),
  right = top-40 with fundamental composite + intraday metrics + signed predictive
  score + 5-bar signal indicators + action label. Click any ticker for inline
  expansion containing an inferno-styled SVG chart (price + SMA-5 + SMA-20 + VWAP +
  EOD/today H/L/±1σ band) plus a stats panel (5-bar momentum, 20-bar z, vs-VWAP,
  range expansion, up-bar ratio, V/ADV, signed pred score, action). Action labels
  are direction-aware: PRE-BREAKOUT / BREAKOUT / STRONG / WATCH / NEUTRAL /
  WEAKENING / PRE-BREAKDOWN / BREAKDOWN. Click a badge in the legend to filter the
  table to that label.
- **Bias signal fixed.** `market_sentiment_bbg.py` now down-weights MR weight by up
  to 60% on trending days via a new `_trend_strength(closes)` helper (persistence ×
  magnitude). On today's data, MR raw 40% → effective 37%, composite −0.31 → −0.08,
  label SELL → NEUTRAL. Dashboard chip also gates label to NEUTRAL when confidence <
  60% and shows components inline (`Sent ↑ BUY · MR ↓ −0.57 · conf 40%`).
- **Predictions capture pipeline.** New `src/predictions_capture.py` script + new
  `systemd.services.rcg-predictions-capture` + `systemd.timers.rcg-predictions-capture`
  in `claude-finance.nix`. Fires `Mon..Fri *-*-* 09..17:05,35:00 America/New_York`
  (every 30 min M-F market hours, 5 min after BBG Task Scheduler so prices land
  fresh). Each snapshot writes one `runs` row (`run_type='live_prediction'`) and
  ~16 signals per ticker into the `signals` table:
    pred_signed_score · pred_magnitude · pred_action (string) ·
    pred_surge · pred_udv · pred_accel · pred_vwap_slope · pred_range_exp ·
    live_price · eod_close · intraday_move · intraday_rsi · vol_now · adv ·
    vol_adv_ratio · fundamental_composite
  At 42 tickers × 16 signals × 18 fires/day, that's ~12K signals/day = ~365K/month.
  First captured run after deploy: run_id=10, 42 tickers, 659 signals.
- **Cross-functionality wiring** (May 5–6):
    - Sharadar local cron now writes parquet to GCS too (date-partitioned mirror)
    - `dynamic_factor_screener_v3.py` writes `watchlist.json` of top-40 + macros and
      SCPs to Windows so BBG Task Scheduler picks up the right universe
    - `bloomberg_prices.json` symlinked into `outputs/` so `trade.html` can
      `fetch('/bloomberg_prices.json')` from the static port-8080 server
    - `factor_signals_bbg.json` symlinked similarly for the bias chip

### Pending Session 4 polish (low priority)
- **ADC reauth** — `gcloud auth application-default login` on NixOS was attempted
  but didn't save. Not blocking anything currently; CLI auth is enough for the
  backup + Sharadar uploads. Needed before any Python SDK work that hits GCS
  (`storage.py` writes, etc.).
- **Finnhub key rotation + env-var refactor** — see Session 4.5 above. Same key
  is hardcoded in `sentiment_refresh_server.py:23` (which is now part of the
  systemd unit's runtime). No change in exposure surface vs before this session.
- **`bloomberg_prices.py` not in rcg-infra repo** — the user chose
  "production code only" scope and the script lives on Windows, so it stays
  out-of-tree for now. If Windows machine fails, the script is recoverable
  from Dropbox sync. Worth committing later for full version control.

---

## Open items
- [x] Session 2: Postgres install (NixOS service), schema design, `signals_db.py`
- [x] Session 3: `storage.py`, `screener_capture_patch.py`, integration into `run_screener.py`,
       Jupyter kernel rebuild for psycopg/google-cloud-storage
- [x] Session 4: nightly Postgres → GCS backup + 30-day lifecycle, Sharadar parallel
       mirror to GCS, GitHub repo + initial commit, Bloomberg-to-GCS pipeline,
       hourly Task Scheduler, NixOS↔Windows SSH, refresh-button systemd service,
       end-to-end smoke test
- [ ] Session 4 (cleanup): ADC reauth on NixOS (only when first Python SDK call needed),
       Finnhub key rotation + env-var refactor, bloomberg_prices.py to repo
- [ ] Session 5: end-to-end smoke test, 24h shadow observation
- [ ] Data-quality audit: investigate `started_at`/`n_tickers_in` None values, signal-count
       drift across runs (precondition for Phase 2D attribution)
- [ ] Phase 2B (after shadow week): `enriched_prices.py` + `short_term_alpha.py`
- [ ] Phase 2C: Tier 3 Bloomberg intraday expansion + trading book separation
- [ ] Phase 2D: performance attribution engine + walk-forward weight calibration
- [ ] Phase 2E (later): trade fill ingestion → realized P&L attribution

---

## Reverting — at this stage

Nothing to revert from Session 1. The bucket and service account exist; if we abandon the
build, just delete them (`gcloud storage buckets delete gs://rcg-prod-data --force` and
`gcloud iam service-accounts delete rcg-prod-app@...`). The existing screener and Phase 1
price-target work are untouched.
