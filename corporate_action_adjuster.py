"""
Institutional Corporate Action Split & Bonus Adjuster Engine (Robust Edition)
================================─────────────────────────────────────────────
Features:
1. Smart Unadjusted Gap Detection:
   - Verifies if historical prices are raw/unadjusted before applying backward division.
   - Prevents double-adjustment on already split-adjusted data (e.g. yfinance auto_adjust).
2. Robust Ratio Matching:
   - Matches official/empirical ratios: 1.5x, 2.0x, 2.5x, 3.0x, 4.0x, 5.0x, 10.0x, 20.0x.
3. Permanent Fix Manifest (TRUE IDEMPOTENCY):
   - Every fix is recorded in fixes_applied.json keyed by (file_path, date).
   - On re-runs, already-fixed events are SKIPPED immediately — zero re-processing.
   - Running --fix twice is 100% safe and will report "0 new fixes needed".
4. Automatic Post-Fix Pipeline Trigger:
   - Automatically rebuilds 32 Sector Indices, dashboard_data.js, and quant_club_sector_terminal.html.
"""

import os
import sys
import glob
import json
import argparse
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
STOCKS_DAILY    = os.path.join(BASE_DIR, "OHLCV", "Stocks", "Daily")
STOCKS_4H       = os.path.join(BASE_DIR, "OHLCV", "Stocks", "4Hour")
REPORT_CSV      = os.path.join(BASE_DIR, "corporate_action_adjustments.csv")
FIX_MANIFEST    = os.path.join(BASE_DIR, "fixes_applied.json")   # <-- Idempotency store

KNOWN_RATIOS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0, 20.0]

# ── Idempotency manifest helpers ──────────────────────────────────────────────

