"""
Real-Time HMM Regime Sensitivity Study & Leading Sector Detector
==================================================================
Runs 3-State Out-of-Sample HMM analysis across all 32 Master Sector Indices.
Evaluates candle smoothing window parameters k in [1, 3, 5, 7, 10, 14, 21].
Identifies leading sector regime shifts and generates regime_dashboard_data.js.
"""

import os
import sys
import glob
import json
import time
import pandas as pd
import numpy as np

# Ensure Portfolio Management Main directory is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from hmm_regime_engine import run_hmm_regime_analysis

# Paths
BASE_PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
INDICES_DIR = os.path.join(BASE_PROJECT_DIR, "OHLCV", "Indices", "Daily")
DATA_OUT_DIR = os.path.join(SCRIPT_DIR, "data")
os.makedirs(DATA_OUT_DIR, exist_ok=True)

JSON_OUT_FILE = os.path.join(DATA_OUT_DIR, "regime_analysis_data.json")
JS_OUT_FILE   = os.path.join(SCRIPT_DIR, "regime_dashboard_data.js")

SMOOTHING_WINDOWS = [1, 3, 5, 7, 10, 14, 21]

def calculate_whipsaw_ratio(state_seq: np.ndarray) -> float:
    """Calculates the proportion of transient 1-day or 2-day state flips."""
    if len(state_seq) < 3:
        return 0.0
    flips = 0
    n = len(state_seq)
    for i in range(1, n - 1):
        if state_seq[i] != state_seq[i-1] and state_seq[i] != state_seq[i+1]:
            flips += 1
    return round(float(flips / n), 4)

def calculate_mean_persistence(state_seq: np.ndarray) -> float:
    """Calculates average consecutive days spent in a regime state."""
    if len(state_seq) == 0:
        return 0.0
    durations = []
    curr_len = 1
    for i in range(1, len(state_seq)):
        if state_seq[i] == state_seq[i-1]:
            curr_len += 1
        else:
            durations.append(curr_len)
            curr_len = 1
    durations.append(curr_len)
    return round(float(np.mean(durations)), 1)

def run_study():
    print("="*80)
    print("RUNNING 3-STATE HMM REGIME ANALYSIS & SMOOTHING SENSITIVITY STUDY")
    print("="*80)

    csv_files = glob.glob(os.path.join(INDICES_DIR, "*.csv"))
    print(f"Found {len(csv_files)} master sector index daily CSV files.")

    all_sector_results = {}
    sector_summaries = []
    smoothing_stats = {k: {"whipsaws": [], "persistence": []} for k in SMOOTHING_WINDOWS}

    for fpath in csv_files:
        sec_slug = os.path.basename(fpath).replace("_daily.csv", "").replace(".csv", "").strip().lower()
        sec_name = sec_slug.upper()
        
        try:
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if df.empty or len(df) < 50 or "Close" not in df.columns:
                continue
                
            df.sort_index(inplace=True)
            sec_k_results = {}
            
            for k in SMOOTHING_WINDOWS:
                res_df = run_hmm_regime_analysis(df, k=k)
                state_seq = res_df["state"].values
                
                whipsaw = calculate_whipsaw_ratio(state_seq)
                persist = calculate_mean_persistence(state_seq)
                
                smoothing_stats[k]["whipsaws"].append(whipsaw)
                smoothing_stats[k]["persistence"].append(persist)
                
                # Format bars for dashboard JSON (cap at recent 1250 bars for optimal payload size)
                bars = []
                sub_df = res_df.tail(1250)
                for dt, row in sub_df.iterrows():
                    dt_str = dt.strftime("%Y-%m-%d")
                    bars.append({
                        "t": dt_str,
                        "c": round(float(row["smooth_close"]), 2),
                        "s": int(row["state"]),
                        "bp": round(float(row["bull_prob"]), 2),
                        "np": round(float(row["neutral_prob"]), 2),
                        "rp": round(float(row["bear_prob"]), 2)
                    })
                    
                cur_state = int(state_seq[-1])
                cur_probs = [round(float(res_df["bull_prob"].iloc[-1]), 4), round(float(res_df["neutral_prob"].iloc[-1]), 4), round(float(res_df["bear_prob"].iloc[-1]), 4)]
                
                sec_k_results[str(k)] = {
                    "k": k,
                    "whipsaw_ratio": whipsaw,
                    "mean_persistence_days": persist,
                    "current_state": cur_state,
                    "current_probs": cur_probs,
                    "bars": bars
                }

            # Select default baseline k=5 for primary sector summary
            base_res = sec_k_results["5"]
            all_sector_results[sec_name] = sec_k_results
            
            # Check days spent in current Bullish state (for leading sector detection)
            latest_state = base_res["current_state"]
            bull_days = 0
            if latest_state == 0:
                # Count consecutive days in state 0 from end
                state_arr = np.array([b["s"] for b in base_res["bars"]])
                for st in reversed(state_arr):
                    if st == 0:
                        bull_days += 1
                    else:
                        break

            sector_summaries.append({
                "sector": sec_name,
                "current_state": latest_state,
                "current_val": base_res["bars"][-1]["c"],
                "bull_prob": base_res["current_probs"][0],
                "neutral_prob": base_res["current_probs"][1],
                "bear_prob": base_res["current_probs"][2],
                "days_in_bull_state": bull_days,
                "whipsaw_ratio_k5": base_res["whipsaw_ratio"],
                "persistence_days_k5": base_res["mean_persistence_days"]
            })
            
            print(f"  [OK] {sec_name:30s} | Current State: {latest_state} (Bull: {base_res['current_probs'][0]:.2f}) | Bull Days: {bull_days:3d}")

        except Exception as e:
            print(f"  [ERROR] {sec_name}: {e}")

    # Calculate overall smoothing window benchmark metrics
    smoothing_benchmark = {}
    for k in SMOOTHING_WINDOWS:
        smoothing_benchmark[str(k)] = {
            "k": k,
            "avg_whipsaw_ratio": round(float(np.mean(smoothing_stats[k]["whipsaws"])), 4),
            "avg_persistence_days": round(float(np.mean(smoothing_stats[k]["persistence"])), 1)
        }

    # Sort leading sectors by days in bullish state
    sector_summaries.sort(key=lambda x: (x["current_state"], -x["days_in_bull_state"], -x["bull_prob"]))

    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S IST"),
        "smoothing_windows": SMOOTHING_WINDOWS,
        "smoothing_benchmark": smoothing_benchmark,
        "sector_summaries": sector_summaries,
        "sector_details": all_sector_results
    }

    # Write to JSON and JS data files
    with open(JSON_OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

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
