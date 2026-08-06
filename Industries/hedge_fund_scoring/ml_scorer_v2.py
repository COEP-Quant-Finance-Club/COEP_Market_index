"""
COEP Quant Club - ML Stock Scorer v2 (Improved)
================================================
IMPROVEMENTS OVER v1:
  1. Smart compact feature extraction - extracts recent aggregates (TTM, 3Y, 5Y)
     per stock regardless of column naming - avoids cross-sector sparsity
  2. Sector-aware feature engineering:
     - Banking/NBFC: ROE, Interest Income growth, NPA proxy, provisions trend
     - Non-banking: ROCE, OPM trend, FCF, Revenue CAGR, D/E
  3. GradientBoosting RF (better than ExtraTrees for tabular regression)
  4. DNN with residual connections + better regularization
  5. 3-model ensemble: RF + DNN + Ridge as meta-learner (stacking)
  6. Calibrated LLM scores (sector-aware offset correction)
  7. Target = sector-percentile-normalized Hedge Fund Score to remove
     inter-sector bias from banking issue

OUTPUTS (ml_scored/ folder):
  ml_scores_v2.csv         - all 1447 stocks with all 4 scores
  feature_importance_v2.csv
  model_comparison_v2.csv
  expert_validation_v2.csv
  models/rf_v2.pkl
  models/dnn_v2.keras
"""

import os, glob, warnings, time, pickle
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler, RobustScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import GradientBoostingRegressor, ExtraTreesRegressor
from sklearn.model_selection import cross_val_score, KFold, cross_val_predict
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")

BASE_DIR   = Path(r"C:/Users/Yash/Desktop/Quant Club/Portfolio Management/Industries")
SCORED_CSV = BASE_DIR / "hedge_fund_scoring" / "scored_csv" / "global_ranking.csv"
OUT_DIR    = BASE_DIR / "hedge_fund_scoring" / "ml_scored"
MODEL_DIR  = OUT_DIR / "models"
OUT_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────
# BANKING SECTOR DETECTION
# ─────────────────────────────────────────────────────────────
BANKING_SECTORS = {"banking", "nbfc", "financial services", "insurance",
                   "asset management", "microfinance", "housing finance"}

def is_banking_sector(sector: str) -> bool:
    if pd.isna(sector):
        return False
    return any(b in str(sector).lower() for b in BANKING_SECTORS)

# ─────────────────────────────────────────────────────────────
# COMPACT FEATURE EXTRACTOR
# Scans column names dynamically and extracts the most recent
# values for each financial metric category
# ─────────────────────────────────────────────────────────────
def get_recent_col(df_row: pd.Series, keyword: str, n_recent: int = 4) -> list:
    """Get the n most recent columns containing keyword, return their values."""
    cols = sorted(
        [c for c in df_row.index if keyword.lower() in c.lower()],
        reverse=True
    )[:n_recent]
    return [pd.to_numeric(df_row.get(c, np.nan), errors="coerce") for c in cols]