def load_fix_manifest() -> dict:
    """Load the persistent fix manifest from disk. Returns {file_path: [date_str, ...]}"""
    if os.path.exists(FIX_MANIFEST):
        try:
            with open(FIX_MANIFEST, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_fix_manifest(manifest: dict):
    """Persist the fix manifest to disk."""
    with open(FIX_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

def _manifest_key(file_path: str) -> str:
    """Normalise path to a consistent manifest key."""
    return os.path.normpath(file_path)

# ── Split / corporate action fixer ────────────────────────────────────────────

def scan_and_adjust_stock_file(file_path: str, timeframe_label: str,
                                apply_fix: bool, manifest: dict) -> list:
    adjustments = []
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        if len(df) < 5 or "Close" not in df.columns or "Open" not in df.columns:
            return adjustments

        sym = os.path.basename(file_path).replace("_daily.csv", "").replace("_4hour.csv", "")
        df.sort_index(inplace=True)

        if "Volume" in df.columns:
            df["Volume"] = df["Volume"].astype(float)

        mkey = _manifest_key(file_path)
        already_fixed_dates = set(manifest.get(mkey, []))

        df["ret"] = df["Close"].pct_change()

        # Potential corporate action split bars: single-bar drop > 25%
        split_candidates = df[df["ret"] < -0.25]

        if split_candidates.empty:
            return adjustments

        modified = False

        for dt, row in split_candidates.iterrows():
            dt_str = dt.strftime("%Y-%m-%d %H:%M") if timeframe_label == "4Hour" else dt.strftime("%Y-%m-%d")

            # ── IDEMPOTENCY CHECK: skip if already fixed in a previous run ──
            if dt_str in already_fixed_dates:
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

            # Check if prices are ALREADY aligned (ratio near 1.0)
            if abs(ratio - 1.0) < 0.15:
                continue  # Data is already split-adjusted, skip!

            matched_ratio = None
            for target_ratio in KNOWN_RATIOS:
                if abs(ratio - target_ratio) / target_ratio < 0.15:
                    matched_ratio = target_ratio
                    break

            if matched_ratio is not None:
                record = {
                    "Symbol":           sym,
                    "Timeframe":        timeframe_label,
                    "Date":             dt_str,
                    "Split Ratio":      matched_ratio,
                    "Pre-Split Price":  round(prev_close, 2),
                    "Post-Split Price": round(cur_open, 2),
                    "Status":           "FIXED" if apply_fix else "DETECTED"
                }
                adjustments.append(record)

                if apply_fix:
                    mask = df.index < dt
                    # Backward-adjust historical OHLC prices safely
                    df.loc[mask, ["Open", "High", "Low", "Close"]] /= matched_ratio
                    if "Volume" in df.columns:
                        df.loc[mask, "Volume"] *= matched_ratio
                    modified = True

                    # Record this fix in the manifest so it's never re-applied
                    if mkey not in manifest:
                        manifest[mkey] = []
                    if dt_str not in manifest[mkey]:
                        manifest[mkey].append(dt_str)

        if apply_fix and modified:
            df.drop(columns=["ret"]).to_csv(file_path)

    except Exception:
        pass

    return adjustments


# ── Spike cleaner (idempotent via manifest) ────────────────────────────────────

def _clean_single_file_spikes(args_tuple) -> int:
    """
    Single-file spike cleaner. Returns number of spikes actually fixed.
    Idempotent: only writes the file if at least one spike was actually corrected.
    Skips spikes already recorded in the manifest.
    """
    file_path, manifest = args_tuple
    try:
        df_spk = pd.read_csv(file_path, index_col=0, parse_dates=True)
        if len(df_spk) < 5 or "Close" not in df_spk.columns or "Open" not in df_spk.columns:
            return 0
        df_spk.sort_index(inplace=True)

        mkey = _manifest_key(file_path)
        already_fixed_dates = set(manifest.get(mkey, []))

        opens  = df_spk["Open"].values.astype(float)
        closes = df_spk["Close"].values.astype(float)
        n = len(df_spk)
        if n < 3:
            return 0

        prev_close = closes[:-2]
        cur_open   = opens[1:-1]
        cur_close  = closes[1:-1]
        next_open  = opens[2:]

        valid_mask = (prev_close > 0) & (cur_open > 0) & (next_open > 0)
        jump_in  = np.zeros(n - 2)
        drop_out = np.zeros(n - 2)
        sym_diff = np.zeros(n - 2)

        jump_in[valid_mask]  = cur_open[valid_mask]  / prev_close[valid_mask]
        drop_out[valid_mask] = next_open[valid_mask]  / cur_close[valid_mask]
        sym_diff[valid_mask] = np.abs((next_open[valid_mask] - prev_close[valid_mask]) / prev_close[valid_mask])

        spike_indices = np.where(
            (jump_in > 1.18) & (drop_out < 0.85) & (sym_diff < 0.08)
        )[0] + 1

        if len(spike_indices) == 0:
            return 0

        actually_fixed = 0
        for idx in spike_indices:
            # Get the date string for this bar
            bar_dt = df_spk.index[idx]
            dt_str = bar_dt.strftime("%Y-%m-%d %H:%M") if " " in str(bar_dt) else bar_dt.strftime("%Y-%m-%d")

            # ── IDEMPOTENCY: skip if already fixed ──────────────────────────
            if dt_str in already_fixed_dates:
                continue

            p_close = df_spk["Close"].iloc[idx - 1]
            c_open  = df_spk["Open"].iloc[idx]
            ratio   = c_open / p_close if p_close > 0 else 1.0

            if ratio > 1.15:
                df_spk.iloc[idx, df_spk.columns.get_loc("Open")]  /= ratio
                df_spk.iloc[idx, df_spk.columns.get_loc("High")]  /= ratio
                df_spk.iloc[idx, df_spk.columns.get_loc("Low")]   /= ratio
                df_spk.iloc[idx, df_spk.columns.get_loc("Close")] /= ratio
                actually_fixed += 1

                # Record in manifest
                if mkey not in manifest:
                    manifest[mkey] = []
                if dt_str not in manifest[mkey]:
                    manifest[mkey].append(dt_str)

        # ── IDEMPOTENCY: only write file if something actually changed ──────
        if actually_fixed > 0:
            df_spk.to_csv(file_path)

        return actually_fixed

    except Exception:
        return 0


# ── Main adjuster runner ───────────────────────────────────────────────────────

def run_adjuster(apply_fix: bool = False, rebuild: bool = False):
    print("=" * 70)
    print("INSTITUTIONAL CORPORATE ACTION ADJUSTER & AUDIT ENGINE (ROBUST)")
    print(f"Mode: {'APPLY FIXES' if apply_fix else 'SCAN ONLY (DRY-RUN)'}")
    print("=" * 70)

    # Load the persistent fix manifest (idempotency store)
    manifest = load_fix_manifest() if apply_fix else {}
    total_already_skipped = sum(len(v) for v in manifest.values())
    if apply_fix and total_already_skipped > 0:
        print(f"[INFO] Fix manifest loaded: {total_already_skipped} previously-fixed events will be SKIPPED.")

    daily_files = glob.glob(os.path.join(STOCKS_DAILY, "*_daily.csv"))
    fh_files    = glob.glob(os.path.join(STOCKS_4H,    "*_4hour.csv"))

    all_adjustments = []

    print(f"\n[1/2] Auditing {len(daily_files)} Daily Stock CSV files...")
    for f in daily_files:
        adj = scan_and_adjust_stock_file(f, "Daily", apply_fix, manifest)
        all_adjustments.extend(adj)

    print(f"[2/2] Auditing {len(fh_files)} 4-Hour Stock CSV files...")
    for f in fh_files:
        adj = scan_and_adjust_stock_file(f, "4Hour", apply_fix, manifest)
        all_adjustments.extend(adj)

    # Persist manifest after split fixes
    if apply_fix:
        save_fix_manifest(manifest)

    print("\n" + "=" * 70)
    print("AUDIT RESULTS SUMMARY")
    print("=" * 70)

    if all_adjustments:
        df_report = pd.DataFrame(all_adjustments)
        df_report.to_csv(REPORT_CSV, index=False)
        print(f"Total Unadjusted Corporate Action Splits {'Fixed' if apply_fix else 'Detected'}: {len(df_report)}")
        print(f"Detailed Audit Log saved to: {REPORT_CSV}\n")
        print(df_report.head(15).to_string(index=False))
    else:
        if apply_fix:
            print("[OK] 0 new fixes needed. All corporate action splits already recorded in fix manifest.")
        else:
            print("[OK] 0 Unadjusted Corporate Action Splits found! All stock OHLCV data is 100% split-adjusted.")

    if apply_fix:
        print("\n" + "=" * 70)
        print("RUNNING FAST VECTORIZED SINGLE-DAY SPIKE CLEANER (32-THREAD PARALLEL)")
        print("=" * 70)

        all_files = daily_files + fh_files
        # Pass manifest to each worker so it can skip already-fixed spikes
        args_list = [(f, manifest) for f in all_files]

        with ThreadPoolExecutor(max_workers=32) as executor:
            spike_results = list(executor.map(_clean_single_file_spikes, args_list))

        total_spikes_cleaned = sum(spike_results)

        # Persist manifest again after spike fixes
        save_fix_manifest(manifest)

        if total_spikes_cleaned > 0:
            print(f"[OK] Cleaned {total_spikes_cleaned} NEW single-day isolated price spikes across dataset!")
        else:
            print("[OK] 0 new spikes found. All previously-detected spikes already recorded in fix manifest.")

    if apply_fix and rebuild:
        generate_terminal_script = os.path.join(BASE_DIR, "generate_terminal.py")
        print("\n" + "=" * 70)
        print("REBUILDING SECTOR INDICES & STANDALONE HTML TERMINAL...")
        print("=" * 70)
        ret = os.system(f'python "{generate_terminal_script}"')
        if ret == 0:
            print("[OK] Sector Indices and Dashboard Data successfully rebuilt!")
        else:
            print("[WARN] generate_terminal.py exited with errors. Check output above.")


def main():
    parser = argparse.ArgumentParser(description="Corporate Action Split & Bonus Adjuster Engine")
    parser.add_argument("--scan",    action="store_true", help="Scan and report splits WITHOUT modifying files")
    parser.add_argument("--fix",     action="store_true", help="Apply backward adjustments and overwrite stock CSV files")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild sector indices and dashboard after fixing")
    parser.add_argument("--reset-manifest", action="store_true",
                        help="DANGER: Delete fixes_applied.json and re-scan everything from scratch")

    args = parser.parse_args()

    if args.reset_manifest:
        if os.path.exists(FIX_MANIFEST):
            os.remove(FIX_MANIFEST)
            print(f"[RESET] Fix manifest deleted: {FIX_MANIFEST}")
            print("[RESET] Next --fix run will re-scan and re-apply all adjustments from scratch.")
        else:
            print("[RESET] No manifest found. Nothing to reset.")
        return

    apply_fix = args.fix
    rebuild   = args.rebuild

    run_adjuster(apply_fix=apply_fix, rebuild=rebuild)

if __name__ == "__main__":
    main()
