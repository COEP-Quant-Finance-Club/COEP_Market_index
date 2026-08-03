#!/usr/bin/env python3
"""Diagnostic tool: correlation + PCA redundancy check across the fundamental
factors used by hedge_fund_scoring.

Important scope note: this is NOT a predictive feature‑selection tool. It has
no forward‑return target, so it cannot tell you which factors *predict*
outperformance — that requires historical price/return data (see the caveat
in ``score_industries.py``'s module docstring). What this answers is a narrower,
still genuinely useful question: are any of the ~30 fundamental signals
already used so highly correlated with each other that they're double‑counting
the same information inside a composite factor (e.g. debtor‑days trend and
inventory‑days trend both just tracking "the company got sloppier with working
capital")? That's a data‑hygiene check, not a return‑prediction check.

If/when you have historical price or forward‑return data per stock, a proper
factor‑validation module (Information Coefficient / IC‑IR analysis — the
standard practice quant desks actually use to validate factors) can be built as
a follow‑up; it needs a target column this repo doesn't currently have.

Usage:
    python factor_diagnostics.py --sector chemicals
    python factor_diagnostics.py --all-sectors
    python factor_diagnostics.py --all-sectors --corr-threshold 0.75
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# ``extract_features`` lives in ``score_industries.py`` within the same package.
from score_industries import extract_features

# Ratio‑style columns only; deliberately excludes absolute‑currency levels
# (borrowings, reserves, equity_capital, annual_profit, total_assets, etc.)
# since those aren't comparable across companies of different sizes and would
# dominate a correlation/PCA analysis purely on scale, not information content.
RATIO_COLUMNS = [
    "sales_growth_3y",
    "profit_growth_3y",
    "roce",
    "roe",
    "opm",
    "pe",
    "cfo_op",
    "debt_to_equity",
    "cash_profit_conversion",
    "interest_coverage",
    "peg",
    "sales_history_trend",
    "quarterly_sales_trend",
    "quarterly_profit_trend",
    "quarterly_margin_trend",
    "sales_stability",
    "profit_consistency",
    "margin_trend",
    "roce_trend",
    "cfo_consistency",
    "cashflow_stability",
    "debt_trend",
    "equity_dilution",
    "promoter_change",
    "institutional_change",
    "working_capital_trend",
    "debtor_days_trend",
    "inventory_days_trend",
    "roa",
    "asset_turnover",
    "retained_earnings_to_assets",
    "ebit_to_assets",
    "equity_to_liabilities",
]


def load_features(input_dir: Path, sectors: list[str]) -> pd.DataFrame:
    """Load *enhanced* CSVs for the given sectors and return a concatenated DataFrame.

    Each sector CSV is expected to contain the raw financial columns. We run
    ``extract_features`` (the deterministic feature extraction used for scoring)
    and tag each row with its sector name.
    """
    frames: list[pd.DataFrame] = []
    for sector in sectors:
        path = input_dir / f"{sector}_enhanced.csv"
        if not path.exists():
            print(f"skip: {path.name} not found")
            continue
        raw = pd.read_csv(path, low_memory=False)
        feats = extract_features(raw).assign(sector=sector)
        frames.append(feats)
    if not frames:
        raise FileNotFoundError("No matching *_enhanced.csv files found.")
    return pd.concat(frames, ignore_index=True)


def correlation_report(features: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Return a DataFrame of feature pairs whose absolute Pearson correlation >= threshold.
    """
    cols = [c for c in RATIO_COLUMNS if c in features.columns]
    corr = features[cols].corr(min_periods=10)
    pairs: list[dict] = []
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            value = corr.loc[a, b]
            if pd.notna(value) and abs(value) >= threshold:
                pairs.append({"feature_a": a, "feature_b": b, "correlation": round(float(value), 3)})
    if not pairs:
        return pd.DataFrame(columns=["feature_a", "feature_b", "correlation"])
    return pd.DataFrame(pairs).sort_values("correlation", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


def pca_report(features: pd.DataFrame, n_components: int = 5) -> pd.DataFrame:
    """Perform a simple PCA (eigen‑decomposition of the covariance matrix) and report the top components.

    The data are median‑imputed, centred and standardised before computing the
    covariance matrix. Only columns with non‑zero variance are kept.
    """
    cols = [c for c in RATIO_COLUMNS if c in features.columns]
    data = features[cols].copy()
    # Median‑impute – consistent with the rest of the pipeline treating "missing" as "neutral".
    data = data.fillna(data.median(numeric_only=True))
    data = data.loc[:, data.std(numeric_only=True) > 1e-9]
    if data.shape[1] < 2 or data.shape[0] < 10:
        return pd.DataFrame()
    standardized = (data - data.mean()) / data.std()
    cov = np.cov(standardized.to_numpy(), rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    total_var = eigvals.sum()
    rows: list[dict] = []
    for i in range(min(n_components, len(eigvals))):
        loadings = dict(zip(data.columns, eigvecs[:, i]))
        top = sorted(loadings.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        rows.append(
            {
                "component": f"PC{i + 1}",
                "variance_explained_pct": round(100 * eigvals[i] / total_var, 1),
                "top_loadings": ", ".join(f"{name} ({weight:+.2f})" for name, weight in top),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input-dir", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--sector", type=str, default=None, help="Single sector, e.g. chemicals (matches chemicals_enhanced.csv)")
    parser.add_argument("--all-sectors", action="store_true", help="Pool every *_enhanced.csv together")
    parser.add_argument("--corr-threshold", type=float, default=0.8)
    args = parser.parse_args()

    if args.all_sectors:
        sectors = [p.stem.removesuffix("_enhanced") for p in sorted(args.input_dir.glob("*_enhanced.csv"))]
    elif args.sector:
        sectors = [args.sector]
    else:
        parser.error("Pass --sector NAME or --all-sectors")

    features = load_features(args.input_dir, sectors)
    print(f"Pooled {len(features)} companies across {len(sectors)} sector file(s).\n")

    print(f"=== Highly correlated factor pairs (|r| >= {args.corr_threshold}) ===")
    corr_df = correlation_report(features, args.corr_threshold)
    if corr_df.empty:
        print("None found at this threshold — no obvious redundancy.")
    else:
        print(corr_df.to_string(index=False))
        print("\nIf two signals are near‑duplicates, they're each getting counted")
        print("separately inside their factor bucket's average — effectively")
        print("double‑weighting one underlying idea. Consider dropping one or")
        print("giving the pair a combined weight in score_sector().")

    print("\n=== PCA: top components and their loadings ===")
    pca_df = pca_report(features)
    if pca_df.empty:
        print("Not enough data/variance to run PCA.")
    else:
        print(pca_df.to_string(index=False))
        print("\nA component with several large same‑sign loadings is one")
        print("underlying idea (e.g. 'overall quality') spread across several")
        print("raw columns — useful context, not a verdict on any single stock.")


if __name__ == "__main__":
    main()