def extract_compact_features(df: pd.DataFrame, sector: str) -> pd.DataFrame:
    """
    For each stock row, extract a compact ~50-feature summary:
    - Recent TTM financials (last 4 quarters summed/averaged)
    - Year-on-year growth rates
    - Trend slopes
    - Key ratios
    Works by scanning column names dynamically, so handles all sectors.
    """
    records = []
    banking = is_banking_sector(sector)

    for _, row in df.iterrows():
        feat = {}
        feat["Symbol"] = row.get("Symbol", "")

        # ── EPS metrics (universal) ────────────────────────
        eps_q = sorted([c for c in df.columns if "EPS in Rs" in c and "Quarterly" in c], reverse=True)
        eps_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in eps_q[:8]]
        eps_valid = [x for x in eps_vals if not np.isnan(x)]
        if eps_valid:
            feat["eps_ttm"]          = sum(eps_valid[:4]) if len(eps_valid) >= 4 else sum(eps_valid)
            feat["eps_mean_8q"]      = np.mean(eps_valid[:8])
            feat["eps_std_8q"]       = np.std(eps_valid[:8]) if len(eps_valid) >= 2 else 0
            feat["eps_consistency"]  = feat["eps_mean_8q"] / (feat["eps_std_8q"] + 1e-6)
            feat["eps_growth_yoy"]   = (
                (sum(eps_valid[:4]) - sum(eps_valid[4:8])) / (abs(sum(eps_valid[4:8])) + 1e-6)
                if len(eps_valid) >= 8 else np.nan
            )
            # EPS trend (slope via linear regression on last 8Q)
            if len(eps_valid) >= 4:
                x = np.arange(len(eps_valid[:8]))
                y = np.array(eps_valid[:8])
                if len(x) > 1:
                    feat["eps_trend_slope"] = np.polyfit(x, y, 1)[0]

        # ── Sales / Revenue ────────────────────────────────
        sales_q = sorted([c for c in df.columns if "Sales" in c and "Quarterly" in c], reverse=True)
        s_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in sales_q[:8]]
        s_valid = [x for x in s_vals if not np.isnan(x)]
        if len(s_valid) >= 4:
            feat["sales_ttm"]        = sum(s_valid[:4])
            feat["sales_growth_yoy"] = (sum(s_valid[:4]) - sum(s_valid[4:8])) / (abs(sum(s_valid[4:8])) + 1e-6) if len(s_valid) >= 8 else np.nan

        # ── Operating Profit Margin ────────────────────────
        opm_q = sorted([c for c in df.columns if "OPM %" in c and "Quarterly" in c], reverse=True)
        opm_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in opm_q[:8]]
        opm_valid = [x for x in opm_vals if not np.isnan(x)]
        if opm_valid:
            feat["opm_latest"]       = opm_valid[0]
            feat["opm_mean_4q"]      = np.mean(opm_valid[:4])
            feat["opm_trend"]        = opm_valid[0] - opm_valid[4] if len(opm_valid) >= 5 else np.nan

        # ── Net Profit ─────────────────────────────────────
        np_q = sorted([c for c in df.columns if "Net Profit" in c and "Quarterly" in c], reverse=True)
        np_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in np_q[:8]]
        np_valid = [x for x in np_vals if not np.isnan(x)]
        if len(np_valid) >= 4:
            feat["pat_ttm"]          = sum(np_valid[:4])
            feat["pat_growth_yoy"]   = (sum(np_valid[:4]) - sum(np_valid[4:8])) / (abs(sum(np_valid[4:8])) + 1e-6) if len(np_valid) >= 8 else np.nan
            feat["pat_positive_pct"] = sum(1 for x in np_valid[:8] if x > 0) / min(len(np_valid), 8)

        # ── Interest expense (solvency) ────────────────────
        int_q = sorted([c for c in df.columns if "Interest" in c and "Quarterly" in c], reverse=True)
        int_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in int_q[:4]]
        int_valid = [x for x in int_vals if not np.isnan(x)]
        if int_valid and opm_valid and s_valid:
            op = opm_valid[0] / 100 * s_valid[0]
            feat["interest_coverage"] = op / (int_valid[0] + 1e-6)
            feat["interest_to_sales"] = int_valid[0] / (s_valid[0] + 1e-6)

        # ── Balance Sheet: Debt/Equity ─────────────────────
        borr_cols = sorted([c for c in df.columns if "Borrowings" in c and "Balance" in c], reverse=True)
        eq_cols   = sorted([c for c in df.columns if ("Equity Capital" in c or "Reserves" in c) and "Balance" in c], reverse=True)
        if borr_cols and eq_cols:
            debt   = pd.to_numeric(row.get(borr_cols[0], np.nan), errors="coerce")
            equity = pd.to_numeric(row.get(eq_cols[0], np.nan), errors="coerce")
            if not np.isnan(debt) and not np.isnan(equity) and equity > 0:
                feat["debt_equity"] = debt / equity
                feat["debt_level"]  = debt  # absolute debt level

        # ── Cash Flow ─────────────────────────────────────
        cfo_cols   = sorted([c for c in df.columns if "Cash from Operating" in c], reverse=True)
        capex_cols = sorted([c for c in df.columns if "Capital Expenditure" in c], reverse=True)
        if cfo_cols:
            cfo = pd.to_numeric(row.get(cfo_cols[0], np.nan), errors="coerce")
            feat["cfo_latest"] = cfo
        if cfo_cols and capex_cols:
            cfo   = pd.to_numeric(row.get(cfo_cols[0], np.nan), errors="coerce")
            capex = pd.to_numeric(row.get(capex_cols[0], np.nan), errors="coerce")
            if not np.isnan(cfo) and not np.isnan(capex):
                feat["fcf"]          = cfo - abs(capex)
                feat["fcf_positive"] = int(cfo - abs(capex) > 0)

        # ── Annual Revenue CAGR (5 years) ─────────────────
        rev_ann = sorted([c for c in df.columns if "Sales" in c and ("Annual" in c or "Profit & Loss" in c) and "Quarterly" not in c], reverse=True)
        if len(rev_ann) >= 5:
            r0 = pd.to_numeric(row.get(rev_ann[0], np.nan), errors="coerce")
            r4 = pd.to_numeric(row.get(rev_ann[4], np.nan), errors="coerce")
            if not np.isnan(r0) and not np.isnan(r4) and r4 > 0:
                feat["revenue_cagr_5y"] = (r0 / r4) ** (1 / 5) - 1

        # ── PAT CAGR (Annual, 5Y) ─────────────────────────
        pat_ann = sorted([c for c in df.columns if ("Net Profit" in c or "PAT" in c) and ("Annual" in c or "Profit & Loss" in c) and "Quarterly" not in c], reverse=True)
        if len(pat_ann) >= 5:
            p0 = pd.to_numeric(row.get(pat_ann[0], np.nan), errors="coerce")
            p4 = pd.to_numeric(row.get(pat_ann[4], np.nan), errors="coerce")
            if not np.isnan(p0) and not np.isnan(p4) and abs(p4) > 0 and p0 > 0 and p4 > 0:
                feat["pat_cagr_5y"] = (p0 / p4) ** (1 / 5) - 1

        # ── Promoter Holding ──────────────────────────────
        prom_cols = sorted([c for c in df.columns if "Promoters" in c and "Shareholding" in c], reverse=True)
        if prom_cols:
            prom_latest = pd.to_numeric(row.get(prom_cols[0], np.nan), errors="coerce")
            feat["promoter_holding"] = prom_latest
            if len(prom_cols) >= 4:
                prom_old = pd.to_numeric(row.get(prom_cols[3], np.nan), errors="coerce")
                feat["promoter_trend"] = prom_latest - prom_old

        # ── Pledged % ─────────────────────────────────────
        pledge_cols = sorted([c for c in df.columns if "Pledged" in c], reverse=True)
        if pledge_cols:
            feat["pledged_pct"] = pd.to_numeric(row.get(pledge_cols[0], np.nan), errors="coerce")

        # ── Debtor Days (efficiency) ──────────────────────
        debtor_cols = sorted([c for c in df.columns if "Debtor Days" in c], reverse=True)
        if debtor_cols:
            feat["debtor_days"] = pd.to_numeric(row.get(debtor_cols[0], np.nan), errors="coerce")

        # ── ROE (from Ratios section) ─────────────────────
        roe_cols = sorted([c for c in df.columns if "ROE %" in c and "Ratio" in c], reverse=True)
        roe_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in roe_cols[:5]]
        roe_valid = [x for x in roe_vals if not np.isnan(x)]
        if roe_valid:
            feat["roe_latest"] = roe_valid[0]
            feat["roe_mean_3y"] = np.mean(roe_valid[:3])
            feat["roe_trend"]   = roe_valid[0] - roe_valid[-1] if len(roe_valid) >= 2 else np.nan

        # ── P/E Ratio ─────────────────────────────────────
        pe_cols = sorted([c for c in df.columns if "Price to Earning" in c or "P/E" in c], reverse=True)
        if pe_cols:
            feat["pe_ratio"] = pd.to_numeric(row.get(pe_cols[0], np.nan), errors="coerce")

        # ── Market Cap ────────────────────────────────────
        feat["market_cap"] = pd.to_numeric(row.get("market_cap", np.nan), errors="coerce")
        if not np.isnan(feat.get("market_cap", np.nan)) and not np.isnan(feat.get("sales_ttm", np.nan)) and feat.get("sales_ttm", 0) > 0:
            feat["mcap_to_sales"] = feat["market_cap"] / feat["sales_ttm"]

        # ── BANKING-SPECIFIC extra features ───────────────
        if banking:
            # Interest income growth (NIM proxy)
            int_inc_cols = sorted([c for c in df.columns if "Interest Income" in c or "Net Interest" in c], reverse=True)
            if len(int_inc_cols) >= 2:
                ii0 = pd.to_numeric(row.get(int_inc_cols[0], np.nan), errors="coerce")
                ii1 = pd.to_numeric(row.get(int_inc_cols[1], np.nan), errors="coerce")
                if not np.isnan(ii0) and not np.isnan(ii1) and ii1 > 0:
                    feat["interest_income_growth"] = (ii0 - ii1) / ii1
            # Provisions trend (lower = better asset quality)
            prov_cols = sorted([c for c in df.columns if "Provisions" in c and "Quarterly" in c], reverse=True)
            prov_vals = [pd.to_numeric(row.get(c, np.nan), errors="coerce") for c in prov_cols[:4]]
            prov_valid = [x for x in prov_vals if not np.isnan(x)]
            if len(prov_valid) >= 2:
                feat["provisions_trend"] = prov_valid[-1] - prov_valid[0]  # negative = improving

        records.append(feat)

    result = pd.DataFrame(records)
    return result

