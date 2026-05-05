# RCG Signal Capture — CONTEXT
**Last updated:** 2026-04-29
**Status:** Phase 2A Sessions 1+2 complete (storage layer + signals DB live)

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
| 3 | `storage.py` GCS abstraction, `screener_capture_patch.py` for capture in screener | NEXT |
| 4 | Sharadar parallel mirror to GCS, Bloomberg-to-GCS replacement, GitHub repo + initial commit | TBD |
| 5 | End-to-end validation, 24h observation, shadow-run period begins | TBD |

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

## Open items
- [x] Session 2: Postgres install (NixOS service), schema design, `signals_db.py`
- [ ] Session 3: `storage.py` (GCS abstraction reading existing local fallback for transition),
       `screener_capture_patch.py`, integration into existing `run_screener.py`
- [ ] Session 4: Sharadar parallel mirror cron, Bloomberg-to-GCS replacement on Windows,
       GitHub repo + initial commit (Nick to create the repo URL ahead of Session 4)
- [ ] Session 5: end-to-end smoke test, 24h shadow observation
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
