"""
3-State Macro Regime Engine & Median Hysteresis Smoothing
=========================================================
Architecture:
1. Layer 1: High-Sensitivity Quantile Discretization (7 Micro States)
2. Layer 2: State Compression Mapping (3 Macro Regimes: Bearish=0, Neutral=1, Bullish=2)
3. Layer 3: Non-Linear Median Hysteresis Smoothing (Window k = 1 to 21 candles)
"""

import os
import sys
import numpy as np
import pandas as pd

def compute_3state_macro(df: pd.DataFrame, close_col: str = 'Close', smoothing_window: int = 9) -> pd.DataFrame:
    """
    Computes 3-State Macro Regime (Bearish=0, Neutral=1, Bullish=2)
    with Rolling Median Hysteresis Filtering.
    """
    df_res = df.copy()
    df_res.sort_index(inplace=True)
    
    close = df_res[close_col].values
    n = len(close)
    if n < 5:
        df_res["state"] = 1
        df_res["causal_7state"] = 3
        return df_res

    # 1. Calculate 1-bar log returns
    rets = np.zeros(n)
    rets[1:] = np.diff(close) / (close[:-1] + 1e-6) * 100.0
    
    # 2. 3-candle rolling momentum sum
    mom3 = pd.Series(rets).rolling(3, min_periods=1).sum().values
    
    # 3. Discretize into 7 quantile states (0 to 6)
    quantiles = np.percentile(mom3, np.linspace(0, 100, 8))
    quantiles[0] -= 1e-5
    quantiles[-1] += 1e-5
    causal_7state = np.clip(np.digitize(mom3, quantiles) - 1, 0, 6)
    
    # 4. Map 7 States -> 3 Macro Regimes
    #    States 0, 1, 2  -> 0 (Bearish 🔴)
    #    State  3        -> 1 (Neutral 🟡)
    #    States 4, 5, 6  -> 2 (Bullish 🟢)
    raw_macro_3state = np.where(causal_7state <= 2, 0, np.where(causal_7state >= 4, 2, 1))
    
    # 5. Apply Rolling Median Hysteresis Smoothing (Window k = 1 to 21 candles)
    if smoothing_window > 1:
        macro_3state = (
            pd.Series(raw_macro_3state)
            .rolling(smoothing_window, min_periods=1, center=True)
            .median()
            .round()
            .astype(int)
            .values
        )
    else:
        macro_3state = raw_macro_3state

    df_res["state"] = macro_3state
    df_res["causal_7state"] = causal_7state
    return df_res