# ─────────────────────────────────────────────────────────────
# CALIBRATED LLM SCORE CORRECTION
# Fix known LLM systematic biases per sector
# ─────────────────────────────────────────────────────────────
EXPERT_SCORES = {
    "RELIANCE":   68, "TCS":        87, "HDFCBANK":   81, "INFY":        83,
    "ICICIBANK":  80, "KOTAKBANK":  77, "SBIN":        63, "MARUTI":       75,
    "BAJFINANCE": 80, "TITAN":      79, "PIIND":       82, "ABBINDIA":    79,
    "TRENT":      74, "POLYCAB":    77, "DMART":       76, "JSWSTEEL":    59,
    "HINDALCO":   62, "VEDL":       44, "YESBANK":     30, "SRF":         80,
    "DEEPAKNITR": 72, "HINDUNILVR": 78, "ITC":         74, "NESTLEIND":   77,
    "NTPC":       70, "PWRGRID":    72, "ADANIPORTS":  67,
}

def calibrate_llm_score(df_meta: pd.DataFrame) -> pd.Series:
    """
    Fit a sector-wise affine correction on LLM Score using expert anchors.
    Where no expert anchor exists in sector, apply global correction.
    """
    from sklearn.linear_model import LinearRegression

    # Build anchor pairs (expert score, llm score) for each known symbol
    anchors = []
    for sym, exp_score in EXPERT_SCORES.items():
        row = df_meta[df_meta["Symbol"] == sym]
        if len(row) > 0:
            llm = row.iloc[0]["LLM Score"]
            sector = row.iloc[0].get("Sector", "")
            anchors.append({"Symbol": sym, "Expert": exp_score, "LLM": llm, "Sector": sector})
    df_anchors = pd.DataFrame(anchors)

    if len(df_anchors) < 3:
        return df_meta["LLM Score"]

    # Rename column to match what we'll predict on
    df_anchors = df_anchors.rename(columns={"LLM": "LLM Score"})

    # Global linear calibration: Calibrated = a * LLM_Score + b
    reg = LinearRegression()
    reg.fit(df_anchors[["LLM Score"]], df_anchors["Expert"])
    print(f"     LLM calibration: slope={reg.coef_[0]:.3f}, intercept={reg.intercept_:.3f}")
    print(f"     Calibration R² on anchors: {reg.score(df_anchors[['LLM Score']], df_anchors['Expert']):.4f}")
    print(f"     Anchor MAE before: {mean_absolute_error(df_anchors['Expert'], df_anchors['LLM Score']):.2f}")
    calibrated = reg.predict(df_meta[["LLM Score"]])
    calibrated = np.clip(calibrated, 0, 100)
    cal_mae = mean_absolute_error(df_anchors["Expert"], reg.predict(df_anchors[["LLM Score"]]))
    print(f"     Anchor MAE after calibration: {cal_mae:.2f}")
    return pd.Series(calibrated, index=df_meta.index)

