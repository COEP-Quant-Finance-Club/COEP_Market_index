"""
Institutional Free-Float Market-Cap Sector Index Builder Engine (Seamless Version)
================================─────────────────────────────────────────────────
Features:
1. Base Value = 100.0:
   - Every sector index starts at 100.0 on its base trading date.
2. Market Cap & Floating Share Weighting:
   - Shares Q_i = (Market Cap in Cr * 10^7) / Latest Close Price.
   - Constituents are weighted by (Price_i * Q_i) relative to total active sector market cap.
3. Institutional Valid-Pair Return Calculation (ZERO Artificial Gaps / Spikes):
   - Only stocks with valid trading prices on BOTH bar (t-1) AND bar (t) are included in bar (t)'s return.
   - Prevents artificial index drops/spikes when new stocks (IPOs) join a sector basket along time.
4. Intra-Candle Relative Return OHLC Math (Authentic Candlestick Patterns):
   - Guarantees Low <= Open, Close <= High for every candle.
   - Forms real green/red candles, wicks, and classical candlestick patterns.
5. Dual Timeframes:
   - Daily Sector Indices (saved to OHLCV/OHLCV/Sector_Indices/Daily/ & Industries/Sector_Indices/Daily/)
   - 4-Hour Sector Indices (saved to OHLCV/OHLCV/Sector_Indices/4Hour/ & Industries/Sector_Indices/4Hour/)
6. Built-in In-Memory Data Cleaner (ZERO Bad Data Propagation):
   - Detects & corrects unadjusted corporate action splits (1.5x–20x gap-downs) IN-MEMORY.
   - Detects & corrects single-day isolated price spikes (>18% jump that immediately reverts) IN-MEMORY.
   - Cleaning happens BEFORE aggregation: sector indices always built from clean data.
   - Source stock CSVs are NOT modified — this is a safe read-time sanitization layer.
"""

import os
import glob
import pandas as pd
import numpy as np

# Known corporate action split/bonus ratios to detect
_KNOWN_RATIOS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0]


