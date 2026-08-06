"""
Zero-Forward-Bias Automated Verification Test
============================================
Verifies that out-of-sample forward filtering probabilities P(S_t | x_{1:t})
assigned to date t are 100.0000% identical whether future data (t+1 ... T) exists or not.
"""

import os
import sys
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(SCRIPT_DIR)

from hmm_regime_engine import compute_smoothed_features, GaussianHMM3State

def test_zero_forward_bias():
    print("="*80)
    print("RUNNING ZERO-FORWARD-BIAS AUTOMATED VERIFICATION TEST")
    print("="*80)

    INDICES_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "OHLCV", "Indices", "Daily")
    test_file = os.path.join(INDICES_DIR, "banking_daily.csv")

    if not os.path.exists(test_file):
        print(f"Error: {test_file} not found!")
        return

    df = pd.read_csv(test_file, index_col=0, parse_dates=True)
    df.sort_index(inplace=True)

    feat_df = compute_smoothed_features(df, k=5)
    X_full = feat_df[["return", "log_vol", "detrend_err"]].values
    n = len(X_full)

    # 1. Fit HMM model on initial training window
    train_n = int(n * 0.7)
    model = GaussianHMM3State(n_states=3, random_state=42)
    model.fit(X_full[:train_n])

    # 2. Run real-time out-of-sample forward filter on full dataset
    probs_full = model.filter_realtime(X_full)

    # 3. Truncate dataset at t_0 = n - 50 and run real-time filter on truncated dataset
    t_0 = n - 50
    X_trunc = X_full[:t_0]
    probs_trunc = model.filter_realtime(X_trunc)

    # 4. Compare probability vector at t_0 - 1
    prob_full_t0 = probs_full[t_0 - 1]
    prob_trunc_t0 = probs_trunc[-1]

    max_diff = np.max(np.abs(prob_full_t0 - prob_trunc_t0))
    print(f"Probabilities at t_0 (Full Series):      {prob_full_t0}")
    print(f"Probabilities at t_0 (Truncated Series): {prob_trunc_t0}")
    print(f"Maximum absolute probability difference: {max_diff:.10f}")

    if max_diff < 1e-8:
        print("\n" + "="*80)
        print("VERIFICATION SUCCESS: ZERO FORWARD BIAS VERIFIED 100.0000% EXACT MATCH!")
        print("="*80)
    else:
        print("\n[ERROR] Forward bias detected!")
        sys.exit(1)

if __name__ == "__main__":
    test_zero_forward_bias()