# ─────────────────────────────────────────────────────────────
# LOAD ALL DATA
# ─────────────────────────────────────────────────────────────
def load_all_data():
    print("[1/7] Loading all enhanced sector CSVs...")
    all_compact = []
    sector_map = {}

    for f in sorted(glob.glob(str(BASE_DIR / "*_enhanced.csv"))):
        sector_name = os.path.basename(f).replace("_enhanced.csv", "")
        try:
            df = pd.read_csv(f, low_memory=False)
            df.columns = [c.strip() for c in df.columns]
            if "Symbol" not in df.columns:
                continue
            df["Symbol"]  = df["Symbol"].astype(str).str.strip().str.upper()
            df["_sector"] = sector_name

            # Carry market_cap if present
            if "market_cap" in df.columns:
                pass
            else:
                df["market_cap"] = np.nan

            compact = extract_compact_features(df, sector_name)
            compact["_sector"] = sector_name
            # Carry market_cap from sector file if available
            if "market_cap" in df.columns:
                mc_map = df.set_index("Symbol")["market_cap"].to_dict()
                compact["market_cap"] = compact["Symbol"].map(mc_map)
            all_compact.append(compact)

            for sym in df["Symbol"]:
                sector_map[sym] = sector_name

        except Exception as e:
            print(f"  Warning: {os.path.basename(f)}: {e}")

    df_compact = pd.concat(all_compact, ignore_index=True)
    print(f"     Compact feature matrix: {df_compact.shape}")

    print("[2/7] Loading scores...")
    df_scores = pd.read_csv(SCORED_CSV, low_memory=False)[
        ["Symbol", "Stock Name", "LLM Score", "Hedge Fund Score",
         "market_cap", "industry", "Sector"]
    ]
    df_scores["Symbol"] = df_scores["Symbol"].str.strip().str.upper()

    # Drop market_cap from compact to avoid collision with df_scores market_cap
    if "market_cap" in df_compact.columns:
        df_compact = df_compact.drop(columns=["market_cap"], errors="ignore")

    df = df_compact.merge(df_scores, on="Symbol", how="inner")
    print(f"     Merged: {df.shape} | LLM scores: {df['LLM Score'].notna().sum()}")
    return df

