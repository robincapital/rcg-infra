## ════════════════════════════════════════════════════════════════════
##  RCG SCREENER — Jupyter Run Cell (v2)
##  Replaces Cell 4 of Screener_w_Macro_Overlay.ipynb
##  v2 adds: USE_NEW_PRICE_TARGETS, PT_APPLY_ENVELOPE, PT_R2_FLOOR/FULL,
##           BLOOMBERG_INTRADAY_MAX
## ════════════════════════════════════════════════════════════════════
import sys, re

# Clear cached imports (incl. price_targets so config changes take effect)
for mod in list(sys.modules.keys()):
    if 'screener' in mod or mod == 'price_targets':
        del sys.modules[mod]

sys.path.insert(0, '/home/nixos/Prod/V1/src')

# ══════════════════════════════════════════════════════
#  MANUAL CONTROLS — CHANGE THESE
# ══════════════════════════════════════════════════════
SENTIMENT_OVERRIDE       = "BULLISH"   # "BULLISH" | "BEARISH" | "NEUTRAL" | None
OVERRIDE_FACTOR          = 0.5         # 0.0 = no effect | 1.0 = full force

EXCL_ADRS                = True
EXCL_BIOTECH             = True
DEBT_COVERAGE            = True
SECTOR_CAP               = True
MAX_PER_SECTOR           = 8

# Cap presets:  "all" | "small" | "mid" | "large" | "custom"
CAP_PRESET               = "custom"
CAP_MIN                  = 350e6
CAP_MAX                  = 300e10

FED_TARGET_RATE          = 0.03625
FED_NEUTRAL_RATE         = 0.0300

# ── Price Target Engine v2 ────────────────────────────
USE_NEW_PRICE_TARGETS    = True        # master switch — flip to False to revert
PT_APPLY_ENVELOPE        = True        # Gate B: clip extreme model PTs to analyst band
PT_R2_FLOOR              = 0.20        # Gate A: drop models with R² < floor
PT_R2_FULL               = 0.40        # full conviction at and above this R²

# ── Bloomberg intraday tier 3 (Phase 5 prep) ──────────
BLOOMBERG_INTRADAY_MAX   = 50          # cap on intraday tickers (BBG license dependent)
# ══════════════════════════════════════════════════════

script = open('/home/nixos/Prod/V1/src/run_screener.py').read()

for pattern, replacement in [
    (r'SENTIMENT_OVERRIDE\s*=\s*"[^"]*"',  f'SENTIMENT_OVERRIDE = "{SENTIMENT_OVERRIDE}"'),
    (r'OVERRIDE_FACTOR\s*=\s*[\d.]+',       f'OVERRIDE_FACTOR    = {OVERRIDE_FACTOR}'),
    (r'EXCL_ADRS\s*=\s*(True|False)',       f'EXCL_ADRS          = {EXCL_ADRS}'),
    (r'EXCL_BIOTECH\s*=\s*(True|False)',    f'EXCL_BIOTECH       = {EXCL_BIOTECH}'),
    (r'DEBT_COVERAGE\s*=\s*(True|False)',   f'DEBT_COVERAGE      = {DEBT_COVERAGE}'),
    (r'SECTOR_CAP\s*=\s*(True|False)',      f'SECTOR_CAP         = {SECTOR_CAP}'),
    (r'MAX_PER_SECTOR\s*=\s*\d+',           f'MAX_PER_SECTOR     = {MAX_PER_SECTOR}'),
    (r'CAP_PRESET\s*=\s*"[^"]*"',           f'CAP_PRESET         = "{CAP_PRESET}"'),
    (r'CAP_MIN\s*=\s*[\d.e+]+',             f'CAP_MIN            = {CAP_MIN}'),
    (r'CAP_MAX\s*=\s*[\d.e+]+',             f'CAP_MAX            = {CAP_MAX}'),
    (r'FED_TARGET_RATE\s*=\s*[\d.]+',       f'FED_TARGET_RATE    = {FED_TARGET_RATE}'),
    (r'FED_NEUTRAL_RATE\s*=\s*[\d.]+',      f'FED_NEUTRAL_RATE   = {FED_NEUTRAL_RATE}'),
    # v2 — price target engine
    (r'USE_NEW_PRICE_TARGETS\s*=\s*(True|False)',
       f'USE_NEW_PRICE_TARGETS  = {USE_NEW_PRICE_TARGETS}'),
    (r'PT_APPLY_ENVELOPE\s*=\s*(True|False)',
       f'PT_APPLY_ENVELOPE      = {PT_APPLY_ENVELOPE}'),
    (r'PT_R2_FLOOR\s*=\s*[\d.]+',           f'PT_R2_FLOOR            = {PT_R2_FLOOR}'),
    (r'PT_R2_FULL\s*=\s*[\d.]+',            f'PT_R2_FULL             = {PT_R2_FULL}'),
    (r'BLOOMBERG_INTRADAY_MAX\s*=\s*\d+',
       f'BLOOMBERG_INTRADAY_MAX = {BLOOMBERG_INTRADAY_MAX}'),
]:
    script = re.sub(pattern, replacement, script, count=1)

exec(script)
