"""
storage.py  —  RCG Unified Storage Layer
=========================================
Single read/write interface that abstracts whether data lives on GCS or
local disk. Callers don't care — they call read_parquet('sharadar/sf1')
and get a polars DataFrame back.

WHY THIS EXISTS
---------------
Phase 2A migrates RCG's data layer from "local-disk-only" to "GCS canonical
+ local fallback." During the transition (Sessions 3-4), Sharadar still
writes to local disk via the existing cron, but new code wants to read
from GCS. This module hides that fork from callers.

PATH SCHEME
-----------
Logical path                        →  GCS path                                                 |  Local fallback
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
sharadar/sf1                        →  gs://rcg-prod-data/sharadar/sf1/year=YYYY/month=MM/day=DD/sf1.parquet
                                                                                                |  /var/sharadar/data/SF1.parquet
sharadar/sep                        →  ...sep/.../sep.parquet                                   |  /var/sharadar/data/SEP.parquet
sharadar/tickers                    →  ...tickers/.../tickers.parquet                           |  /var/sharadar/data/TICKERS.parquet
sharadar/sfp                        →  ...sfp/.../sfp.parquet                                   |  /var/sharadar/data/SFP.parquet
bloomberg/intraday                  →  ...bloomberg/intraday/date=YYYY-MM-DD/bbg.parquet        |  (no local fallback yet)
finnhub/price_targets               →  ...finnhub/price_targets/date=YYYY-MM-DD/pt.parquet      |  (in-memory only)
outputs/screener                    →  ...outputs/screener/date=YYYY-MM-DD/long_screener.csv    |  /home/nixos/Prod/V1/outputs/long_screener_results.csv

PUBLIC API
----------
read_parquet(logical_path, asof_date=None) -> polars.DataFrame
write_parquet(logical_path, df, asof_date=None)
read_text(logical_path, asof_date=None) -> str
write_text(logical_path, content, asof_date=None)
write_blob(logical_path, bytes_data, asof_date=None)
exists(logical_path, asof_date=None) -> bool
list_dates(logical_prefix) -> list[date]   # e.g. find which dates have data

CONFIG
------
RCG_GCS_BUCKET     env var, default 'rcg-prod-data'
RCG_LOCAL_FALLBACK env var, default 'true' (set to 'false' to require GCS)
RCG_CACHE_DIR      env var, default '/tmp/rcg-cache'
RCG_CACHE_TTL_HOURS  default 24

USAGE
-----
    import storage
    sep = storage.read_parquet('sharadar/sep')        # latest available
    sep = storage.read_parquet('sharadar/sep', asof_date=date(2026, 4, 28))
    storage.write_parquet('outputs/signals_export', signals_df)

Author: RCG / Nick Diaz
Version: 1.0  (2026-04-29)
"""
from __future__ import annotations

import io
import logging
import os
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import polars as pl

try:
    from google.cloud import storage as gcs_client
    _GCS_AVAILABLE = True
except ImportError:
    _GCS_AVAILABLE = False


# ============================================================
# CONFIG
# ============================================================
GCS_BUCKET     = os.environ.get("RCG_GCS_BUCKET", "rcg-prod-data")
LOCAL_FALLBACK = os.environ.get("RCG_LOCAL_FALLBACK", "true").lower() in ("1", "true", "yes")
CACHE_DIR      = Path(os.environ.get("RCG_CACHE_DIR", "/tmp/rcg-cache"))
CACHE_TTL_HOURS = int(os.environ.get("RCG_CACHE_TTL_HOURS", "24"))

