"""
Real-Time 3-State Macro Regime & Sector Constituents Data Builder
===================================================================
1. Master Sector Index OHLCV & 3-State Macro Regimes (k = 1..50)
2. All 32 Sector Constituent Stock Daily Prices with Zero-Volume Placeholder Bar Purging
"""

import os
import sys
import glob
import json
import time
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from hmm_regime_engine import compute_3state_macro

BASE_PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
INDICES_DIR = os.path.join(BASE_PROJECT_DIR, "OHLCV", "Indices", "Daily")
IND_DIR = os.path.join(BASE_PROJECT_DIR, "Industries")
STOCKS_DIR = os.path.join(BASE_PROJECT_DIR, "OHLCV", "Stocks")

DATA_OUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_OUT_DIR, exist_ok=True)

JSON_OUT_FILE = os.path.join(DATA_OUT_DIR, "regime_analysis_data.json")
JS_OUT_FILE   = os.path.join(SCRIPT_DIR, "regime_dashboard_data.js")

def run_study():
    print("="*80)
    print("BUILDING 32-SECTOR OHLCV, MICRO REGIME & CLEANED CONSTITUENT STOCK DATASET")
    print("="*80)

    # Index Stock CSV files
    stock_files = glob.glob(os.path.join(STOCKS_DIR, "**", "*.csv"), recursive=True)
    stock_file_map = {}
    for f in stock_files:
        fname = os.path.basename(f).replace(".csv", "").replace("_daily", "").strip().upper()
        stock_file_map[fname] = f
        clean_sym = fname.replace(".NS", "")
        stock_file_map[clean_sym] = f

    print(f"Indexed {len(stock_files)} stock files.")

    csv_files = glob.glob(os.path.join(INDICES_DIR, "*.csv"))
    print(f"Processing {len(csv_files)} master sector index daily CSV files...")

    all_sector_details = {}
    sector_summaries = []

    for fpath in csv_files:
        sec_name = os.path.basename(fpath).replace("_daily.csv", "").replace(".csv", "").strip().upper()
        
        try:
            df = pd.read_csv(fpath)
            if df.empty or len(df) < 10:
                continue
                
            date_cols = [c for c in df.columns if 'date' in c.lower() or 'time' in c.lower()]
            close_cols = [c for c in df.columns if 'close' in c.lower()]
            if not date_cols or not close_cols:
                continue
                
            dcol = date_cols[0]
            ccol = close_cols[0]
            
            df[dcol] = pd.to_datetime(df[dcol])
            df.sort_values(by=dcol, inplace=True)
            df.set_index(dcol, inplace=True)
            
            for c in ["Open", "High", "Low", "Close"]:
                if c not in df.columns:
                    df[c] = df[ccol]
            if "Volume" not in df.columns:
                df["Volume"] = 0

            res_df = compute_3state_macro(df, close_col=ccol, smoothing_window=1)
            
            bars = []
            sub_df = res_df.tail(1250)
            for dt, row in sub_df.iterrows():
                dt_str = dt.strftime("%Y-%m-%d")
                bars.append({
                    "t": dt_str,
                    "o": round(float(row["Open"]), 2),
                    "h": round(float(row["High"]), 2),
                    "l": round(float(row["Low"]), 2),
                    "c": round(float(row[ccol]), 2),
                    "v": int(row["Volume"]),
                    "m": int(row["state"])
                })
                
            cur_val = bars[-1]["c"]
            tot_ret = round(((cur_val - 100.0) / 100.0) * 100.0, 2)
            ret_str = f"{'+' if tot_ret >= 0 else ''}{tot_ret:.2f}%"

            # Load Constituents for this Sector
            ind_csv = os.path.join(IND_DIR, f"{sec_name.lower()}_enhanced.csv")
            stock_constituents = []
            
            if os.path.exists(ind_csv):
                try:
                    df_ind = pd.read_csv(ind_csv)
                    sym_col = [c for c in df_ind.columns if 'symbol' in c.lower() or 'ticker' in c.lower()][0]
                    name_col = [c for c in df_ind.columns if 'name' in c.lower()][0]
                    
                    for _, row in df_ind.iterrows():
                        raw_sym = str(row[sym_col]).strip().upper()
                        clean_sym = raw_sym.replace(".NS", "")
                        name = str(row[name_col]).strip()
                        
                        stk_file = stock_file_map.get(raw_sym) or stock_file_map.get(clean_sym) or stock_file_map.get(clean_sym + "_DAILY")
                        p_dict = {}
                        if stk_file:
                            try:
                                stk_df = pd.read_csv(stk_file)
                                s_date_cols = [c for c in stk_df.columns if 'date' in c.lower() or 'time' in c.lower()]
                                s_close_cols = [c for c in stk_df.columns if 'close' in c.lower()]
                                s_vol_cols = [c for c in stk_df.columns if 'vol' in c.lower()]
                                
                                if s_date_cols and s_close_cols:
                                    sdcol = s_date_cols[0]
                                    sccol = s_close_cols[0]
                                    
                                    # Purge zero-volume flat placeholder bars
                                    if s_vol_cols:
                                        svcol = s_vol_cols[0]
                                        s_open_cols = [c for c in stk_df.columns if 'open' in c.lower()]
                                        if s_open_cols:
                                            socol = s_open_cols[0]
                                            stk_df = stk_df[~((stk_df[svcol] == 0) & (stk_df[socol] == stk_df[sccol]))].copy()

                                    # Auto-adjust unadjusted corporate action demergers (>35% drop)
                                    closes_val = stk_df[sccol].values
                                    n_stk = len(closes_val)
                                    for idx_stk in range(1, n_stk):
                                        p_prev_stk = closes_val[idx_stk - 1]
                                        p_curr_stk = closes_val[idx_stk]
                                        if p_prev_stk > 0 and p_curr_stk > 0:
                                            if (p_curr_stk - p_prev_stk) / p_prev_stk < -0.35:
                                                fact_stk = p_curr_stk / p_prev_stk
                                                stk_df.loc[:idx_stk-1, sccol] = stk_df.loc[:idx_stk-1, sccol] * fact_stk

                                    stk_df[sdcol] = pd.to_datetime(stk_df[sdcol]).dt.strftime('%Y-%m-%d')
                                    sub_stk = stk_df.tail(1250)
                                    p_dict = dict(zip(sub_stk[sdcol], sub_stk[sccol].round(2)))
                            except Exception:
                                pass
                                
                        stock_constituents.append({
                            "symbol": clean_sym,
                            "name": name,
                            "prices": p_dict
                        })
                except Exception as e_ind:
                    print(f"    [WARN] Could not parse industry CSV for {sec_name}: {e_ind}")

            all_sector_details[sec_name] = {
                "sector": sec_name,
                "current_val": cur_val,
                "total_return_pct": ret_str,
                "bars": bars,
                "constituents": stock_constituents
            }

            sector_summaries.append({
                "sector": sec_name,
                "current_val": cur_val,
                "total_return_pct": ret_str,
                "stock_count": len(stock_constituents)
            })
            
            print(f"  [OK] {sec_name:30s} | Close: {cur_val:7.2f} | Return: {ret_str} | Stocks: {len(stock_constituents)}")

        except Exception as e:
            print(f"  [ERROR] {sec_name}: {e}")

    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S IST"),
        "max_k": 50,
        "sector_summaries": sector_summaries,
        "sector_details": all_sector_details
    }

    print("\nWriting payload files...")
    with open(JSON_OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    with open(JS_OUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.REGIME_ANALYSIS_DATA = ")
        json.dump(payload, f)
        f.write(";\n")

    size_mb = round(os.path.getsize(JS_OUT_FILE) / (1024 * 1024), 2)
    print("="*80)
    print(f"SUCCESS: Generated regime_dashboard_data.js ({size_mb} MB) for {len(sector_summaries)} sectors!")
    print("="*80)

if __name__ == "__main__":
    run_study()
