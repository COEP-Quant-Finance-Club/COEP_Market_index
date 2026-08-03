"""
COEP Market Index Engine (Master Single-File Solution)
======================================================
Architecture:
1. Pure YFinance Incremental Downloader (Zero Angel One API dependencies).
2. Automated Split & Bonus Adjuster (Backward adjustment with idempotency).
3. Free-Float Market-Cap Sector Index Calculation (Zero Forward Bias):
   - Fixed Share Count Q_i = (Base Market Cap_0 * 10^7) / Base Price_0.
   - Daily Market Cap M_{i,t} = Price_{i,t} * Q_i.
   - Sector Return R_{Sector,t} = sum(w_{i,t-1} * R_{i,t}) for valid trading pairs.
   - Sector Index Level I_t = I_{t-1} * (1 + R_{Sector,t}), Base = 100.0.
4. Daily Sector Weightage Overwrite:
   - Overwrites todays_sector_weights.json with today's stock weightages per sector.
   - Deletes past weights so no stale data accumulates.
"""

import os
import sys
import glob
import json
import time
import logging
import re
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── PATHS & GLOBALS ───────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_DIR = os.path.join(BASE_DIR, "OHLCV", "Stocks", "Daily")
INDICES_DIR = os.path.join(BASE_DIR, "OHLCV", "Indices", "Daily")
JSON_DIR = os.path.join(BASE_DIR, "json")
os.makedirs(JSON_DIR, exist_ok=True)

BASE_MCAP_FILE = os.path.join(JSON_DIR, "base_market_caps.json")
WEIGHTS_FILE = os.path.join(JSON_DIR, "todays_sector_weights.json")
MANIFEST_FILE = os.path.join(JSON_DIR, "fixes_applied.json")
SUMMARY_FILE = os.path.join(JSON_DIR, "update_summary.json")

os.makedirs(STOCKS_DIR, exist_ok=True)
os.makedirs(INDICES_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
log = logging.getLogger("COEPMarketIndex")

OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]
KNOWN_SPLIT_RATIOS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0]