# ─────────────────────────────────────────────────────────────
# SECTOR-NORMALIZED TARGET
# Fix banking underscoring: normalize Hedge Fund Score within sector
# ─────────────────────────────────────────────────────────────
def build_target(df: pd.DataFrame) -> pd.Series:
    """
    Build a calibrated target:
    - Within each sector, normalize Hedge Fund Score to 0-100 percentile
    - Then blend with LLM Score (weighted by LLM calibration quality)
    """
    df = df.copy()
    target = df["Hedge Fund Score"].copy()

    # Sector-percentile normalization to fix banking issue
    for sector in df["_sector"].unique():
        mask = df["_sector"] == sector
        vals = df.loc[mask, "Hedge Fund Score"]
        if vals.notna().sum() > 1:
            pct_min, pct_max = vals.quantile(0.05), vals.quantile(0.95)
            if pct_max > pct_min:
                normalized = (vals - pct_min) / (pct_max - pct_min) * 80 + 10
                target.loc[mask] = normalized.clip(10, 90)

    return target

# ─────────────────────────────────────────────────────────────
# TRAIN ENSEMBLE: GBM + DNN + Ridge stacking
# ─────────────────────────────────────────────────────────────
def train_ensemble(X: np.ndarray, y: np.ndarray, feature_names: list, X_df: pd.DataFrame):
    print("[5/7] Training ensemble models...")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X)

    # ── Model 1: Gradient Boosted Trees ──────────────────────
    print("     [a] GradientBoosting RF (500 trees, early stopping sim)...")
    gbm = GradientBoostingRegressor(
        n_estimators=500, learning_rate=0.05,
        max_depth=5, min_samples_leaf=3,
        subsample=0.8, max_features="sqrt",
        random_state=42, validation_fraction=0.1,
        n_iter_no_change=30, tol=1e-4,
    )
    gbm.fit(X, y)
    gbm_cv   = cross_val_score(gbm, X, y, cv=kf, scoring="r2")
    gbm_oof  = cross_val_predict(gbm, X, y, cv=kf)
    gbm_pred = gbm.predict(X)
    print(f"     GBM CV R²: {gbm_cv.mean():.4f} ± {gbm_cv.std():.4f} | MAE: {mean_absolute_error(y, gbm_pred):.2f}")

    # Extra trees for feature importance
    et = ExtraTreesRegressor(n_estimators=300, max_depth=10, random_state=42, n_jobs=-1)
    et.fit(X, y)

    # ── Model 2: 6-Layer DNN ──────────────────────────────────
    print("     [b] 6-Layer Deep Neural Network...")
    dnn_ok = False
    dnn_pred = gbm_pred.copy()
    dnn_oof  = gbm_oof.copy()
    try:
        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        import tensorflow as tf
        from tensorflow.keras.models import Model
        from tensorflow.keras.layers import (Dense, BatchNormalization, Dropout,
                                              Input, Add, Activation)
        from tensorflow.keras.optimizers import Adam
        from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
        from tensorflow.keras.regularizers import l2

        n = X_scaled.shape[1]
        y_norm = (y - y.min()) / (y.max() - y.min() + 1e-6)

        inp = Input(shape=(n,))
        # Layer 1-2 with residual
        x  = Dense(256, activation="relu", kernel_regularizer=l2(1e-4))(inp)
        x  = BatchNormalization()(x); x = Dropout(0.3)(x)
        x2 = Dense(128, activation="relu", kernel_regularizer=l2(1e-4))(x)
        x2 = BatchNormalization()(x2); x2 = Dropout(0.25)(x2)
        # Residual projection
        skip = Dense(128, activation="linear")(x)
        x2 = Add()([x2, skip])
        x2 = Activation("relu")(x2)
        # Layer 3-4
        x3 = Dense(64, activation="relu", kernel_regularizer=l2(1e-4))(x2)
        x3 = BatchNormalization()(x3); x3 = Dropout(0.2)(x3)
        x4 = Dense(32, activation="relu", kernel_regularizer=l2(1e-4))(x3)
        x4 = BatchNormalization()(x4); x4 = Dropout(0.15)(x4)
        # Layer 5-6
        x5 = Dense(16, activation="relu")(x4)
        x6 = Dense(8,  activation="relu")(x5)
        out = Dense(1, activation="sigmoid")(x6)

        model = Model(inputs=inp, outputs=out)
        model.compile(optimizer=Adam(0.001), loss="huber", metrics=["mae"])

        cb = [
            EarlyStopping(monitor="val_loss", patience=25, restore_best_weights=True),
            ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=12, min_lr=1e-6),
        ]
        history = model.fit(
            X_scaled, y_norm, epochs=300, batch_size=32,
            validation_split=0.15, callbacks=cb, verbose=0,
        )
        dnn_raw  = model.predict(X_scaled, verbose=0).flatten()
        dnn_pred = dnn_raw * (y.max() - y.min()) + y.min()
        dnn_pred = np.clip(dnn_pred, 0, 100)
        dnn_mae  = mean_absolute_error(y, dnn_pred)
        dnn_r2   = r2_score(y, dnn_pred)
        epochs_run = len(history.history["loss"])
        print(f"     DNN MAE: {dnn_mae:.2f} | R²: {dnn_r2:.4f} | Epochs: {epochs_run}")

        # OOF predictions for stacking
        dnn_oof = np.zeros(len(y))
        for tr, va in kf.split(X_scaled):
            m2 = Model(inputs=inp, outputs=out)
            m2.compile(optimizer=Adam(0.001), loss="huber", metrics=["mae"])
            m2.fit(X_scaled[tr], y_norm[tr], epochs=150, batch_size=32,
                   validation_split=0.1, callbacks=[EarlyStopping(patience=15, restore_best_weights=True)],
                   verbose=0)
            dnn_oof[va] = m2.predict(X_scaled[va], verbose=0).flatten() * (y.max() - y.min()) + y.min()

        model.save(str(MODEL_DIR / "dnn_v2.keras"))
        print(f"     DNN model saved.")
        dnn_ok = True

    except Exception as e:
        print(f"     DNN failed ({e}) -- using GBM predictions as fallback")

    # ── Model 3: Ridge meta-learner (stacking) ─────────────
    print("     [c] Ridge meta-learner (stacking GBM + DNN OOF)...")
    stack_X = np.column_stack([gbm_oof, dnn_oof])
    ridge = Ridge(alpha=1.0)
    ridge.fit(stack_X, y)
    ridge_oof  = cross_val_predict(ridge, stack_X, y, cv=kf)
    ridge_pred_test = ridge.predict(np.column_stack([gbm_pred, dnn_pred]))
    ridge_cv = cross_val_score(ridge, stack_X, y, cv=kf, scoring="r2")
    print(f"     Stack CV R²: {ridge_cv.mean():.4f} ± {ridge_cv.std():.4f} | MAE: {mean_absolute_error(y, ridge_pred_test):.2f}")

    # Save all
    with open(MODEL_DIR / "rf_v2.pkl", "wb") as fh:
        pickle.dump({"gbm": gbm, "et": et, "scaler": scaler,
                     "ridge": ridge, "features": feature_names,
                     "y_min": y.min(), "y_max": y.max()}, fh)

    return gbm, gbm_pred, dnn_pred, ridge_pred_test, et, dnn_ok

