"""
Real-Time 3-State Macro Regime Data Builder for Full k in [1..50]
===================================================================
Outputs real OHLCV daily candle bars and 7-state micro regime sequences
for all 32 Master Sector Indices so client-side dashboard can support
EVERY integer smoothing factor k from 1 to 50 dynamically.
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
DATA_OUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_OUT_DIR, exist_ok=True)

JSON_OUT_FILE = os.path.join(DATA_OUT_DIR, "regime_analysis_data.json")
JS_OUT_FILE   = os.path.join(SCRIPT_DIR, "regime_dashboard_data.js")

def run_study():
    print("="*80)
    print("BUILDING COMPLETE 32-SECTOR OHLCV & MICRO REGIME DATASET (k = 1..50)")
    print("="*80)

    csv_files = glob.glob(os.path.join(INDICES_DIR, "*.csv"))
    print(f"Processing {len(csv_files)} master sector index daily CSV files...")

    all_sector_details = {}
    sector_summaries = []

    for fpath in csv_files:
        sec_name = os.path.basename(fpath).replace("_daily.csv", "").replace(".csv", "").strip().upper()
        
        try:
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if df.empty or len(df) < 10 or "Close" not in df.columns:
                continue
                
            df.sort_index(inplace=True)
            
            # Ensure required OHLCV columns
            for c in ["Open", "High", "Low", "Close"]:
                if c not in df.columns:
                    df[c] = df["Close"]
            if "Volume" not in df.columns:
                df["Volume"] = 0

            # Compute base 7-state micro regime and raw 3-state macro
            res_df = compute_3state_macro(df, close_col="Close", smoothing_window=1)
            
            bars = []
            sub_df = res_df.tail(1500)
            for dt, row in sub_df.iterrows():
                dt_str = dt.strftime("%Y-%m-%d")
                bars.append({
                    "t": dt_str,
                    "o": round(float(row["Open"]), 2),
                    "h": round(float(row["High"]), 2),
                    "l": round(float(row["Low"]), 2),
                    "c": round(float(row["Close"]), 2),
                    "v": int(row["Volume"]),
                    "m": int(row["state"])  # raw 3-state macro (0, 1, 2)
                })
                
            cur_val = bars[-1]["c"]
            tot_ret = round(((cur_val - 100.0) / 100.0) * 100.0, 2)
            ret_str = f"{'+' if tot_ret >= 0 else ''}{tot_ret:.2f}%"

            all_sector_details[sec_name] = {
                "sector": sec_name,
                "current_val": cur_val,
                "total_return_pct": ret_str,
                "bars": bars
            }

            sector_summaries.append({
                "sector": sec_name,
                "current_val": cur_val,
                "total_return_pct": ret_str
            })
            
            print(f"  [OK] {sec_name:30s} | Close: {cur_val:7.2f} | Return: {ret_str} | Bars: {len(bars)}")

        except Exception as e:
            print(f"  [ERROR] {sec_name}: {e}")

    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S IST"),
        "max_k": 50,
        "sector_summaries": sector_summaries,
        "sector_details": all_sector_details
    }

    with open(JSON_OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    with open(JS_OUT_FILE, "w", encoding="utf-8") as f:
        f.write("window.REGIME_ANALYSIS_DATA = ")
        json.dump(payload, f)
        f.write(";\n")

    size_mb = round(os.path.getsize(JS_OUT_FILE) / (1024 * 1024), 2)
    print("\n" + "="*80)
    print(f"SUCCESS: Generated regime_dashboard_data.js ({size_mb} MB) for {len(sector_summaries)} sectors!")
    print("="*80)

if __name__ == "__main__":
    run_study()