logger = logging.getLogger("rcg.storage")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] [%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


# ============================================================
# LOCAL FALLBACK MAP
# Logical path → local disk path (None = no fallback available)
# ============================================================
LOCAL_FALLBACK_MAP = {
    "sharadar/sf1":     "/var/sharadar/data/SF1.parquet",
    "sharadar/sep":     "/var/sharadar/data/SEP.parquet",
    "sharadar/sfp":     "/var/sharadar/data/SFP.parquet",
    "sharadar/tickers": "/var/sharadar/data/TICKERS.parquet",
    "sharadar/daily":   "/var/sharadar/data/DAILY.parquet",
    "sharadar/sf2":     "/var/sharadar/data/SF2.parquet",
    "sharadar/sf3":     "/var/sharadar/data/SF3.parquet",
    "sharadar/actions": "/var/sharadar/data/ACTIONS.parquet",
    "sharadar/events":  "/var/sharadar/data/EVENTS.parquet",
    "sharadar/sp500":   "/var/sharadar/data/SP500.parquet",
    "sharadar/indicators": "/var/sharadar/data/INDICATORS.parquet",
}


# ============================================================
# GCS PATH BUILDERS
# ============================================================
def _gcs_path_for(logical_path: str, asof_date: Optional[date] = None) -> str:
    """
    Build the GCS object path for a logical path.

    For dataset paths (sharadar/sf1, etc.), uses date-partitioned layout.
    For output paths (outputs/screener), uses date partition.

    If asof_date is None, returns the path for the LATEST available date —
    the caller will need to enumerate to find that.
    """
    asof_date = asof_date or _today_utc()

    parts = logical_path.split("/")
    # sharadar/{table}      → sharadar/{table}/year=YYYY/month=MM/day=DD/{table}.parquet
    # bloomberg/{kind}      → bloomberg/{kind}/date=YYYY-MM-DD/bbg.parquet
    # finnhub/{kind}        → finnhub/{kind}/date=YYYY-MM-DD/pt.parquet
    # outputs/{kind}        → outputs/{kind}/date=YYYY-MM-DD/...

    if parts[0] == "sharadar" and len(parts) >= 2:
        table = parts[1]
        return (f"sharadar/{table}/"
                f"year={asof_date.year}/month={asof_date.month:02d}/day={asof_date.day:02d}/"
                f"{table}.parquet")

    if parts[0] == "bloomberg" and len(parts) >= 2:
        kind = parts[1]
        return f"bloomberg/{kind}/date={asof_date.isoformat()}/bbg.parquet"

    if parts[0] == "finnhub" and len(parts) >= 2:
        kind = parts[1]
        return f"finnhub/{kind}/date={asof_date.isoformat()}/pt.parquet"

    if parts[0] == "outputs":
        rest = "/".join(parts[1:])
        # caller's logical path may include the filename already
        return f"outputs/{rest}/date={asof_date.isoformat()}"

    # Pass-through for arbitrary paths
    return logical_path


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


# ============================================================
# CACHE
# ============================================================
def _cache_path_for(gcs_object: str) -> Path:
    safe = gcs_object.replace("/", "_").replace("=", "-")
    return CACHE_DIR / safe


def _is_cache_fresh(p: Path) -> bool:
    if not p.exists():
        return False
    age = time.time() - p.stat().st_mtime
    return age < CACHE_TTL_HOURS * 3600


# ============================================================
# GCS CLIENT
# ============================================================
_client = None


def _get_gcs_client():
    global _client
    if not _GCS_AVAILABLE:
        return None
    if _client is None:
        try:
            _client = gcs_client.Client()
        except Exception as e:
            logger.warning(f"GCS client init failed: {e}")
            return None
    return _client


def _gcs_blob_exists(gcs_object: str) -> bool:
    c = _get_gcs_client()
    if c is None:
        return False
    try:
        return c.bucket(GCS_BUCKET).blob(gcs_object).exists()
    except Exception as e:
        logger.warning(f"GCS exists() check failed for {gcs_object}: {e}")
        return False


def _gcs_download_to_cache(gcs_object: str) -> Optional[Path]:
    """Downloads a GCS object to local cache. Returns local path or None."""
    c = _get_gcs_client()
    if c is None:
        return None
    cache_p = _cache_path_for(gcs_object)
    if _is_cache_fresh(cache_p):
        return cache_p
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        b = c.bucket(GCS_BUCKET).blob(gcs_object)
        if not b.exists():
            return None
        b.download_to_filename(str(cache_p))
        logger.info(f"Cached gs://{GCS_BUCKET}/{gcs_object} → {cache_p}")
        return cache_p
    except Exception as e:
        logger.warning(f"GCS download failed for {gcs_object}: {e}")
        return None


def _gcs_upload(gcs_object: str, source_path: Path) -> bool:
    c = _get_gcs_client()
    if c is None:
        return False
    try:
        b = c.bucket(GCS_BUCKET).blob(gcs_object)
        b.upload_from_filename(str(source_path))
        logger.info(f"Uploaded {source_path} → gs://{GCS_BUCKET}/{gcs_object}")
        return True
    except Exception as e:
        logger.error(f"GCS upload failed for {gcs_object}: {e}")
        return False


def _gcs_upload_bytes(gcs_object: str, data: bytes,
                       content_type: str = "application/octet-stream") -> bool:
    c = _get_gcs_client()
    if c is None:
        return False
    try:
        b = c.bucket(GCS_BUCKET).blob(gcs_object)
        b.upload_from_string(data, content_type=content_type)
        logger.info(f"Uploaded {len(data)} bytes → gs://{GCS_BUCKET}/{gcs_object}")
        return True
    except Exception as e:
        logger.error(f"GCS upload (bytes) failed for {gcs_object}: {e}")
        return False


# ============================================================
# PUBLIC READ API
# ============================================================
def read_parquet(logical_path: str,
                  asof_date: Optional[date] = None) -> pl.DataFrame:
    """
    Read a parquet from GCS (preferred) or local disk (fallback).

    Tries (in order):
      1. GCS at the date-partitioned path (asof_date or today)
      2. GCS at recent prior dates (up to 7 days back) — handles late refresh
      3. Local disk fallback (if mapped and LOCAL_FALLBACK is enabled)
    """
    # 1+2: GCS path
    if _GCS_AVAILABLE and _get_gcs_client():
        gcs_obj = _gcs_path_for(logical_path, asof_date)
        cache_p = _gcs_download_to_cache(gcs_obj)
        if cache_p is None and asof_date is None:
            # Try recent prior dates
            for days_back in range(1, 8):
                d = _today_utc() - timedelta(days=days_back)
                gcs_obj = _gcs_path_for(logical_path, d)
                cache_p = _gcs_download_to_cache(gcs_obj)
                if cache_p:
                    logger.info(f"GCS hit at d-{days_back} for {logical_path}")
                    break
        if cache_p is not None:
            return pl.read_parquet(str(cache_p))

    # 3: Local fallback
    if LOCAL_FALLBACK:
        local = LOCAL_FALLBACK_MAP.get(logical_path)
        if local and Path(local).exists():
            logger.info(f"Local fallback hit for {logical_path} → {local}")
            return pl.read_parquet(local)

    raise FileNotFoundError(
        f"No data found for logical path '{logical_path}' "
        f"(asof_date={asof_date}). Tried GCS bucket={GCS_BUCKET} and local fallback."
    )


def read_text(logical_path: str, asof_date: Optional[date] = None) -> str:
    """Read a text file (CSV, JSON, etc.) from GCS preferred, local fallback."""
    if _GCS_AVAILABLE and _get_gcs_client():
        gcs_obj = _gcs_path_for(logical_path, asof_date)
        cache_p = _gcs_download_to_cache(gcs_obj)
        if cache_p is not None:
            return cache_p.read_text()

    if LOCAL_FALLBACK:
        local = LOCAL_FALLBACK_MAP.get(logical_path)
        if local and Path(local).exists():
            return Path(local).read_text()

    raise FileNotFoundError(f"No data found for logical path '{logical_path}'")


def exists(logical_path: str, asof_date: Optional[date] = None) -> bool:
    """Returns True if data is reachable via GCS or local fallback."""
    if _GCS_AVAILABLE and _get_gcs_client():
        if _gcs_blob_exists(_gcs_path_for(logical_path, asof_date)):
            return True
    if LOCAL_FALLBACK:
        local = LOCAL_FALLBACK_MAP.get(logical_path)
        if local and Path(local).exists():
            return True
    return False


# ============================================================
# PUBLIC WRITE API
# ============================================================
def write_parquet(logical_path: str,
                   df: pl.DataFrame,
                   asof_date: Optional[date] = None) -> bool:
    """Write a polars DataFrame to GCS as parquet. Returns True on success."""
    if not _GCS_AVAILABLE or not _get_gcs_client():
        logger.warning(f"GCS unavailable — write_parquet({logical_path}) skipped")
        return False
    buf = io.BytesIO()
    df.write_parquet(buf, compression="zstd")
    buf.seek(0)
    gcs_obj = _gcs_path_for(logical_path, asof_date)
    return _gcs_upload_bytes(gcs_obj, buf.getvalue(),
                              content_type="application/octet-stream")


def write_text(logical_path: str,
                content: str,
                asof_date: Optional[date] = None,
                content_type: str = "text/plain; charset=utf-8") -> bool:
    """Write a text payload (CSV, JSON, HTML) to GCS."""
    if not _GCS_AVAILABLE or not _get_gcs_client():
        logger.warning(f"GCS unavailable — write_text({logical_path}) skipped")
        return False
    gcs_obj = _gcs_path_for(logical_path, asof_date)
    return _gcs_upload_bytes(gcs_obj, content.encode("utf-8"),
                              content_type=content_type)


def write_blob(logical_path: str,
                data: bytes,
                asof_date: Optional[date] = None,
                content_type: str = "application/octet-stream") -> bool:
    """Write raw bytes to GCS."""
    if not _GCS_AVAILABLE or not _get_gcs_client():
        logger.warning(f"GCS unavailable — write_blob({logical_path}) skipped")
        return False
    gcs_obj = _gcs_path_for(logical_path, asof_date)
    return _gcs_upload_bytes(gcs_obj, data, content_type=content_type)


def write_local_file(logical_path: str,
                      source_path: str,
                      asof_date: Optional[date] = None) -> bool:
    """Upload a file from local disk to GCS at the logical path."""
    if not _GCS_AVAILABLE or not _get_gcs_client():
        logger.warning(f"GCS unavailable — write_local_file({logical_path}) skipped")
        return False
    p = Path(source_path)
    if not p.exists():
        logger.error(f"Source file not found: {source_path}")
        return False
    gcs_obj = _gcs_path_for(logical_path, asof_date)
    return _gcs_upload(gcs_obj, p)


# ============================================================
# DIAGNOSTIC
# ============================================================
def health_check() -> dict:
    out = {
        "gcs_sdk_available": _GCS_AVAILABLE,
        "gcs_client_ok":     False,
        "bucket":            GCS_BUCKET,
        "bucket_exists":     False,
        "local_fallback":    LOCAL_FALLBACK,
        "cache_dir":         str(CACHE_DIR),
        "fallback_files_present": {},
    }
    if _GCS_AVAILABLE:
        c = _get_gcs_client()
        if c is not None:
            out["gcs_client_ok"] = True
            try:
                out["bucket_exists"] = c.bucket(GCS_BUCKET).exists()
            except Exception as e:
                out["bucket_check_error"] = str(e)
    for logical, local in LOCAL_FALLBACK_MAP.items():
        out["fallback_files_present"][logical] = Path(local).exists()
    return out


if __name__ == "__main__":
    import pprint
    print("=== RCG Storage Layer Health Check ===")
    pprint.pprint(health_check())
