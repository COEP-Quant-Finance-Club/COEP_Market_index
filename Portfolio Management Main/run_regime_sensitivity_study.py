"""
Real-Time 3-State Macro Regime Sensitivity Study & Payload Builder
==================================================================
Runs 3-State Macro Regime calculation with Rolling Median Hysteresis Filtering
across all 32 Master Sector Indices using REAL OHLCV candle data.
Evaluates candle smoothing window parameters k in [1, 3, 5, 7, 9, 10, 14, 21].
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

SMOOTHING_WINDOWS = [1, 3, 5, 7, 9, 10, 14, 21]

def run_study():
    print("="*80)
    print("BUILDING REAL OHLCV 3-STATE MACRO REGIME DATASET")
    print("="*80)

    csv_files = glob.glob(os.path.join(INDICES_DIR, "*.csv"))
    print(f"Processing {len(csv_files)} master sector index daily CSV files...")

    all_sector_results = {}
    sector_summaries = []

    for fpath in csv_files:
        sec_name = os.path.basename(fpath).replace("_daily.csv", "").replace(".csv", "").strip().upper()
        
        try:
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if df.empty or len(df) < 10 or "Close" not in df.columns:
                continue
                
            df.sort_index(inplace=True)
            sec_k_results = {}
            
            # Ensure all required OHLCV columns exist
            for c in ["Open", "High", "Low", "Close"]:
                if c not in df.columns:
                    df[c] = df["Close"]
            if "Volume" not in df.columns:
                df["Volume"] = 0

            for k in SMOOTHING_WINDOWS:
                res_df = compute_3state_macro(df, close_col="Close", smoothing_window=k)
                state_seq = res_df["state"].values
                
                # Format real OHLCV bars (cap at recent 1250 bars for optimal JS payload size)
                bars = []
                sub_df = res_df.tail(1250)
                for dt, row in sub_df.iterrows():
                    dt_str = dt.strftime("%Y-%m-%d")
                    bars.append({
                        "t": dt_str,
                        "o": round(float(row["Open"]), 2),
                        "h": round(float(row["High"]), 2),
                        "l": round(float(row["Low"]), 2),
                        "c": round(float(row["Close"]), 2),
                        "v": int(row["Volume"]),
                        "s": int(row["state"])
                    })
                    
                cur_state = int(state_seq[-1])
                sec_k_results[str(k)] = {
                    "k": k,
                    "current_state": cur_state,
                    "bars": bars
                }

            # Baseline k=9 for primary summary
            base_res = sec_k_results["9"]
            all_sector_results[sec_name] = sec_k_results
            
            cur_val = base_res["bars"][-1]["c"]
            tot_ret = round(((cur_val - 100.0) / 100.0) * 100.0, 2)
            ret_str = f"{'+' if tot_ret >= 0 else ''}{tot_ret:.2f}%"

            sector_summaries.append({
                "sector": sec_name,
                "current_state": base_res["current_state"],
                "current_val": cur_val,
                "total_return_pct": ret_str
            })
            
            print(f"  [OK] {sec_name:30s} | State: {base_res['current_state']} | Close: {cur_val:7.2f} | Return: {ret_str}")

        except Exception as e:
            print(f"  [ERROR] {sec_name}: {e}")

    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S IST"),
        "smoothing_windows": SMOOTHING_WINDOWS,
        "sector_summaries": sector_summaries,
        "sector_details": all_sector_results
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
