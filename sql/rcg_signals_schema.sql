-- ════════════════════════════════════════════════════════════════
-- RCG Signal Capture — Schema v1
-- ════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS runs (
    run_id          BIGSERIAL PRIMARY KEY,
    run_timestamp   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_type        TEXT NOT NULL,
    config_json     JSONB,
    output_path     TEXT,
    n_tickers_in    INTEGER,
    n_tickers_out   INTEGER,
    runtime_seconds REAL,
    git_commit      TEXT,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_timestamp ON runs (run_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_runs_type      ON runs (run_type, run_timestamp DESC);

CREATE TABLE IF NOT EXISTS signals (
    signal_id       BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    signal_name     TEXT NOT NULL,
    signal_value    DOUBLE PRECISION,
    signal_string   TEXT,
    signal_json     JSONB,
    sector          TEXT,
    asof_date       DATE NOT NULL,
    asof_timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    horizon_days    INTEGER,
    metadata        JSONB
);

CREATE INDEX IF NOT EXISTS idx_signals_ticker_date    ON signals (ticker, asof_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_name_date      ON signals (signal_name, asof_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_run            ON signals (run_id);
CREATE INDEX IF NOT EXISTS idx_signals_ticker_name    ON signals (ticker, signal_name, asof_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_sector_date    ON signals (sector, asof_date DESC) WHERE sector IS NOT NULL;

CREATE TABLE IF NOT EXISTS forward_returns (
    ticker          TEXT NOT NULL,
    asof_date       DATE NOT NULL,
    horizon_days    INTEGER NOT NULL,
    realized_return DOUBLE PRECISION,
    entry_price     DOUBLE PRECISION,
    exit_price      DOUBLE PRECISION,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, asof_date, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_fwd_returns_date ON forward_returns (asof_date, horizon_days);

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);

INSERT INTO schema_version (version, description)
    VALUES (1, 'initial schema: runs, signals, forward_returns')
    ON CONFLICT (version) DO NOTHING;

\echo 'Tables in rcg_signals:'
\dt
\echo ''
\echo 'Indexes on signals table:'
\di signals*

