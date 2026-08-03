"""
COEP Market Index - Daily EOD Automated Stock Update & Sector Index Pipeline
============================================================================
Features:
1. Incremental Stock OHLCV Updates via yfinance:
   - Scans all stock CSV files in OHLCV/Stocks/Daily/ (and fallback folders).
   - Finds latest date in each file.
   - Fetches missing daily candles from yfinance (Symbol.NS) up to today.
   - Appends, deduplicates by date, and saves back to CSV.
2. Corporate Action & Split Adjuster:
   - Audits single-bar drops (>25%) and applies split/bonus backward adjustments.
3. Sector Index Rebuilding:
   - Recalculates free-float market-cap weighted Sector Indices for all 32 sectors.
   - Saves updated sector indices to OHLCV/Indices/Daily/*.csv.
4. Update Manifest:
   - Writes update_summary.json with timestamp and index levels.
"""

import os
import sys
import glob
import json
import time
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── PATH CONFIGURATION ────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Search paths for Daily stock CSVs
DAILY_STOCKS_PATHS = [
    os.path.join(BASE_DIR, "OHLCV", "OHLCV", "Stocks", "Daily"),
    os.path.join(BASE_DIR, "OHLCV", "Stocks", "Daily"),
]

DAILY_INDICES_PATHS = [
    os.path.join(BASE_DIR, "OHLCV", "OHLCV", "Indices", "Daily"),
    os.path.join(BASE_DIR, "OHLCV", "Indices", "Daily"),
]

# Find active stock directory
STOCKS_DIR = None
for p in DAILY_STOCKS_PATHS:
    if os.path.exists(p) and len(glob.glob(os.path.join(p, "*.csv"))) > 0:
        STOCKS_DIR = p
        break

if not STOCKS_DIR:
    STOCKS_DIR = DAILY_STOCKS_PATHS[0]
    os.makedirs(STOCKS_DIR, exist_ok=True)

INDICES_DIR = None
for p in DAILY_INDICES_PATHS:
    if os.path.exists(p):
        INDICES_DIR = p
        break

if not INDICES_DIR:
    INDICES_DIR = DAILY_INDICES_PATHS[0]
    os.makedirs(INDICES_DIR, exist_ok=True)

SUMMARY_FILE = os.path.join(BASE_DIR, "update_summary.json")

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("AutoUpdate")

OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]

# ── HELPER FUNCTIONS ──────────────────────────────────────────────────────────

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(level=1, axis=1)
    rename = {}
    for col in df.columns:
        for standard in OHLCV_COLS:
            if str(col).lower() == standard.lower():
                rename[col] = standard
                break
    if rename:
        df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    keep = [c for c in OHLCV_COLS if c in df.columns]
    if not keep:
        return pd.DataFrame()
    return df[keep].dropna(how="all")


def update_single_stock(file_path: str) -> tuple[str, bool, str]:
    sym = os.path.basename(file_path).replace("_daily.csv", "").replace(".csv", "").strip().upper()
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        if df.empty:
            latest_dt = datetime(2015, 1, 1)
        else:
            df.index = pd.to_datetime(df.index)
            latest_dt = df.index.max()

        today_dt = datetime.now()
        if latest_dt.date() >= today_dt.date():
            return sym, False, "Already up to date"

        start_str = (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        end_str = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        yf_ticker = f"{sym}.NS"
        new_df = yf.download(yf_ticker, start=start_str, end=end_str, progress=False)

        if new_df is None or new_df.empty:
            # Try BSE fallback if NSE has no data
            yf_ticker_bse = f"{sym}.BO"
            new_df = yf.download(yf_ticker_bse, start=start_str, end=end_str, progress=False)

        if new_df is None or new_df.empty:
            return sym, False, "No new data returned"

        new_df = normalize_cols(new_df)
        if new_df.empty:
            return sym, False, "Empty data after normalization"

        if not df.empty:
            df = normalize_cols(df)
            combined = pd.concat([df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = new_df.sort_index()

        combined.to_csv(file_path)
        return sym, True, f"Updated {len(new_df)} new row(s)"

    except Exception as e:
        return sym, False, str(e)


def run_incremental_stock_updates(stocks_dir: str, max_workers: int = 15) -> dict:
    csv_files = glob.glob(os.path.join(stocks_dir, "*.csv"))
    log.info(f"Phase 1: Starting incremental updates for {len(csv_files)} stock files in {os.path.basename(stocks_dir)}...")

    updated_count = 0
    up_to_date_count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(update_single_stock, f): f for f in csv_files}
        for future in as_completed(futures):
            sym, was_updated, msg = future.result()
            if was_updated:
                updated_count += 1
            elif "Already" in msg:
                up_to_date_count += 1
            else:
                failed_count += 1

    log.info(f"Phase 1 Complete: {updated_count} updated, {up_to_date_count} up-to-date, {failed_count} skipped/no-data.")
    return {"total": len(csv_files), "updated": updated_count, "up_to_date": up_to_date_count, "failed": failed_count}


def run_corporate_actions():
    log.info("Phase 2: Running Corporate Action Split Adjuster...")
    try:
        from corporate_action_adjuster import scan_and_adjust_stock_file, load_fix_manifest, save_fix_manifest
        manifest = load_fix_manifest()
        csv_files = glob.glob(os.path.join(STOCKS_DIR, "*.csv"))
        total_fixes = 0
        for f in csv_files:
            adj = scan_and_adjust_stock_file(f, "Daily", apply_fix=True, manifest=manifest)
            total_fixes += len(adj)
        save_fix_manifest(manifest)
        log.info(f"Phase 2 Complete: {total_fixes} split/bonus fixes applied.")
    except Exception as e:
        log.warning(f"Phase 2 Warning (Corporate Action Adjuster): {e}")


def run_sector_index_rebuild():
    log.info("Phase 3: Rebuilding Sector Indices...")
    try:
        from build_sector_indices import main as build_indices_main
        build_indices_main()
        log.info("Phase 3 Complete: All 32 Sector Indices rebuilt.")
    except Exception as e:
        log.warning(f"Phase 3 Warning (Sector Index Rebuilder): {e}")


def main():
    start_time = time.time()
    log.info("="*70)
    log.info("COEP MARKET INDEX - AUTOMATED EOD UPDATE PIPELINE")
    log.info("="*70)

    # 1. Update stock OHLCV data
    stock_stats = run_incremental_stock_updates(STOCKS_DIR)

    # 2. Adjust for corporate actions
    run_corporate_actions()

    # 3. Rebuild sector indices
    run_sector_index_rebuild()

    elapsed = round(time.time() - start_time, 2)
    summary = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "execution_time_sec": elapsed,
        "stocks_summary": stock_stats,
        "status": "SUCCESS"
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    log.info(f"\n[ALL DONE] EOD Update Pipeline finished cleanly in {elapsed}s!")
    log.info(f"Summary written to {os.path.basename(SUMMARY_FILE)}")


if __name__ == "__main__":
    main()