# ── STEP 1: YFINANCE INCREMENTAL DOWNLOADER ───────────────────────────────────

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
            yf_ticker_bse = f"{sym}.BO"
            new_df = yf.download(yf_ticker_bse, start=start_str, end=end_str, progress=False)

        if new_df is None or new_df.empty:
            return sym, False, "No new data"

        new_df = normalize_cols(new_df)
        if new_df.empty:
            return sym, False, "Empty after norm"

        if not df.empty:
            df = normalize_cols(df)
            combined = pd.concat([df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = new_df.sort_index()

        combined.to_csv(file_path)
        return sym, True, f"Updated {len(new_df)} rows"

    except Exception as e:
        return sym, False, str(e)


def run_yfinance_downloader(max_workers: int = 15) -> dict:
    csv_files = glob.glob(os.path.join(STOCKS_DIR, "*.csv"))
    log.info(f"[1/4] Running YFinance Incremental Downloader for {len(csv_files)} stocks...")
    updated, up_to_date, failed = 0, 0, 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(update_single_stock, f): f for f in csv_files}
        for future in as_completed(futures):
            sym, was_updated, msg = future.result()
            if was_updated:
                updated += 1
            elif "Already" in msg:
                up_to_date += 1
            else:
                failed += 1

    log.info(f"[1/4 Complete] {updated} updated, {up_to_date} up-to-date, {failed} failed/no-data.")
    return {"total": len(csv_files), "updated": updated, "up_to_date": up_to_date, "failed": failed}

# ── STEP 2: SPLIT & BONUS ADJUSTER ────────────────────────────────────────────

def load_manifest() -> dict:
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_manifest(manifest: dict):
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def run_split_adjuster():
    log.info("[2/4] Auditing stock files for Corporate Action Splits & Bonuses...")
    manifest = load_manifest()
    csv_files = glob.glob(os.path.join(STOCKS_DIR, "*.csv"))
    fixes_count = 0

    for file_path in csv_files:
        try:
            df = pd.read_csv(file_path, index_col=0, parse_dates=True)
            if len(df) < 5 or "Close" not in df.columns or "Open" not in df.columns:
                continue

            df.sort_index(inplace=True)
            mkey = os.path.normpath(file_path)
            already_fixed = set(manifest.get(mkey, []))

            df["ret"] = df["Close"].pct_change()
            candidates = df[df["ret"] < -0.25]
            if candidates.empty:
                continue

            modified = False
            for dt, row in candidates.iterrows():
                dt_str = dt.strftime("%Y-%m-%d")
                if dt_str in already_fixed:
                    continue

                loc = df.index.get_loc(dt)
                prev_idx = loc - 1 if isinstance(loc, int) else loc.start - 1
                if prev_idx < 0:
                    continue

                prev_close = df["Close"].iloc[prev_idx]
                cur_open   = row["Open"]

                if prev_close <= 0 or cur_open <= 0:
                    continue

                ratio = prev_close / cur_open
                if abs(ratio - 1.0) < 0.15:
                    continue

                matched_ratio = None
                for target_ratio in KNOWN_SPLIT_RATIOS:
                    if abs(ratio - target_ratio) / target_ratio < 0.15:
                        matched_ratio = target_ratio
                        break

                if matched_ratio is not None:
                    mask = df.index < dt
                    df.loc[mask, ["Open", "High", "Low", "Close"]] /= matched_ratio
                    if "Volume" in df.columns:
                        df.loc[mask, "Volume"] *= matched_ratio
                    modified = True
                    fixes_count += 1
                    if mkey not in manifest:
                        manifest[mkey] = []
                    manifest[mkey].append(dt_str)

            if modified:
                df.drop(columns=["ret"]).to_csv(file_path)

        except Exception:
            pass

    save_manifest(manifest)
    log.info(f"[2/4 Complete] {fixes_count} new split/bonus adjustments applied.")

# ── STEP 3: ZERO-FORWARD-BIAS SECTOR INDEX ENGINE ─────────────────────────────

def load_base_market_caps() -> dict:
    if not os.path.exists(BASE_MCAP_FILE):
        log.error(f"Base Market Cap file missing: {BASE_MCAP_FILE}. Run mcap_seed_scraper.py first!")
        return {}
    with open(BASE_MCAP_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def calculate_sector_indices() -> tuple[dict, dict]:
    log.info("[3/4] Rebuilding Free-Float Market-Cap Sector Indices (Zero Forward Bias)...")
    base_caps = load_base_market_caps()
    if not base_caps:
        return {}, {}

    # Group stocks by sector
    sectors_map = {}
    for sym, item in base_caps.items():
        sec = item.get("sector", "MISCELLANEOUS")
        if sec not in sectors_map:
            sectors_map[sec] = []
        sectors_map[sec].append(item)

    summary = {}
    todays_sector_weights = {}

    for sec_name, constituents in sectors_map.items():
        # Load stock price histories for sector constituents
        stock_dfs = {}
        for c in constituents:
            sym = c["symbol"]
            mcap_cr = c.get("base_market_cap_cr", 100.0)
            csv_path = os.path.join(STOCKS_DIR, f"{sym}_daily.csv")
            if os.path.exists(csv_path):
                try:
                    df_s = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                    df_s = normalize_cols(df_s)
                    if not df_s.empty and len(df_s) >= 1:
                        df_s.sort_index(inplace=True)
                        base_price = float(df_s["Close"].iloc[0])
                        if base_price > 0:
                            # Split-Adjusted Outstanding Shares Derivation (Q_i):
                            # Since run_split_adjuster() runs BEFORE index calculation, base_price (P_0) is split-adjusted.
                            # Q_i = (Base Market Cap_0 in Cr * 10^7) / P_0(split-adjusted).
                            # If a 2:1 split occurred, P_0 halved, so Q_i automatically doubled (Q_i * 2).
                            shares = (mcap_cr * 1e7) / base_price
                            stock_dfs[sym] = {"df": df_s, "shares": shares}
                except Exception:
                    pass

        if not stock_dfs:
            continue

        # Combine Close, Open, High, Low, Volume into aligned DataFrames
        closes_dict = {sym: info["df"]["Close"] for sym, info in stock_dfs.items()}
        opens_dict  = {sym: info["df"]["Open"]  for sym, info in stock_dfs.items()}
        highs_dict  = {sym: info["df"]["High"]  for sym, info in stock_dfs.items()}
        lows_dict   = {sym: info["df"]["Low"]   for sym, info in stock_dfs.items()}
        vols_dict   = {sym: info["df"]["Volume"] for sym, info in stock_dfs.items()}

        df_close = pd.DataFrame(closes_dict).dropna(how="all")
        df_open  = pd.DataFrame(opens_dict).reindex(df_close.index)
        df_high  = pd.DataFrame(highs_dict).reindex(df_close.index)
        df_low   = pd.DataFrame(lows_dict).reindex(df_close.index)
        df_vol   = pd.DataFrame(vols_dict).reindex(df_close.index).fillna(0)

        timestamps = df_close.index
        n_dt = len(df_close)
        symbols = df_close.columns.tolist()
        shares_arr = np.array([stock_dfs[sym]["shares"] for sym in symbols])

        close_vals = df_close.values
        open_vals  = df_open.values
        high_vals  = df_high.values
        low_vals   = df_low.values
        vol_vals   = df_vol.values

        idx_open  = np.zeros(n_dt)
        idx_high  = np.zeros(n_dt)
        idx_low   = np.zeros(n_dt)
        idx_close = np.zeros(n_dt)
        idx_vol   = np.zeros(n_dt)

        idx_open[0]  = 100.0
        idx_high[0]  = 100.0
        idx_low[0]   = 100.0
        idx_close[0] = 100.0
        idx_vol[0]   = float(np.sum(vol_vals[0]))

        # Calculate Index level time-series dynamically without forward bias
        for t in range(1, n_dt):
            # Valid trading pair: price must exist at both t-1 and t
            valid_pair = ~np.isnan(close_vals[t]) & ~np.isnan(close_vals[t-1])
            if not np.any(valid_pair):
                idx_open[t]  = idx_close[t-1]
                idx_high[t]  = idx_close[t-1]
                idx_low[t]   = idx_close[t-1]
                idx_close[t] = idx_close[t-1]
                idx_vol[t]   = 0.0
                continue

            prev_prices = close_vals[t-1, valid_pair]
            cur_prices  = close_vals[t, valid_pair]
            pairs_shares = shares_arr[valid_pair]

            # Daily Market Caps at t-1 for weighting
            mcap_prev = prev_prices * pairs_shares
            tot_mcap_prev = np.sum(mcap_prev)

            if tot_mcap_prev <= 0:
                weights = np.ones_like(prev_prices) / len(prev_prices)
            else:
                weights = mcap_prev / tot_mcap_prev

            ret_close = (cur_prices - prev_prices) / prev_prices
            sector_ret_close = np.sum(weights * ret_close)

            # High/Low intra-bar return estimates
            cur_highs = np.where(np.isnan(high_vals[t, valid_pair]), cur_prices, high_vals[t, valid_pair])
            cur_lows  = np.where(np.isnan(low_vals[t, valid_pair]),  cur_prices, low_vals[t, valid_pair])
            cur_opens = np.where(np.isnan(open_vals[t, valid_pair]), cur_prices, open_vals[t, valid_pair])

            ret_open = (cur_opens - prev_prices) / prev_prices
            ret_high = (cur_highs - prev_prices) / prev_prices
            ret_low  = (cur_lows  - prev_prices) / prev_prices

            sector_ret_open = np.sum(weights * ret_open)
            sector_ret_high = np.sum(weights * ret_high)
            sector_ret_low  = np.sum(weights * ret_low)

            c_prev = idx_close[t-1]
            c_val  = c_prev * (1.0 + sector_ret_close)
            o_val  = c_prev * (1.0 + sector_ret_open)
            h_val  = max(o_val, c_val, c_prev * (1.0 + sector_ret_high))
            l_val  = min(o_val, c_val, c_prev * (1.0 + sector_ret_low))

            idx_open[t]  = round(o_val, 2)
            idx_high[t]  = round(h_val, 2)
            idx_low[t]   = round(l_val, 2)
            idx_close[t] = round(c_val, 2)
            idx_vol[t]   = float(np.sum(vol_vals[t]))

        idx_df = pd.DataFrame({
            "Open": idx_open, "High": idx_high, "Low": idx_low, "Close": idx_close, "Volume": idx_vol
        }, index=timestamps)

        # Save sector index CSV
        out_path = os.path.join(INDICES_DIR, f"{sec_name.lower()}_daily.csv")
        idx_df.to_csv(out_path)

        # Calculate today's final constituent weights for export
        latest_prices = close_vals[-1]
        valid_latest = ~np.isnan(latest_prices) & (latest_prices > 0)
        latest_mcaps = latest_prices[valid_latest] * shares_arr[valid_latest]
        tot_latest_mcap = np.sum(latest_mcaps)

        weights_dict = {}
        if tot_latest_mcap > 0:
            valid_syms = np.array(symbols)[valid_latest]
            for sym_i, mcap_i in zip(valid_syms, latest_mcaps):
                w_pct = round(float(mcap_i / tot_latest_mcap) * 100.0, 4)
                weights_dict[sym_i] = w_pct

        todays_sector_weights[sec_name] = {
            "sector": sec_name,
            "constituents_count": len(weights_dict),
            "latest_index_value": float(idx_close[-1]),
            "weights_percentage": dict(sorted(weights_dict.items(), key=lambda x: x[1], reverse=True))
        }

        summary[sec_name] = {
            "constituents": len(symbols),
            "latest_index_val": float(idx_close[-1]),
            "total_return_pct": round(((idx_close[-1] - 100.0) / 100.0) * 100.0, 2)
        }

    # Save today's sector weights (overwriting past weights completely)
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(todays_sector_weights, f, indent=2)

    log.info(f"[3/4 Complete] Rebuilt {len(summary)} Sector Indices. Todays weightages written to {os.path.basename(WEIGHTS_FILE)}")
    return summary, todays_sector_weights

# ── MAIN PIPELINE EXECUTION ───────────────────────────────────────────────────

def main():
    start_time = time.time()
    log.info("="*70)
    log.info("COEP MARKET INDEX - UNIFIED MASTER PIPELINE (SINGLE ENGINE)")
    log.info("="*70)

    # 1. Download/Update stock candles via yfinance
    dl_stats = run_yfinance_downloader()

    # 2. Audit and fix splits/bonuses
    run_split_adjuster()

    # 3. Rebuild free-float market-cap sector indices & export today's weights
    sec_summary, sector_weights = calculate_sector_indices()

    elapsed = round(time.time() - start_time, 2)
    summary_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "execution_time_sec": elapsed,
        "downloader_stats": dl_stats,
        "sectors_built": len(sec_summary),
        "status": "SUCCESS"
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    log.info("="*70)
    log.info(f"[ALL DONE] Unified EOD Index Pipeline completed in {elapsed}s!")
    log.info("="*70)


if __name__ == "__main__":
    main()