def clean_stock_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    In-memory cleaner: fixes two types of bad data BEFORE aggregation.
    Does NOT write anything to disk. Safe read-time sanitization.

    Pass 1 — Corporate Action Split Fixer:
      Finds gap-down opens >25% vs prior close that match a known split ratio.
      Backward-divides historical OHLC by that ratio (same as backward adjustment).
      Only applies if the data is NOT already adjusted (ratio not near 1.0).

    Pass 2 — Single-Day Isolated Spike Cleaner:
      Finds bars where price jumps >18% from prior close AND next open
      falls straight back down (<85% of spike close), i.e. a 1-day anomaly.
      Divides that single bar's OHLC by the spike ratio to normalise it.
    """
    if df is None or len(df) < 5:
        return df
    if "Close" not in df.columns or "Open" not in df.columns:
        return df

    df = df.copy()
    df.sort_index(inplace=True)

    opens  = df["Open"].values.astype(float)
    closes = df["Close"].values.astype(float)
    n = len(df)

    # ── Pass 1: Corporate Action Split Backward Adjustment ──────────────────
    for i in range(1, n):
        prev_c = closes[i - 1]
        cur_o  = opens[i]
        if prev_c <= 0 or cur_o <= 0:
            continue
        ret = (cur_o - prev_c) / prev_c
        if ret >= -0.25:          # Not a big enough gap-down
            continue
        ratio = prev_c / cur_o
        if abs(ratio - 1.0) < 0.15:  # Already adjusted
            continue
        matched = None
        for r in _KNOWN_RATIOS:
            if abs(ratio - r) / r < 0.15:
                matched = r
                break
        if matched is None:
            continue
        # Backward-adjust all bars before index i
        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                df.iloc[:i, df.columns.get_loc(col)] /= matched
        if "Volume" in df.columns:
            df.iloc[:i, df.columns.get_loc("Volume")] *= matched
        # Refresh arrays after in-place edit
        opens  = df["Open"].values.astype(float)
        closes = df["Close"].values.astype(float)

    # ── Pass 2: Single-Day Isolated Spike Cleaner ────────────────────────────
    opens  = df["Open"].values.astype(float)
    closes = df["Close"].values.astype(float)
    if n >= 3:
        prev_c_arr = closes[:-2]
        cur_o_arr  = opens[1:-1]
        cur_c_arr  = closes[1:-1]
        next_o_arr = opens[2:]

        valid = (prev_c_arr > 0) & (cur_o_arr > 0) & (next_o_arr > 0)
        jump_in  = np.where(valid, cur_o_arr / np.where(prev_c_arr > 0, prev_c_arr, 1), 1.0)
        drop_out = np.where(valid, next_o_arr / np.where(cur_c_arr > 0, cur_c_arr, 1), 1.0)
        sym_diff = np.where(valid, np.abs((next_o_arr - prev_c_arr) / np.where(prev_c_arr > 0, prev_c_arr, 1)), 1.0)

        spike_indices = np.where((jump_in > 1.18) & (drop_out < 0.85) & (sym_diff < 0.08))[0] + 1
        for idx in spike_indices:
            p_close = df["Close"].iloc[idx - 1]
            c_open  = df["Open"].iloc[idx]
            ratio   = c_open / p_close if p_close > 0 else 1.0
            if ratio > 1.15:
                for col in ["Open", "High", "Low", "Close"]:
                    if col in df.columns:
                        df.iloc[idx, df.columns.get_loc(col)] /= ratio

    return df

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
DATA_CSV          = os.path.join(BASE_DIR, "Data.csv")
INDUSTRIES_DIR    = os.path.join(BASE_DIR, "Industries")
OHLCV_BASE        = os.path.join(BASE_DIR, "OHLCV")
STOCKS_DAILY_DIR  = os.path.join(OHLCV_BASE, "Stocks", "Daily")
STOCKS_4H_DIR     = os.path.join(OHLCV_BASE, "Stocks", "4Hour")

# Output Index Directories
OHLCV_SECTOR_DAILY = os.path.join(OHLCV_BASE, "Indices", "Daily")
OHLCV_SECTOR_4H    = os.path.join(OHLCV_BASE, "Indices", "4Hour")
IND_SECTOR_DAILY   = os.path.join(INDUSTRIES_DIR, "Sector_Indices", "Daily")
IND_SECTOR_4H      = os.path.join(INDUSTRIES_DIR, "Sector_Indices", "4Hour")

for d in [OHLCV_SECTOR_DAILY, OHLCV_SECTOR_4H, IND_SECTOR_DAILY, IND_SECTOR_4H]:
    os.makedirs(d, exist_ok=True)
    # Clean old index CSVs to prevent stale sector indices
    for f in glob.glob(os.path.join(d, "*.csv")):
        try:
            os.remove(f)
        except Exception:
            pass

df_data = pd.read_csv(DATA_CSV, low_memory=False)
df_data["Symbol_Clean"] = df_data["Symbol"].astype(str).str.strip().str.upper()

def get_stock_shares_dict(stocks_dir: str) -> dict:
    shares_map = {}
    for idx, row in df_data.iterrows():
        sym = row["Symbol_Clean"]
        safe_sym = sym.replace("/", "_").replace(" ", "_")
        csv_path = os.path.join(stocks_dir, f"{safe_sym}_daily.csv")

        mcap_cr = row.get("market_cap", 0)
        if pd.isna(mcap_cr) or mcap_cr <= 0:
            continue

        mcap_val = float(mcap_cr) * 1e7 # Cr to INR

        if os.path.exists(csv_path):
            try:
                df_s = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                if not df_s.empty and "Close" in df_s.columns:
                    latest_close = float(df_s["Close"].dropna().iloc[-1])
                    if latest_close > 0:
                        shares_map[sym] = mcap_val / latest_close
            except Exception:
                pass
    return shares_map

def build_sector_index_timeframe(sector_name: str, stock_symbols: list, stocks_dir: str, shares_map: dict, is_4h: bool = False) -> tuple[pd.DataFrame, str]:
    prices_open  = {}
    prices_high  = {}
    prices_low   = {}
    prices_close = {}
    volumes      = {}

    valid_symbols = []
    suffix = "_4hour.csv" if is_4h else "_daily.csv"

    for sym in stock_symbols:
        safe_sym = sym.replace("/", "_").replace(" ", "_")
        csv_path = os.path.join(stocks_dir, f"{safe_sym}{suffix}")
        if not os.path.exists(csv_path):
            continue

        try:
            df_s = pd.read_csv(csv_path, index_col=0, parse_dates=True)
            if df_s.empty:
                continue

            df_s.index = pd.to_datetime(df_s.index)
            df_s = df_s[~df_s.index.isna()].sort_index()

            req_cols = ["Open", "High", "Low", "Close", "Volume"]
            if not all(c in df_s.columns for c in req_cols):
                continue

            # ── IN-MEMORY CLEAN: fix splits & spikes before aggregation ──
            df_s = clean_stock_df(df_s)

            prices_open[sym]  = df_s["Open"]
            prices_high[sym]  = df_s["High"]
            prices_low[sym]   = df_s["Low"]
            prices_close[sym] = df_s["Close"]
            volumes[sym]      = df_s["Volume"]
            valid_symbols.append(sym)
        except Exception:
            pass

    if not valid_symbols:
        return pd.DataFrame(), "N/A"

    df_close = pd.DataFrame(prices_close).dropna(how="all")
    df_open  = pd.DataFrame(prices_open).reindex(df_close.index)
    df_high  = pd.DataFrame(prices_high).reindex(df_close.index)
    df_low   = pd.DataFrame(prices_low).reindex(df_close.index)
    df_vol   = pd.DataFrame(volumes).reindex(df_close.index).fillna(0)

    shares = np.array([shares_map.get(sym, 1e6) for sym in valid_symbols])

    close_vals = df_close.values
    open_vals  = df_open.values
    high_vals  = df_high.values
    low_vals   = df_low.values
    vol_vals   = df_vol.values

    n_dt, n_stocks = close_vals.shape
    timestamps = df_close.index
    base_date_str = timestamps[0].strftime("%Y-%m-%d %H:%M") if is_4h else timestamps[0].strftime("%Y-%m-%d")

    idx_open  = np.zeros(n_dt)
    idx_high  = np.zeros(n_dt)
    idx_low   = np.zeros(n_dt)
    idx_close = np.zeros(n_dt)
    idx_vol   = np.zeros(n_dt)

    idx_close[0] = 100.0
    idx_open[0]  = 100.0
    idx_high[0]  = 100.0
    idx_low[0]   = 100.0
    idx_vol[0]   = np.sum(np.nan_to_num(vol_vals[0], nan=0.0))

    for t in range(1, n_dt):
        # Stock must have valid prices on BOTH t-1 AND t to be included in return
        valid_pair = ~np.isnan(close_vals[t]) & ~np.isnan(close_vals[t-1])
        if not np.any(valid_pair):
            idx_open[t]  = idx_close[t-1]
            idx_high[t]  = idx_close[t-1]
            idx_low[t]   = idx_close[t-1]
            idx_close[t] = idx_close[t-1]
            idx_vol[t]   = 0.0
            continue

        prev_c_stocks = close_vals[t-1, valid_pair]
        cur_o_stocks  = open_vals[t, valid_pair]
        cur_h_stocks  = high_vals[t, valid_pair]
        cur_l_stocks  = low_vals[t, valid_pair]
        cur_c_stocks  = close_vals[t, valid_pair]
        q_stocks      = shares[valid_pair]

        w = prev_c_stocks * q_stocks
        w_tot = np.sum(w)
        if w_tot == 0:
            w_tot = 1.0

        ret_o = np.sum(w * ((cur_o_stocks - prev_c_stocks) / prev_c_stocks)) / w_tot
        ret_h = np.sum(w * ((cur_h_stocks - prev_c_stocks) / prev_c_stocks)) / w_tot
        ret_l = np.sum(w * ((cur_l_stocks - prev_c_stocks) / prev_c_stocks)) / w_tot
        ret_c = np.sum(w * ((cur_c_stocks - prev_c_stocks) / prev_c_stocks)) / w_tot

        prev_idx_c = idx_close[t-1]
        idx_open[t]  = prev_idx_c * (1.0 + ret_o)
        idx_high[t]  = max(prev_idx_c * (1.0 + ret_h), idx_open[t])
        idx_low[t]   = min(prev_idx_c * (1.0 + ret_l), idx_open[t])
        idx_close[t] = prev_idx_c * (1.0 + ret_c)
        idx_high[t]  = max(idx_high[t], idx_close[t])
        idx_low[t]   = min(idx_low[t], idx_close[t])
        idx_vol[t]   = np.sum(np.nan_to_num(vol_vals[t], nan=0.0))

    index_df = pd.DataFrame({
        "Open":   idx_open,
        "High":   idx_high,
        "Low":    idx_low,
        "Close":  idx_close,
        "Volume": idx_vol
    }, index=timestamps)

    index_df = index_df[index_df["Close"] > 0]
    return index_df, base_date_str

def main():
    print("="*70)
    print("INSTITUTIONAL SEAMLESS FREE-FLOAT MARKET-CAP SECTOR INDEX ENGINE")
    print("="*70)

    shares_map = get_stock_shares_dict(STOCKS_DAILY_DIR)
    print(f"Calculated share counts for {len(shares_map)} stocks from Market Cap data.")

    all_csvs = [f for f in os.listdir(INDUSTRIES_DIR) if os.path.isfile(os.path.join(INDUSTRIES_DIR, f)) and f.endswith(".csv") and not f.endswith("summary.csv")]

    summary_records = []

    for f_name in sorted(all_csvs):
        clean_sec_name = f_name.replace("_enhanced.csv", "").replace(".csv", "").upper()
        ind_file = os.path.join(INDUSTRIES_DIR, f_name)

        df_sec = pd.read_csv(ind_file)
        sym_col = next((c for c in df_sec.columns if c.lower() == "symbol"), None)
        if not sym_col:
            continue

        symbols = df_sec[sym_col].dropna().astype(str).str.strip().str.upper().unique().tolist()

        df_daily_idx, base_date_daily = build_sector_index_timeframe(
            clean_sec_name, symbols, STOCKS_DAILY_DIR, shares_map, is_4h=False
        )

        if not df_daily_idx.empty:
            out_daily_ohlcv = os.path.join(OHLCV_SECTOR_DAILY, f"{clean_sec_name.lower()}_daily.csv")
            out_daily_ind   = os.path.join(IND_SECTOR_DAILY,   f"{clean_sec_name.lower()}_daily.csv")
            df_daily_idx.to_csv(out_daily_ohlcv)
            df_daily_idx.to_csv(out_daily_ind)

        df_4h_idx, base_date_4h = build_sector_index_timeframe(
            clean_sec_name, symbols, STOCKS_4H_DIR, shares_map, is_4h=True
        )

        if not df_4h_idx.empty:
            out_4h_ohlcv = os.path.join(OHLCV_SECTOR_4H, f"{clean_sec_name.lower()}_4hour.csv")
            out_4h_ind   = os.path.join(IND_SECTOR_4H,   f"{clean_sec_name.lower()}_4hour.csv")
            df_4h_idx.to_csv(out_4h_ohlcv)
            df_4h_idx.to_csv(out_4h_ind)

        start_val = 100.0
        end_val = df_daily_idx["Close"].iloc[-1] if not df_daily_idx.empty else 0.0
        tot_return_pct = ((end_val - start_val) / start_val) * 100.0 if start_val > 0 else 0.0

        if not df_daily_idx.empty:
            summary_records.append({
                "Sector Name": clean_sec_name,
                "Daily Index File": f"{clean_sec_name.lower()}_daily.csv",
                "4Hour Index File": f"{clean_sec_name.lower()}_4hour.csv",
                "Constituents Count": len(symbols),
                "Base Date": base_date_daily,
                "Base Value": 100.0,
                "Current Index Value": round(end_val, 2),
                "Total Sector Return %": f"{tot_return_pct:+.2f}%"
            })

            print(f"[OK] {clean_sec_name:32s} -> Base: 100.0 | Current: {end_val:7.2f} ({tot_return_pct:+.2f}%) | Stocks: {len(symbols)}")

    if summary_records:
        summary_df = pd.DataFrame(summary_records)
        summary_df = summary_df.drop_duplicates(subset=["Sector Name"]).sort_values(by="Current Index Value", ascending=False)
        summary_path = os.path.join(INDUSTRIES_DIR, "sector_index_summary.csv")
        summary_df.to_csv(summary_path, index=False)
        print("\n" + "="*70)
        print(f"SUCCESS: Built {len(summary_df)} Seamless Sector Indices (Daily & 4-Hour)!")
        print(f"Master Sector Index Report saved to {summary_path}")
        print("="*70)

if __name__ == "__main__":
    main()
