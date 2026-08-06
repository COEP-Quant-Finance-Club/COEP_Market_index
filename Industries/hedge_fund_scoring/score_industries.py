"""Sector-relative hedge-fund-style scoring for the Industries CSV universe.

The numeric score is deterministic and peer-relative. An optional LLM
review can refine the written rationale and apply a tightly limited adjustment.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from openai import OpenAI

LOG = logging.getLogger(__name__)

# =============================================================================
# DEFAULT CONFIGURATIONS & GLOBALS
# (These act as fallbacks if scoring_config.json is missing or incomplete)
# =============================================================================

FINANCIAL_SECTORS = {
    "banking", "financial_services", "financial_infrastructure",
    "Banks", "NBFC", "Insurance", "Financial Services"
}
ASSET_HEAVY_SECTORS = {
    "infrastructure", "power_and_utilities", "real_estate", "telecom_infra",
    "telecommunications", "oil_gas_utilities", "capital_goods", "renewable_energy",
    "Oil & Gas", "Power", "Metals", "Telecom", "Infrastructure", "Cement"
}

WEIGHTS = {
    "standard": {
        "growth": 0.18, "profitability": 0.22, "balance_sheet": 0.15,
        "cash_generation": 0.13, "valuation": 0.12, "ownership": 0.08,
        "quality": 0.12
    },
    "financial": {
        "growth": 0.17, "profitability": 0.27, "balance_sheet": 0.04,
        "cash_generation": 0.07, "valuation": 0.22, "ownership": 0.10,
        "quality": 0.13
    },
    "asset_heavy": {
        "growth": 0.17, "profitability": 0.19, "balance_sheet": 0.09,
        "cash_generation": 0.19, "valuation": 0.14, "ownership": 0.10,
        "quality": 0.12
    }
}

SCORE_SPREAD_MULTIPLIER = 1.75
LLM_MAX_ADJUSTMENT = 15.0

RED_FLAG_THRESHOLDS = {
    "min_interest_coverage": 1.0,
    "min_profit_consistency": 0.4,
    "max_dilution_with_weak_returns": 0.15,
    "weak_roce_threshold": 10.0,
}
RED_FLAG_CAPS = {
    "negative_net_worth": 15.0,
    "cannot_cover_interest": 25.0,
    "chronic_losses": 30.0,
    "dilution_with_weak_returns": 35.0,
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def load_local_env(env_path: Path) -> None:
    """Load environment variables from a local .env file if it exists."""
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
            LOG.info("Loaded environment from %s", env_path)
        except ImportError:
            LOG.warning("python-dotenv not installed. Skipping .env file load.")

def load_config(config_path: Path) -> None:
    """Load custom weights and sectors from a JSON config if available."""
    global FINANCIAL_SECTORS, ASSET_HEAVY_SECTORS, WEIGHTS, SCORE_SPREAD_MULTIPLIER, LLM_MAX_ADJUSTMENT
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "FINANCIAL_SECTORS" in data:
                FINANCIAL_SECTORS = set(data["FINANCIAL_SECTORS"])
            if "ASSET_HEAVY_SECTORS" in data:
                ASSET_HEAVY_SECTORS = set(data["ASSET_HEAVY_SECTORS"])
            if "WEIGHTS" in data:
                WEIGHTS = data["WEIGHTS"]
            if "SCORE_SPREAD_MULTIPLIER" in data:
                SCORE_SPREAD_MULTIPLIER = float(data["SCORE_SPREAD_MULTIPLIER"])
            if "LLM_MAX_ADJUSTMENT" in data:
                LLM_MAX_ADJUSTMENT = float(data["LLM_MAX_ADJUSTMENT"])
            LOG.info("Successfully loaded scoring configuration from %s", config_path.name)
        except Exception as e:
            LOG.warning("Failed to parse config %s: %s. Using default settings.", config_path.name, e)

# =============================================================================
# PARSING AND DETERMINISTIC FEATURE ENGINEERING
# =============================================================================

def parse_number(value: Any) -> float:
    if isinstance(value, (int, float, np.number)):
        return float(value)
    text = (str(value).strip().replace(",", "").replace("₹", "")
            .replace("−", "-").replace("–", "-").replace("—", "-"))
    if text in {"", "-", "NA", "N/A", "nan", "None"}:
        return np.nan
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("Cr.", "").replace("Cr", "").replace("%", "")
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return np.nan
    number = float(match.group())
    return -abs(number) if negative else number


def last_matching_value(frame: pd.DataFrame, starts_with: str, prefer_ttm: bool = True) -> pd.Series:
    cols = [c for c in frame.columns if c.startswith(starts_with)]
    if not cols:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    if prefer_ttm:
        ttm = [c for c in cols if c.endswith("TTM")]
        if ttm:
            values = frame[ttm].apply(lambda s: s.map(parse_number)).bfill(axis=1).iloc[:, 0]
            if values.notna().any():
                return values
    def sort_key(col: str) -> tuple[int, int]:
        m = re.search(r"(Mar|Jun|Sep|Dec)\s+(\d{4})$", col)
        if not m:
            return (0, 0)
        return (int(m.group(2)), {"Mar": 3, "Jun": 6, "Sep": 9, "Dec": 12}[m.group(1)])
    ordered = sorted(cols, key=sort_key, reverse=True)
    return frame[ordered].apply(lambda s: s.map(parse_number)).bfill(axis=1).iloc[:, 0]


def value_for(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return frame[column].map(parse_number)


def history_stats(frame: pd.DataFrame, starts_with: str) -> pd.DataFrame:
    cols = [c for c in frame.columns if c.startswith(starts_with) and not c.endswith("TTM")]
    def key(col: str) -> tuple[int, int]:
        m = re.search(r"(Mar|Jun|Sep|Dec)\s+(\d{4})$", col)
        return (int(m.group(2)), {"Mar": 3, "Jun": 6, "Sep": 9, "Dec": 12}[m.group(1)]) if m else (0, 0)
    cols = sorted(cols, key=key)
    result = pd.DataFrame(index=frame.index, columns=["periods", "trend", "recent_change", "positive_share", "stability"], dtype=float)
    if not cols:
        return result
    numbers = frame[cols].apply(lambda s: s.map(parse_number))
    for index, row in numbers.iterrows():
        values = row.dropna().to_numpy(dtype=float)
        result.at[index, "periods"] = len(values)
        if len(values) >= 2:
            result.at[index, "recent_change"] = values[-1] - values[-2]
            if values[0] > 0 and values[-1] > 0:
                result.at[index, "trend"] = (values[-1] / values[0]) ** (1 / (len(values) - 1)) - 1
        if len(values):
            result.at[index, "positive_share"] = float((values > 0).mean())
        if len(values) >= 3 and abs(values.mean()) > 1e-9:
            result.at[index, "stability"] = -float(np.std(values) / abs(values.mean()))
    return result


def quarterly_turnaround_stats(frame: pd.DataFrame) -> pd.DataFrame:
    """Analyze recent quarterly results for Loss-to-Profit turnarounds and acceleration."""
    profit_cols = [c for c in frame.columns if c.startswith("Quarterly Results - Net Profit -") and not c.endswith("TTM")]
    def key(col: str) -> tuple[int, int]:
        m = re.search(r"(Mar|Jun|Sep|Dec)\s+(\d{4})$", col)
        return (int(m.group(2)), {"Mar": 3, "Jun": 6, "Sep": 9, "Dec": 12}[m.group(1)]) if m else (0, 0)
    profit_cols = sorted(profit_cols, key=key)

    result = pd.DataFrame(index=frame.index, columns=["latest_q_profit", "prev_q_profit", "turnaround_score", "is_turnaround"], dtype=float)
    if not profit_cols:
        return result

    numbers = frame[profit_cols].apply(lambda s: s.map(parse_number))
    for index, row in numbers.iterrows():
        vals = row.dropna().to_numpy(dtype=float)
        if len(vals) >= 2:
            latest = vals[-1]
            prev = vals[-2]
            result.at[index, "latest_q_profit"] = latest
            result.at[index, "prev_q_profit"] = prev
            # Turnaround: previous quarter was negative or zero, latest quarter turned positive!
            if prev <= 0 and latest > 0:
                result.at[index, "is_turnaround"] = 1.0
                result.at[index, "turnaround_score"] = float(latest - prev)
            elif len(vals) >= 3 and vals[-3] <= 0 and latest > 0:
                result.at[index, "is_turnaround"] = 1.0
                result.at[index, "turnaround_score"] = float(latest - vals[-3])
            else:
                result.at[index, "is_turnaround"] = 0.0
                result.at[index, "turnaround_score"] = 0.0
    return result


def extract_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    out["sales_growth_3y"] = value_for(frame, "Profit & Loss - Compounded Sales Growth - 3 Years")
    out["profit_growth_3y"] = value_for(frame, "Profit & Loss - Compounded Profit Growth - 3 Years")
    out["roce"] = value_for(frame, "Key Metrics - ROCE")
    out["roe"] = value_for(frame, "Key Metrics - ROE")
    out["opm"] = last_matching_value(frame, "Profit & Loss - OPM %")
    out["pe"] = value_for(frame, "Key Metrics - Stock P/E")
    out["cfo_op"] = last_matching_value(frame, "Cash Flows - CFO/OP")
    out["free_cash_flow"] = last_matching_value(frame, "Cash Flows - Free Cash Flow", prefer_ttm=False)
    out["borrowings"] = last_matching_value(frame, "Balance Sheet - Borrowings", prefer_ttm=False)
    out["reserves"] = last_matching_value(frame, "Balance Sheet - Reserves", prefer_ttm=False)
    out["equity_capital"] = last_matching_value(frame, "Balance Sheet - Equity Capital", prefer_ttm=False)
    out["promoters"] = last_matching_value(frame, "Shareholding Pattern (Quarterly) - Promoters", prefer_ttm=False)
    out["annual_profit"] = last_matching_value(frame, "Profit & Loss - Net Profit", prefer_ttm=False)
    out["annual_cfo"] = last_matching_value(frame, "Cash Flows - Cash from Operating Activity", prefer_ttm=False)
    out["operating_profit"] = last_matching_value(frame, "Profit & Loss - Operating Profit", prefer_ttm=False)
    out["interest"] = last_matching_value(frame, "Profit & Loss - Interest", prefer_ttm=False)
    out["total_assets"] = last_matching_value(frame, "Balance Sheet - Total Assets", prefer_ttm=False)
    out["total_liabilities"] = last_matching_value(frame, "Balance Sheet - Total Liabilities", prefer_ttm=False)
    
    # Market Cap & Size Factor
    mcap = value_for(frame, "market_cap").fillna(value_for(frame, "Key Metrics - Market Cap"))
    out["market_cap_log"] = np.log1p(mcap.clip(lower=0))
    
    annual_sales = last_matching_value(frame, "Profit & Loss - Sales", prefer_ttm=False)
    pbt = last_matching_value(frame, "Profit & Loss - Profit before tax", prefer_ttm=False)

    equity = out["reserves"] + out["equity_capital"]
    assets_safe = out["total_assets"].where(out["total_assets"].abs() > 1)
    liab_safe = out["total_liabilities"].where(out["total_liabilities"].abs() > 1)
    sales_safe = annual_sales.where(annual_sales.abs() > 1)

    out["debt_to_equity"] = out["borrowings"] / equity.where(equity.abs() > 1)
    out["cash_profit_conversion"] = out["annual_cfo"] / out["annual_profit"].where(out["annual_profit"].abs() > 1)
    out["interest_coverage"] = out["operating_profit"] / out["interest"].where(out["interest"] > 0)
    out["asset_turnover"] = annual_sales / assets_safe
    out["roa"] = out["annual_profit"] / assets_safe
    out["retained_earnings_to_assets"] = out["reserves"] / assets_safe
    out["ebit_to_assets"] = out["operating_profit"] / assets_safe
    out["equity_to_liabilities"] = equity / liab_safe
    
    # Institutional Enhancements: Sloan Accruals & 5-Factor DuPont Components
    out["sloan_accruals"] = (out["annual_profit"] - out["annual_cfo"]) / assets_safe
    out["dupont_tax_efficiency"] = out["annual_profit"] / pbt.where(pbt.abs() > 1)
    out["dupont_interest_burden"] = pbt / out["operating_profit"].where(out["operating_profit"].abs() > 1)
    out["dupont_ebit_margin"] = out["operating_profit"] / sales_safe
    out["dupont_leverage"] = assets_safe / equity.where(equity.abs() > 1)

    growth_base = pd.concat([out["sales_growth_3y"], out["profit_growth_3y"]], axis=1).mean(axis=1).where(lambda s: s > 0)
    out["peg"] = out["pe"] / growth_base.where(growth_base > .1)
    
    sales = history_stats(frame, "Profit & Loss - Sales")
    quarterly_sales = history_stats(frame, "Quarterly Results - Sales")
    quarterly_profit = history_stats(frame, "Quarterly Results - Net Profit")
    quarterly_opm = history_stats(frame, "Quarterly Results - OPM %")
    profits = history_stats(frame, "Profit & Loss - Net Profit")
    margins = history_stats(frame, "Profit & Loss - OPM %")
    roce_history = history_stats(frame, "Ratios - ROCE %")
    cfo = history_stats(frame, "Cash Flows - Cash from Operating Activity")
    debt = history_stats(frame, "Balance Sheet - Borrowings")
    capital = history_stats(frame, "Balance Sheet - Equity Capital")
    promoter_history = history_stats(frame, "Shareholding Pattern (Quarterly) - Promoters")
    fii_history = history_stats(frame, "Shareholding Pattern (Quarterly) - FIIs")
    dii_history = history_stats(frame, "Shareholding Pattern (Quarterly) - DIIs")
    working_capital = history_stats(frame, "Ratios - Working Capital Days")
    debtor_days = history_stats(frame, "Ratios - Debtor Days")
    inventory_days = history_stats(frame, "Ratios - Inventory Days")
    
    # Turnaround & Quarterly Acceleration
    q_turnaround = quarterly_turnaround_stats(frame)
    out["is_turnaround"] = q_turnaround["is_turnaround"]
    out["turnaround_score"] = q_turnaround["turnaround_score"]

    out["history_periods"] = sales["periods"]
    out["sales_history_trend"] = sales["trend"]
    out["quarterly_sales_trend"] = quarterly_sales["trend"]
    out["quarterly_profit_trend"] = quarterly_profit["trend"]
    out["quarterly_margin_trend"] = quarterly_opm["recent_change"]
    out["sales_stability"] = sales["stability"]
    out["profit_consistency"] = profits["positive_share"]
    out["margin_trend"] = margins["recent_change"]
    out["roce_trend"] = roce_history["recent_change"]
    out["cfo_consistency"] = cfo["positive_share"]
    out["cashflow_stability"] = cfo["stability"]
    out["debt_trend"] = debt["trend"]
    out["equity_dilution"] = capital["trend"]
    out["promoter_change"] = promoter_history["recent_change"]
    out["institutional_change"] = fii_history["recent_change"].fillna(0) + dii_history["recent_change"].fillna(0)
    out["working_capital_trend"] = working_capital["recent_change"]
    out["debtor_days_trend"] = debtor_days["recent_change"]
    out["inventory_days_trend"] = inventory_days["recent_change"]
    return out.replace([np.inf, -np.inf], np.nan)


def peer_percentile(series: pd.Series, higher_is_better: bool, group_size: int) -> pd.Series:
    clean = series.clip(series.quantile(.02), series.quantile(.98)) if series.notna().any() else series
    pct = clean.rank(pct=True, ascending=higher_is_better)
    shrink = min(1.0, group_size / 15.0)
    return (50.0 + (pct - .5) * 100.0 * shrink).fillna(50.0)


def bool_flag(condition: pd.Series, *required: pd.Series) -> pd.Series:
    if required:
        valid = pd.concat(list(required), axis=1).notna().all(axis=1)
    else:
        valid = pd.Series(True, index=condition.index)
    return pd.Series(np.where(valid, condition.astype(float), np.nan), index=condition.index)


def quality_flags(features: pd.DataFrame, profile: str) -> pd.DataFrame:
    flags = pd.DataFrame(index=features.index)
    flags["profitable"] = bool_flag((features.roce > 0) & (features.roe > 0), features.roce, features.roe)
    flags["consistent_profits"] = bool_flag(features.profit_consistency >= .75, features.profit_consistency)
    flags["margin_improving"] = bool_flag(features.margin_trend > 0, features.margin_trend)
    flags["returns_improving"] = bool_flag(features.roce_trend > 0, features.roce_trend)
    flags["earnings_cash_backed"] = bool_flag(features.cash_profit_conversion >= .8, features.cash_profit_conversion)
    flags["low_accruals"] = bool_flag(features.sloan_accruals <= 0.05, features.sloan_accruals)
    flags["no_heavy_dilution"] = bool_flag(features.equity_dilution <= .05, features.equity_dilution)
    flags["promoters_not_exiting"] = bool_flag(features.promoter_change >= -1.0, features.promoter_change)
    flags["no_wc_trap"] = bool_flag((features.working_capital_trend <= 15) | (features.sales_history_trend > 0.05), features.working_capital_trend)
    flags["turnaround_company"] = bool_flag(features.is_turnaround == 1.0, features.is_turnaround)
    if profile != "financial":
        flags["deleveraging_or_low_debt"] = bool_flag(
            (features.debt_trend <= 0) | (features.debt_to_equity <= .5),
            features.debt_trend, features.debt_to_equity,
        )
        flags["interest_safe"] = bool_flag(features.interest_coverage >= 3, features.interest_coverage)
    return flags


def evaluate_red_flags(features: pd.DataFrame, profile: str) -> list[tuple[str, pd.Series, float]]:
    equity = features.reserves + features.equity_capital
    altman_proxy = (1.2 * features.ebit_to_assets.fillna(0) + 
                    1.4 * features.retained_earnings_to_assets.fillna(0) + 
                    0.6 * features.equity_to_liabilities.fillna(0))

    flags: list[tuple[str, pd.Series, float]] = [
        ("negative net worth", equity < 0, RED_FLAG_CAPS["negative_net_worth"]),
        ("losses in most reported periods",
         features.profit_consistency < RED_FLAG_THRESHOLDS["min_profit_consistency"],
         RED_FLAG_CAPS["chronic_losses"]),
        ("chronic negative operating cash flow",
         features.cfo_consistency < 0.3,
         20.0),
        ("financial distress zone (Altman risk)",
         altman_proxy < 0.3,
         20.0),
        ("debt trap (high leverage with negative free cash flow)",
         (features.debt_to_equity > 1.5) & (features.free_cash_flow < 0),
         25.0),
        ("double working capital drain (debtor & inventory bloat)",
         (features.debtor_days_trend > 15) & (features.inventory_days_trend > 15),
         30.0),
        ("promoters dumping stake",
         features.promoter_change < -3.0,
         35.0),
        ("heavy dilution with weak returns",
         (features.equity_dilution > RED_FLAG_THRESHOLDS["max_dilution_with_weak_returns"]) &
         (features.roce < RED_FLAG_THRESHOLDS["weak_roce_threshold"]),
         RED_FLAG_CAPS["dilution_with_weak_returns"]),
        ("working capital bloat with weak sales",
         (features.working_capital_trend > 30) & (features.sales_history_trend <= 0),
         35.0),
    ]
    if profile != "financial":
        flags.insert(1, (
            "can't cover interest from operating profit",
            features.interest_coverage < RED_FLAG_THRESHOLDS["min_interest_coverage"],
            RED_FLAG_CAPS["cannot_cover_interest"],
        ))
    return flags


def apply_red_flags(score: pd.Series, features: pd.DataFrame, profile: str) -> tuple[pd.Series, pd.Series]:
    capped = score.copy()
    labels = pd.Series([[] for _ in range(len(score))], index=score.index, dtype=object)
    for label, condition, cap in evaluate_red_flags(features, profile):
        triggered = condition.fillna(False)
        if not triggered.any():
            continue
        cap_series = pd.Series(np.where(triggered, cap, np.inf), index=score.index)
        capped = np.minimum(capped, cap_series)
        for idx in triggered[triggered].index:
            labels.at[idx] = labels.at[idx] + [label]
    return capped, labels


def score_sector(frame: pd.DataFrame, sector: str) -> pd.DataFrame:
    features = extract_features(frame)
    n = len(frame)
    profile = "financial" if sector in FINANCIAL_SECTORS else "asset_heavy" if sector in ASSET_HEAVY_SECTORS else "standard"

    peer = pd.DataFrame(index=frame.index)
    peer["growth"] = (peer_percentile(features.sales_growth_3y, True, n) + peer_percentile(features.profit_growth_3y, True, n) +
                      peer_percentile(features.sales_history_trend, True, n) + peer_percentile(features.quarterly_sales_trend, True, n) +
                      peer_percentile(features.quarterly_profit_trend, True, n) + peer_percentile(features.sales_stability, True, n) +
                      peer_percentile(features.turnaround_score, True, n)) / 7
    peer["profitability"] = (peer_percentile(features.roce, True, n) + peer_percentile(features.roe, True, n) +
                             peer_percentile(features.opm, True, n) + peer_percentile(features.profit_consistency, True, n) +
                             peer_percentile(features.margin_trend, True, n) + peer_percentile(features.roce_trend, True, n) +
                             peer_percentile(features.cash_profit_conversion, True, n) + peer_percentile(features.quarterly_margin_trend, True, n) +
                             peer_percentile(features.roa, True, n) + peer_percentile(features.asset_turnover, True, n) +
                             peer_percentile(features.sloan_accruals, False, n) + peer_percentile(features.dupont_ebit_margin, True, n) +
                             peer_percentile(features.dupont_tax_efficiency, True, n)) / 13
    peer["balance_sheet"] = (peer_percentile(features.debt_to_equity, False, n) + peer_percentile(features.debt_trend, False, n) +
                             peer_percentile(features.interest_coverage, True, n) + peer_percentile(features.working_capital_trend, False, n) +
                             peer_percentile(features.debtor_days_trend, False, n) + peer_percentile(features.inventory_days_trend, False, n) +
                             peer_percentile(features.retained_earnings_to_assets, True, n) + peer_percentile(features.ebit_to_assets, True, n) +
                             peer_percentile(features.equity_to_liabilities, True, n) + peer_percentile(features.dupont_interest_burden, True, n)) / 10
    peer["cash_generation"] = (peer_percentile(features.cfo_op, True, n) + peer_percentile(features.free_cash_flow, True, n) +
                                peer_percentile(features.cfo_consistency, True, n) + peer_percentile(features.cashflow_stability, True, n)) / 4
    
    positive_pe = features.pe.where(features.pe > 0)
    peer["valuation"] = (peer_percentile(positive_pe, False, n) + peer_percentile(features.peg.where(features.peg > 0), False, n)) / 2
    peer.loc[features.pe <= 0, "valuation"] = 25.0
    peer["ownership"] = (peer_percentile(features.promoters, True, n) + peer_percentile(features.promoter_change, True, n) +
                         peer_percentile(features.equity_dilution, False, n) + peer_percentile(features.institutional_change, True, n) +
                         peer_percentile(features.market_cap_log, True, n)) / 5

    quality_score = quality_flags(features, profile).mean(axis=1, skipna=True) * 100
    peer["quality"] = peer_percentile(quality_score, True, n)

    weights = WEIGHTS[profile]
    score = sum(peer[key] * weight for key, weight in weights.items())
    
    available = features.notna().sum(axis=1)
    history_confidence = (features.history_periods.fillna(0) / 5).clip(.35, 1)
    confidence = ((available / len(features.columns)).clip(.35, 1) * history_confidence).pow(.5)
    evidence_adjusted_score = 50 + (score - 50) * confidence
    score = (50 + (evidence_adjusted_score - 50) * SCORE_SPREAD_MULTIPLIER).clip(0, 100)
    
    score, red_flag_labels = apply_red_flags(score, features, profile)

    result = frame.copy()
    result["Hedge Fund Score"] = score.round(1)
    result["LLM Score"] = np.nan
    result["Combined Score"] = result["Hedge Fund Score"]
    result["Pros"] = [deterministic_pros(peer.loc[i], int(features.history_periods.loc[i]) if pd.notna(features.history_periods.loc[i]) else 0, bool(features.is_turnaround.loc[i] == 1.0))
                      for i in result.index]
    result["Cons"] = [format_cons(deterministic_cons(peer.loc[i], int(features.history_periods.loc[i]) if pd.notna(features.history_periods.loc[i]) else 0),
                                   red_flag_labels.loc[i])
                      for i in result.index]
    result["Peer Context"] = [peer_context(peer.loc[i], profile, n) for i in result.index]
    return result


def deterministic_pros(peer: pd.Series, periods: int, is_turnaround: bool = False) -> str:
    strengths = [name.replace("_", " ") for name, value in peer.sort_values(ascending=False).items() if value >= 60]
    if is_turnaround:
        strengths.insert(0, "recent loss-to-profit turnaround")
    text = "; ".join(strengths[:3]) or "No decisive peer-relative strength identified"
    return text + ("; early-stage company, current signals are promising but unseasoned" if periods < 3 and strengths else "")


def deterministic_cons(peer: pd.Series, periods: int) -> str:
    risks = [name.replace("_", " ") for name, value in peer.sort_values().items() if value <= 40]
    text = "; ".join(risks[:3]) or "No material peer-relative weakness identified"
    return text + ("; newly listed/short financial history—lower evidence confidence" if periods < 3 else "")


def format_cons(base_cons: str, red_flags: list[str]) -> str:
    if not red_flags:
        return base_cons
    return f"RED FLAG: {'; '.join(red_flags)}. {base_cons}"


def peer_context(peer: pd.Series, profile: str, peers: int) -> str:
    best = peer.sort_values(ascending=False).head(2)
    worst = peer.sort_values().head(2)
    return (f"{profile} peer model; n={peers}. Above peers: " +
            ", ".join(f"{k.replace('_', ' ')} ({v:.0f}/100)" for k, v in best.items()) +
            "; below peers: " +
            ", ".join(f"{k.replace('_', ' ')} ({v:.0f}/100)" for k, v in worst.items()))


def compact_record(row: pd.Series, max_fields: int) -> dict[str, Any]:
    """Return a dictionary of selected raw fields for the LLM."""
    values = [(str(k), str(v)) for k, v in row.items() if pd.notna(v) and str(v).strip()]
    priority = [x for x in values if any(t in x[0] for t in ("Stock Name", "Symbol", "market_cap", "Key Metrics", "TTM", "Jun 2026", "Mar 2026"))]
    remainder = [x for x in values if x not in priority]
    if max_fields == 0:
        chosen = priority + remainder
    else:
        chosen = (priority + remainder)[:max_fields]
    return dict(chosen)

def list_all_columns(df: pd.DataFrame) -> list[str]:
    """Return a list of *all* column names present in the supplied DataFrame."""
    cols = list(df.columns)
    LOG.info("Available columns (%d): %s", len(cols), ", ".join(cols))
    return cols

# =============================================================================
# LLM FEATURE SELECTION
# =============================================================================

def _correlation_features(df: pd.DataFrame, target_column: str, n: int) -> list[str]:
    numeric = df.select_dtypes(include=[np.number]).drop(columns=[target_column], errors="ignore")
    if numeric.empty:
        LOG.warning("No numeric columns available for correlation‑based feature selection.")
        return []
    corrs = numeric.apply(lambda s: s.corr(df[target_column])).abs()
    return corrs.sort_values(ascending=False).head(n).index.tolist()

def _mutual_info_features(df: pd.DataFrame, target_column: str, n: int) -> list[str]:
    try:
        from sklearn.feature_selection import mutual_info_regression
    except ImportError as exc:
        LOG.warning("sklearn not available for mutual information: %s – falling back to correlation.", exc)
        return _correlation_features(df, target_column, n)

    X = df.select_dtypes(include=[np.number]).drop(columns=[target_column], errors="ignore")
    y = df[target_column]
    if X.empty:
        LOG.warning("No numeric columns available for mutual‑information feature selection.")
        return []
    mi = mutual_info_regression(X, y, random_state=0)
    mi_series = pd.Series(mi, index=X.columns)
    return mi_series.sort_values(ascending=False).head(n).index.tolist()

def _random_forest_features(df: pd.DataFrame, target_column: str, n: int) -> list[str]:
    try:
        from sklearn.ensemble import RandomForestRegressor
    except ImportError as exc:
        LOG.warning("sklearn not available for RandomForest importance: %s – falling back to correlation.", exc)
        return _correlation_features(df, target_column, n)

    X = df.select_dtypes(include=[np.number]).drop(columns=[target_column], errors="ignore")
    y = df[target_column]
    if X.empty:
        LOG.warning("No numeric columns available for RandomForest feature selection.")
        return []
    model = RandomForestRegressor(n_estimators=200, random_state=0, n_jobs=-1)
    model.fit(X, y)
    importances = pd.Series(model.feature_importances_, index=X.columns)
    return importances.sort_values(ascending=False).head(n).index.tolist()

def _lasso_features(df: pd.DataFrame, target_column: str, n: int) -> list[str]:
    try:
        from sklearn.linear_model import LassoCV
    except ImportError as exc:
        LOG.warning("sklearn not available for Lasso feature selection: %s – falling back to correlation.", exc)
        return _correlation_features(df, target_column, n)

    X = df.select_dtypes(include=[np.number]).drop(columns=[target_column], errors="ignore")
    y = df[target_column]
    if X.empty:
        LOG.warning("No numeric columns available for Lasso feature selection.")
        return []
    lasso = LassoCV(cv=5, random_state=0, max_iter=5000)
    lasso.fit(X, y)
    coef = pd.Series(lasso.coef_, index=X.columns)
    nonzero = coef[coef != 0].abs().sort_values(ascending=False)
    if nonzero.empty:
        LOG.warning("Lasso yielded all zero coefficients; falling back to correlation.")
        return _correlation_features(df, target_column, n)
    return nonzero.head(n).index.tolist()

def select_top_features(
    df: pd.DataFrame,
    target_column: str = "Hedge Fund Score",
    n: int = 100,
    method: str = "correlation",
) -> list[str]:
    """Select the ``n`` most predictive numeric columns for ``target_column``."""
    if target_column not in df.columns:
        LOG.warning("Target column %s not found for feature selection.", target_column)
        return []

    method = method.lower()
    if method == "correlation":
        top = _correlation_features(df, target_column, n)
    elif method == "mutual_info":
        top = _mutual_info_features(df, target_column, n)
    elif method == "random_forest":
        top = _random_forest_features(df, target_column, n)
    elif method == "lasso":
        top = _lasso_features(df, target_column, n)
    else:
        LOG.warning("Unknown feature‑selection method '%s'; falling back to correlation.", method)
        top = _correlation_features(df, target_column, n)

    LOG.info("Selected top %d features using %s for %s.", len(top), method, target_column)
    return top

def compact_record_with_features(row: pd.Series, features: list[str]) -> dict[str, Any]:
    out = {}
    for col in features:
        if col in row and pd.notna(row[col]):
            out[str(col)] = str(row[col])
    return out


def merge_existing_llm_scores(current_scored: pd.DataFrame, output_dir: Path, sector: str) -> pd.DataFrame:
    """Merge previously calculated LLM scores to avoid redundant API calls upon restart."""
    target_file = output_dir / f"{sector}_scored.csv"
    if not target_file.exists():
        return current_scored

    try:
        existing = pd.read_csv(target_file, low_memory=False)
        if "Symbol" not in existing.columns or "LLM Score" not in existing.columns:
            return current_scored

        mapping = existing.dropna(subset=["LLM Score"]).drop_duplicates(subset=["Symbol"])
        if mapping.empty:
            return current_scored
            
        mapping = mapping.set_index("Symbol")[["LLM Score", "Combined Score", "Pros", "Cons"]]

        for idx in current_scored.index:
            sym = current_scored.at[idx, "Symbol"]
            if pd.notna(sym) and sym in mapping.index:
                current_scored.at[idx, "LLM Score"] = float(mapping.at[sym, "LLM Score"])
                current_scored.at[idx, "Combined Score"] = float(mapping.at[sym, "Combined Score"])
                current_scored.at[idx, "Pros"] = str(mapping.at[sym, "Pros"])
                current_scored.at[idx, "Cons"] = str(mapping.at[sym, "Cons"])
    except Exception as e:
        LOG.warning("Could not merge existing LLM scores for %s: %s", sector, e)

    return current_scored

# =============================================================================
# LLM EVALUATION INTEGRATION
# =============================================================================

@dataclass
class GroqReviewer:
    timeout: int = 60
    max_fields: int = 0
    model: str = "nvidia/nemotron-3-super-120b-a12b"
    fallback_models: tuple = (
        "nvidia/nemotron-3-super-120b-a12b",
        "meta/llama-3.1-70b-instruct",
        "nvidia/nemotron-3-nano-30b-a3b",
        "openai/gpt-oss-20b",
        "stepfun-ai/step-3.7-flash",
        "meta/llama-3.1-8b-instruct",
    )

    def __post_init__(self):
        # Prefer environment variable for security, fallback to provided key
        api_key = os.getenv("NVIDIA_API_KEY", "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")
        self.client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1", 
            api_key=api_key, 
            timeout=self.timeout
        )

    def check_connection(self) -> None:
        last_error = None
        for m in self.fallback_models:
            try:
                LOG.info("Testing LLM endpoint connection using model '%s'...", m)
                completion = self.client.chat.completions.create(
                    model=m, 
                    messages=[{"role": "user", "content": "Reply with the single word READY."}], 
                    temperature=0.1, 
                    top_p=1, 
                    max_tokens=256, 
                    stream=False
                )
                content = completion.choices[0].message.content or ""
                if content.strip():
                    self.model = m
                    LOG.info("LLM preflight passed with model '%s': endpoint connected and active.", m)
                    return
            except Exception as exc:
                LOG.warning("Model '%s' preflight check failed (%s). Trying fallback...", m, exc)
                last_error = exc
                time.sleep(1)

        raise RuntimeError(f"OpenAI/NVIDIA LLM preflight failed; all models timed out/errored: {last_error}")

    def review_batch(self, batch_data: list[dict], sector: str, peer_context_text: str) -> dict[str, dict]:
        prompt = {
            "task": f"You are a disciplined long-only hedge fund analyst. Review exactly {len(batch_data)} Indian stocks.",
            "rules": [
                "The numeric score is sector-relative, not a cross-sector comparison.",
                "Do not penalize normal sector economics.",
                "Do not invent facts. Treat absent data as unknown.",
                "Return strict JSON ONLY. The output must be a single JSON dictionary where the keys are the exact 'id' strings provided, and values are objects containing {llm_score: number from 0 to 100, pros: string under 60 words, cons: string under 60 words}.",
                "The llm_score must be your independent assessment from the raw data and peer context. Do not copy or infer a score from any pre-calculated score.",
            ],
            "data_dictionary": {
                "format": "Each entry in all_nonempty_raw_fields is exact_source_column_name: source_value.",
                "interpretation": [
                    "A date suffix such as Mar 2026 or Jun 2026 is the reporting period.",
                    "Interpret values with their exact column name and compare the company primarily with the supplied sector peer context."
                ]
            },
            "sector": sector,
            "peer_context": peer_context_text,
            "stocks": [
                {"id": item["id"], "all_nonempty_raw_fields": item["data"]} for item in batch_data
            ]
        }
        
        models_to_try = [self.model] + [m for m in self.fallback_models if m != self.model]
        
        for attempt in range(len(models_to_try) * 2):
            m_target = models_to_try[attempt % len(models_to_try)]
            try:
                completion = self.client.chat.completions.create(
                    model=m_target,
                    messages=[
                        {"role": "system", "content": "You are an analyst. Return ONLY a JSON object where each key is the stock id string and the value contains {llm_score: number 0-100, pros: short string, cons: short string}. Do not include any additional text."},
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    temperature=0.7,
                    max_tokens=2048,
                    top_p=1,
                    response_format={"type": "json_object"},
                    stream=False,
                )

                content = completion.choices[0].message.content or ""
                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError:
                    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
                    parsed = json.loads(match.group() if match else "{}")
                
                results = {}
                for item in batch_data:
                    stock_id = item["id"]
                    base_score = item["base_score"]

                    if isinstance(parsed, dict) and stock_id in parsed:
                        stock_data = parsed[stock_id]
                        try:
                            llm_score = float(stock_data["llm_score"])
                        except Exception:
                            LOG.warning("Invalid llm_score for stock %s; using base score.", stock_id)
                            llm_score = np.nan
                        if not (0 <= llm_score <= 100):
                            LOG.warning("llm_score out of bounds for stock %s; clamping.", stock_id)
                            llm_score = np.clip(llm_score, 0, 100)

                        pros = str(stock_data.get("pros", "")).strip()
                        cons = str(stock_data.get("cons", "")).strip()

                        if pros and cons and not np.isnan(llm_score):
                            adjustment = float(np.clip(llm_score - base_score, -LLM_MAX_ADJUSTMENT, LLM_MAX_ADJUSTMENT))
                            combined = float(np.clip(base_score + adjustment, 0, 100))
                            results[stock_id] = {"score": combined, "llm_score": llm_score, "pros": pros, "cons": cons}
                        else:
                            results[stock_id] = {"score": base_score, "llm_score": llm_score, "pros": pros, "cons": cons}
                    else:
                        LOG.warning("LLM response missing entry for stock %s; using base score.", stock_id)
                        results[stock_id] = {"score": base_score, "llm_score": np.nan, "pros": "LLM omitted stock", "cons": "LLM omitted stock"}

                return results
                
            except (KeyError, ValueError, json.JSONDecodeError, Exception) as exc:
                if "rate_limit_exceeded" in str(exc) or "413" in str(exc):
                    LOG.warning("Rate limit or payload size issue: %s", exc)
                elif attempt == 2:
                    LOG.warning("LLM batch review failed for block: %s", exc)
                time.sleep(2 ** attempt)
                
        return {item["id"]: {"score": item["base_score"], "llm_score": np.nan, "pros": "LLM error", "cons": "LLM error"} for item in batch_data}


def compact_output(frame: pd.DataFrame) -> pd.DataFrame:
    source = frame.copy()
    source["market_cap"] = source.get("market_cap", np.nan)
    source["industry"] = source.get("industry", "Unknown")
    for col in ["Stock Name", "Symbol"]:
        if col not in source:
            source[col] = "Unknown"
    return source[["Stock Name", "Symbol", "market_cap", "industry", "Hedge Fund Score", "LLM Score", "Combined Score", "Pros", "Cons"]]


def assert_output_targets_writable(output_dir: Path, files: list[Path]) -> None:
    targets = [output_dir / f"{path.stem.removesuffix('_enhanced')}_scored.csv" for path in files]
    targets.extend([output_dir / "scoring_manifest.csv", output_dir / "global_ranking.csv", output_dir / "top_bottom_20.csv"])
    blocked = []
    for target in targets:
        if not target.exists():
            continue
        try:
            with target.open("a", encoding="utf-8"):
                pass
        except PermissionError:
            blocked.append(target.name)
    if blocked:
        names = ", ".join(blocked)
        raise RuntimeError(f"Close these output files (for example, in Excel) or choose --output-dir: {names}")


def write_rankings(all_final: list[pd.DataFrame], manifest: list[dict[str, Any]], output_dir: Path) -> None:
    pd.DataFrame(manifest).to_csv(output_dir / "scoring_manifest.csv", index=False)
    if not all_final:
        return
    ranked = pd.concat(all_final, ignore_index=True)
    global_ranking = ranked.sort_values("Combined Score", ascending=False, kind="stable").reset_index(drop=True).copy()
    global_ranking.insert(0, "Global Rank", range(1, len(global_ranking) + 1))
    global_ranking.insert(1, "Global Percentile", (100 * (len(global_ranking) - global_ranking.index) / len(global_ranking)).round(1))
    global_ranking.to_csv(output_dir / "global_ranking.csv", index=False, encoding="utf-8-sig")
    best = ranked.nlargest(20, "Combined Score").copy()
    best.insert(0, "List", "Top 20")
    best.insert(1, "Rank", range(1, len(best) + 1))
    worst = ranked.nsmallest(20, "Combined Score").copy()
    worst.insert(0, "List", "Bottom 20")
    worst.insert(1, "Rank", range(1, len(worst) + 1))
    pd.concat([best, worst], ignore_index=True).to_csv(output_dir / "top_bottom_20.csv", index=False, encoding="utf-8-sig")

# =============================================================================
# MAIN EXECUTION PIPELINE
# =============================================================================

def run(input_dir: Path, output_dir: Path, use_llm: bool, limit: int | None, max_fields: int) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob("*_enhanced.csv"))
    if not files:
        raise FileNotFoundError(f"No *_enhanced.csv files found in {input_dir}")
    assert_output_targets_writable(output_dir, files)

    LOG.info("Phase 1/2: generating deterministic sector scores.")
    manifest = []
    deterministic_finals = []
    for path in files:
        sector = path.stem.removesuffix("_enhanced")
        LOG.info("Deterministic scoring %s", path.name)
        raw = pd.read_csv(path, low_memory=False)
        scored = score_sector(raw, sector)
        
        # Merge previously calculated LLM scores before writing Phase 1 output
        scored = merge_existing_llm_scores(scored, output_dir, sector)
        
        final = compact_output(scored)
        final.to_csv(output_dir / f"{sector}_scored.csv", index=False, encoding="utf-8-sig")
        
        # Log all column names for diagnostics (as requested)
        list_all_columns(scored)
        
        deterministic_finals.append(final.assign(Sector=sector))
        manifest.append({"source": path.name, "output": f"{sector}_scored.csv", "sector": sector,
                         "stocks": len(final), "mean_score": round(float(final["Combined Score"].mean()), 2),
                         "llm_reviewed": 0})
    write_rankings(deterministic_finals, manifest, output_dir)
    LOG.info("Deterministic outputs saved. They remain available if LLM review fails.")

    if not use_llm:
        LOG.info("LLM review disabled: deterministic phase is complete.")
        return

    reviewer = GroqReviewer(max_fields=max_fields)
    LOG.info("Phase 2/2: checking LLM connection.")
    reviewer.check_connection()
    
    llm_finals = []
    chunk_size = max(1, (max_fields // 3))
    if chunk_size > 5:
        chunk_size = 5
    
    for item, path in enumerate(files):
        sector = path.stem.removesuffix("_enhanced")
        raw = pd.read_csv(path, low_memory=False)
        scored = score_sector(raw, sector)
        
        # Load any existing progress
        scored = merge_existing_llm_scores(scored, output_dir, sector)

        # Determine the most predictive 100 features for this sector
        top_features = select_top_features(scored, target_column="Hedge Fund Score", n=100)
        count = len(scored) if limit is None else min(limit, len(scored))
        subset_indices = scored.index[:count]

        # Filter to only the stocks that DO NOT have a valid LLM Score yet (or failed previously)
        indices_to_score = [
            i for i in subset_indices 
            if pd.isna(scored.at[i, "LLM Score"]) or str(scored.at[i, "Pros"]).startswith("LLM ")
        ]
        already_scored = count - len(indices_to_score)
        
        if already_scored > 0:
            LOG.info("Skipping %d stocks in %s that already have an LLM score.", already_scored, sector)
            
        if not indices_to_score:
            final = compact_output(scored)
            llm_finals.append(final.assign(Sector=sector))
            manifest[item]["mean_score"] = round(float(final["Combined Score"].mean()), 2)
            manifest[item]["llm_reviewed"] = count
            continue
            
        LOG.info("LLM generating reviews for %d/%d stocks in %s (batched by %d).", len(indices_to_score), count, sector, chunk_size)
        
        for i in range(0, len(indices_to_score), chunk_size):
            batch_indices = indices_to_score[i:i + chunk_size]
            batch_data = []
            
            for idx in batch_indices:
                record_data = compact_record_with_features(scored.loc[idx], top_features) if top_features else compact_record(scored.loc[idx], max_fields)
                batch_data.append({
                    "id": str(idx),
                    "base_score": scored.at[idx, "Hedge Fund Score"],
                    "data": record_data
                })
            
            results = reviewer.review_batch(batch_data, sector, scored.at[batch_indices[0], "Peer Context"])
            
            for idx_str, res in results.items():
                idx_int = int(idx_str)
                scored.at[idx_int, "Combined Score"] = round(res["score"], 1)
                scored.at[idx_int, "LLM Score"] = round(res["llm_score"], 1) if pd.notna(res["llm_score"]) else np.nan
                scored.at[idx_int, "Pros"] = res["pros"]
                scored.at[idx_int, "Cons"] = res["cons"]
                
            # Intermediate save
            compact_output(scored).to_csv(output_dir / f"{sector}_scored.csv", index=False, encoding="utf-8-sig")
            time.sleep(1.5)

        final = compact_output(scored)
        final.to_csv(output_dir / f"{sector}_scored.csv", index=False, encoding="utf-8-sig")
        llm_finals.append(final.assign(Sector=sector))
        manifest[item]["mean_score"] = round(float(final["Combined Score"].mean()), 2)
        manifest[item]["llm_reviewed"] = count
        
    write_rankings(llm_finals, manifest, output_dir)
    LOG.info("Complete: deterministic and LLM outputs written for %d sectors.", len(files))


def main() -> None:
    default_input = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=default_input)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "scored_csv")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent / "scoring_config.json")
    parser.set_defaults(llm=True)
    parser.add_argument("--llm", dest="llm", action="store_true", help="Use Groq model review (the default).")
    parser.add_argument("--no-llm", dest="llm", action="store_false", help="Disable Groq review and run only the local deterministic model.")
    parser.add_argument("--max-llm-stocks", type=int, default=None, help="Review only this many rows per sector.")
    parser.add_argument(
        "--max-model-fields",
        type=int,
        default=0,
        help="Maximum number of non‑empty raw fields sent per LLM call. ``0`` means no limit (send all columns).",
    )
    args = parser.parse_args()
    if args.max_llm_stocks is not None and args.max_llm_stocks < 1:
        parser.error("--max-llm-stocks must be at least 1")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    
    # Load environment variables and configuration definitions before running
    load_local_env(Path(__file__).resolve().parent / ".env")
    load_config(args.config)
    
    run(args.input_dir, args.output_dir, args.llm, args.max_llm_stocks, args.max_model_fields)


if __name__ == "__main__":
    main()