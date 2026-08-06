import os
import glob
import json
import pandas as pd
import numpy as np

BASE_DIR = r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management"
INDICES_DIR = os.path.join(BASE_DIR, "OHLCV", "Indices", "Daily")
WEIGHTS_FILE = os.path.join(BASE_DIR, "json", "todays_sector_weights.json")
OUTPUT_JS = os.path.join(BASE_DIR, "dashboard_data.js")

def main():
    print("="*70)
    print("BUILDING SECTOR DASHBOARD DATA JS")
    print("="*70)

    # Load stock counts per sector from weights file
    stock_counts = {}
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                weights = json.load(f)
                for sec, stk_map in weights.items():
                    stock_counts[sec] = len(stk_map)
        except Exception as e:
            print(f"Warning loading weights file: {e}")

    summary = []
    daily_data = {}

    csv_files = glob.glob(os.path.join(INDICES_DIR, "*.csv"))
    print(f"Processing {len(csv_files)} sector index files...")

    for f in csv_files:
        basename = os.path.basename(f).replace("_daily.csv", "").replace(".csv", "").upper()
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if df.empty or "Close" not in df.columns:
                continue

            df.sort_index(inplace=True)
            
            # Extract daily candles
            bars = []
            for dt, row in df.iterrows():
                dt_str = dt.strftime("%Y-%m-%d")
                o_val = round(float(row.get("Open", row["Close"])), 2)
                h_val = round(float(row.get("High", row["Close"])), 2)
                l_val = round(float(row.get("Low", row["Close"])), 2)
                c_val = round(float(row["Close"]), 2)
                v_val = int(row.get("Volume", 0))

                bars.append({
                    "time": dt_str,
                    "open": o_val,
                    "high": h_val,
                    "low": l_val,
                    "close": c_val,
                    "volume": v_val,
                    "Open": o_val,
                    "High": h_val,
                    "Low": l_val,
                    "Close": c_val,
                    "Volume": v_val
                })

            if not bars:
                continue

            cur_val = bars[-1]["close"]
            tot_ret_val = round(((cur_val - 100.0) / 100.0) * 100.0, 2)
            ret_str = f"{'+' if tot_ret_val >= 0 else ''}{tot_ret_val:.2f}%"
            n_stocks = stock_counts.get(basename, 10)

            summary.append({
                "sector": basename,
                "Sector Name": basename,
                "current_val": cur_val,
                "Current Index Value": cur_val,
                "total_return_pct": ret_str,
                "Total Sector Return %": ret_str,
                "stock_count": n_stocks,
                "Constituents Count": n_stocks
            })

            daily_data[basename] = bars
            print(f"  [OK] {basename:30s} -> {len(bars):4d} bars | Current: {cur_val:7.2f} | Return: {ret_str}")

        except Exception as e:
            print(f"  [ERROR] {basename}: {e}")

    # Sort summary by total return descending
    summary.sort(key=lambda x: x["total_return_pct"], reverse=True)

    payload = {
        "summary": summary,
        "daily": daily_data,
        "fourhour": {}
    }

    print(f"\nWriting payload to {OUTPUT_JS}...")
    with open(OUTPUT_JS, "w", encoding="utf-8") as f:
        f.write("window.SECTOR_INDEX_DATA = ")
        json.dump(payload, f)
        f.write(";\n")

    size_mb = os.path.getsize(OUTPUT_JS) / (1024 * 1024)
    print("="*70)
    print(f"SUCCESS: Generated dashboard_data.js ({size_mb:.2f} MB) for {len(summary)} sectors!")
    print("="*70)

if __name__ == "__main__":
    main()