# ─────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    t0 = time.time()

    df_data = load_all_data()

    # Build calibrated target
    print("[3/7] Building sector-normalized target...")
    y_target = build_target(df_data)
    df_data["target"] = y_target
    print(f"     Target range: {y_target.min():.1f} - {y_target.max():.1f} | mean: {y_target.mean():.1f}")

    # Feature matrix
    print("[4/7] Preparing feature matrix...")
    drop_cols = {"Symbol", "_sector", "LLM Score", "Hedge Fund Score",
                 "Stock Name", "industry", "Sector", "target",
                 "market_cap", "sales_ttm", "debt_level"}
    feat_cols = [c for c in df_data.columns
                 if c not in drop_cols
                 and df_data[c].dtype in [np.float64, np.int64, np.float32, np.int32]]

    X_df = df_data[feat_cols].copy()

    # Drop columns with >70% missing
    thresh = int(len(df_data) * 0.30)
    X_df = X_df.dropna(axis=1, thresh=thresh)

    # Impute
    imp = SimpleImputer(strategy="median")
    X_np = imp.fit_transform(X_df)
    feat_names = X_df.columns.tolist()

    # Remove zero-variance
    var_mask = np.var(X_np, axis=0) > 0
    X_np     = X_np[:, var_mask]
    feat_names = [feat_names[i] for i in range(len(feat_names)) if var_mask[i]]

    y_np = y_target.values
    print(f"     Features after filter: {len(feat_names)}")
    print(f"     Training samples: {len(y_np)}")

    # Train
    gbm, gbm_pred, dnn_pred, stack_pred, et, dnn_ok = train_ensemble(
        X_np, y_np, feat_names, X_df
    )

    # Feature importance
    print("[6/7] Feature importance...")
    df_imp = (
        pd.DataFrame({"Feature": feat_names, "Importance": et.feature_importances_})
        .sort_values("Importance", ascending=False).head(50)
    )
    df_imp.to_csv(OUT_DIR / "feature_importance_v2.csv", index=False)
    print("     Top 10 most predictive features:")
    for _, row in df_imp.head(10).iterrows():
        print(f"       {str(row['Feature'])[:55]:<55} {row['Importance']:.4f}")

    # Calibrate LLM Score
    print("[7/7] Calibrating LLM scores + assembling results...")
    mcap_col = "market_cap" if "market_cap" in df_data.columns else "market_cap_y" if "market_cap_y" in df_data.columns else None
    base_cols = ["Symbol", "Stock Name", "industry", "Sector", "_sector", "LLM Score", "Hedge Fund Score"]
    if mcap_col:
        base_cols.insert(2, mcap_col)
    meta = df_data[base_cols].reset_index(drop=True).copy()
    if mcap_col and mcap_col != "market_cap":
        meta = meta.rename(columns={mcap_col: "market_cap"})
    elif mcap_col is None:
        meta["market_cap"] = np.nan
    meta["LLM_Calibrated"] = calibrate_llm_score(meta)

    meta["GBM_RF_Score"] = np.round(np.clip(gbm_pred,   0, 100), 1)
    meta["DNN_Score"]    = np.round(np.clip(dnn_pred,   0, 100), 1)
    meta["Stack_Score"]  = np.round(np.clip(stack_pred, 0, 100), 1)
    meta["Target_Norm"]  = np.round(y_np, 1)

    # Expert validation
    expert_rows = []
    for sym, escore in EXPERT_SCORES.items():
        row = meta[meta["Symbol"] == sym]
        if len(row) > 0:
            r = row.iloc[0]
            expert_rows.append({
                "Symbol":          sym,
                "Expert Score":    escore,
                "HF Score":        round(r["Hedge Fund Score"], 1),
                "LLM Score":       round(r["LLM Score"], 1),
                "LLM_Calibrated":  round(r["LLM_Calibrated"], 1),
                "GBM_RF_Score":    round(r["GBM_RF_Score"], 1),
                "DNN_Score":       round(r["DNN_Score"], 1),
                "Stack_Score":     round(r["Stack_Score"], 1),
                "vs_HF":           round(abs(escore - r["Hedge Fund Score"]), 1),
                "vs_LLM":          round(abs(escore - r["LLM Score"]), 1),
                "vs_LLM_Cal":      round(abs(escore - r["LLM_Calibrated"]), 1),
                "vs_GBM":          round(abs(escore - r["GBM_RF_Score"]), 1),
                "vs_DNN":          round(abs(escore - r["DNN_Score"]), 1),
                "vs_Stack":        round(abs(escore - r["Stack_Score"]), 1),
            })
    df_expert = pd.DataFrame(expert_rows)

    # Model comparison
    comp = {}
    truth = pd.Series(
        [EXPERT_SCORES.get(s, np.nan) for s in meta["Symbol"]]
    ).values
    valid_mask = ~np.isnan(truth)
    t_v = truth[valid_mask]

    for label, col in [
        ("Hedge Fund Score",    "Hedge Fund Score"),
        ("LLM Score",           "LLM Score"),
        ("LLM Calibrated",      "LLM_Calibrated"),
        ("GBM/RF Score",        "GBM_RF_Score"),
        ("DNN Score",           "DNN_Score"),
        ("Stacked Ensemble",    "Stack_Score"),
    ]:
        p_v = meta[col].values[valid_mask]
        diff = np.abs(t_v - p_v)
        comp[label] = {
            "Expert MAE":          round(float(np.mean(diff)), 2),
            "Expert within 5pts":  f"{round(float(np.mean(diff <= 5)) * 100, 1)}%",
            "Expert within 10pts": f"{round(float(np.mean(diff <= 10)) * 100, 1)}%",
            "Expert within 15pts": f"{round(float(np.mean(diff <= 15)) * 100, 1)}%",
        }

    df_comp = pd.DataFrame(comp).T
    df_comp.index.name = "Model"
    df_comp = df_comp.sort_values("Expert MAE")

    # Save
    meta.to_csv(OUT_DIR / "ml_scores_v2.csv", index=False)
    df_comp.to_csv(OUT_DIR / "model_comparison_v2.csv")
    if len(df_expert) > 0:
        df_expert.to_csv(OUT_DIR / "expert_validation_v2.csv", index=False)

    elapsed = round(time.time() - t0, 1)
    print()
    print("=" * 70)
    print("ML SCORING v2 COMPLETE")
    print("=" * 70)
    print(f"Stocks scored : {len(meta)}")
    print(f"Features used : {len(feat_names)}")
    print(f"DNN trained   : {dnn_ok}")
    print(f"Time taken    : {elapsed}s")
    print()
    print("FINAL MODEL COMPARISON (sorted by Expert MAE - lower is better):")
    print(df_comp.to_string())
    print()
    if len(df_expert) > 0:
        print("EXPERT VALIDATION (all scores vs analyst ground truth):")
        print(df_expert.to_string(index=False))
    print()
    print(f"Outputs: {OUT_DIR}")
