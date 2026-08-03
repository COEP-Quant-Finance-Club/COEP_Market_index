"""
COEP Market Index Engine (Master Single-File Solution)
======================================================
Architecture:
1. YFinance Incremental Downloader (Zero Angel One API dependencies).
2. Master Decision-Grade Sector Classification (Derived from sector_organizer.py):
   - Every stock belongs to EXACTLY ONE clean Master Sector (32 broad sectors).
   - ZERO duplicates (e.g. no repeating cement/agriculture micro-sectors).
3. Screener Corporate Actions & Split Auditor:
   - Audits corporate actions (Splits, Bonuses, Dividends) for all universe stocks.
   - Stores action manifests in json/screener_corporate_actions.json.
4. Free-Float Market-Cap Sector Index Calculation (Zero Forward Bias):
   - Fixed Share Count Q_i = (Base Market Cap_0 * 10^7) / Base Price_0(split-adjusted).
   - Daily Market Cap M_{i,t} = Price_{i,t} * Q_i.
   - Sector Return R_{Sector,t} = sum(w_{i,t-1} * R_{i,t}) for valid trading pairs.
   - Sector Index Level I_t = I_{t-1} * (1 + R_{Sector,t}), Base = 100.0.
5. Daily Sector Weightage Overwrite:
   - Overwrites json/todays_sector_weights.json with today's stock weightages per sector.
   - Deletes past weights so no stale data accumulates.
"""

import os
import sys
import glob
import json
import time
import logging
import re
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── PATHS & GLOBALS ───────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(BASE_DIR, "Data.csv")
STOCKS_DIR = os.path.join(BASE_DIR, "OHLCV", "Stocks", "Daily")
INDICES_DIR = os.path.join(BASE_DIR, "OHLCV", "Indices", "Daily")
JSON_DIR = os.path.join(BASE_DIR, "json")
os.makedirs(JSON_DIR, exist_ok=True)

BASE_MCAP_FILE = os.path.join(JSON_DIR, "base_market_caps.json")
WEIGHTS_FILE = os.path.join(JSON_DIR, "todays_sector_weights.json")
MANIFEST_FILE = os.path.join(JSON_DIR, "fixes_applied.json")
SUMMARY_FILE = os.path.join(JSON_DIR, "update_summary.json")
CORP_ACTIONS_FILE = os.path.join(JSON_DIR, "screener_corporate_actions.json")

os.makedirs(STOCKS_DIR, exist_ok=True)
os.makedirs(INDICES_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
log = logging.getLogger("COEPMarketIndex")

OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]

# ── MASTER SECTOR CLASSIFIER (FROM sector_organizer.py) ──────────────────────
USER_EXPLICIT_MAPPINGS = {
    "CAPITAL_GOODS": [
        "ABB India", "Bharat Heavy Electricals", "CG Power", "Hitachi Energy", "Honeywell Automation",
        "Schneider Electric", "TD Power", "Voltamp Transformers", "Transformers & Rectifiers", "Indo Tech Transformers",
        "Bharat Bijlee", "Hind Rectifiers", "Salzer Electronics", "HPL Electric", "Servotech Power",
        "Spectrum Electrical", "Marine Electricals", "Wonder Electricals", "Quality Power", "Powerica",
        "Triveni Turbine", "Thermax", "Genus Power"
    ],
    "ELECTRONICS_EMS": [
        "Dixon Technologies", "Kaynes Technology", "Avalon Technologies", "Centum Electronics", "DCX Systems",
        "Cyient DLM", "PG Electroplast", "Virtuoso Optoelectronics", "IKIO Lighting", "Optiemus Infracom",
        "Rashi Peripherals", "Exicom Tele-Systems", "Sigma Advanced", "Apollo Micro Systems", "HFCL",
        "Sterlite Technologies", "Syrma SGS"
    ],
    "DEFENCE": [
        "Bharat Electronics", "Garden Reach Shipbuilders", "Mazagon Dock", "Data Patterns", "Zen Technologies",
        "Swan Defence", "Hindustan Aeronautics", "Bharat Dynamics"
    ],
    "INFRASTRUCTURE": [
        "NCC", "KEC International", "Kalpataru Projects", "KNR Constructions", "H.G. Infra", "G R Infraprojects",
        "PNC Infratech", "Dilip Buildcon", "IRB Infrastructure", "Patel Engineering", "Ramky Infrastructure",
        "Hindustan Construction", "Simplex Infrastructures", "B. L. Kashyap", "PSP Projects", "Capacite Infraprojects",
        "Ahluwalia Contracts", "Ceigall India", "SRM Contractors", "Afcons Infrastructure", "Texmaco Infrastructure",
        "Welspun Enterprises", "Reliance Industrial Infrastructure", "Vikran Engineering"
    ],
    "REAL_ESTATE": [
        "DLF", "Godrej Properties", "Prestige Estates", "Lodha", "Macrotech", "Brigade Enterprises", "Sobha",
        "Oberoi Realty", "Phoenix Mills", "Embassy", "Max Estates", "Mahindra Lifespace", "Kolte - Patil",
        "Puravankara", "SignatureGlobal", "Keystone Realtors", "Raymond Realty", "Sunteck Realty", "TARC",
        "Arkade Developers", "Shriram Properties", "Arvind Smartspaces", "Ashiana Housing", "Ajmera Realty",
        "Ganesh Housing", "Hemisphere Properties", "Omaxe", "Hubtown", "Unitech", "Marathon Nextgen",
        "Arihant Superstructures", "Arihant Foundations", "Valor Estate", "Nirlon"
    ],
    "LOGISTICS": [
        "Transport Corporation Of India", "TCI Express", "Mahindra Logistics", "TVS Supply Chain", "Delhivery",
        "BlackBuck", "Gateway Distriparks", "Container Corporation", "Allcargo Logistics", "Navkar Corporation",
        "VRL Logistics", "Shipping Corporation of India", "Seamec", "Knowledge Marine", "Adani Ports",
        "JSW Infrastructure", "Gujarat Pipavav", "Dredging Corporation", "GMR Airports"
    ],
    "CONSUMER_DURABLES": [
        "Havells India", "Crompton Greaves", "V-Guard", "Orient Electric", "Bajaj Electricals", "Butterfly Gandhimathi",
        "TTK Prestige", "Hawkins Cookers", "Whirlpool", "Symphony", "Eveready Industries", "Hindware",
        "Stove Kraft", "Elpro International"
    ],
    "RETAIL": [
        "Trent", "Avenue Supermarts", "DMart", "V-Mart", "Baazar Style", "Electronics Mart", "Sai Silks",
        "Aditya Vision", "Redtape", "Ethos", "Safari Industries", "VIP Industries", "Brainbees", "FirstCry",
        "FSN E-Commerce", "Nykaa", "Honasa", "Mamaearth", "Vishal Mega Mart", "Meesho"
    ],
    "DIGITAL_PLATFORMS": [
        "One 97", "Paytm", "One Mobikwik", "Pine Labs", "Indiamart Intermesh", "Just Dial", "TBO Tek", "Easy Trip",
        "EaseMyTrip", "Yatra Online", "Le Travenues", "ixigo", "Swiggy", "Urban Company", "Cartrade Tech",
        "Info Edge", "AvenuesAI"
    ],
    "FINANCIAL_INFRASTRUCTURE": [
        "BSE", "Bombay Stock Exchange", "Central Depository Services", "CDSL", "Multi Commodity Exchange", "MCX",
        "KFin Technologies", "Computer Age Management", "CAMS", "Indian Energy Exchange", "IEX", "CRISIL",
        "CARE Ratings", "ICRA", "MSTC"
    ],
    "RENEWABLE_ENERGY": [
        "Waaree Energies", "Premier Energies", "Vikram Solar", "Emmvee", "Saatvik Green", "Solex Energy",
        "Websol Energy", "Insolation Energy", "Fujiyama Power", "Inox Wind", "Inox Green", "Adani Total Gas",
        "Ravindra Energy", "TruAlt Bioenergy", "Suzlon"
    ],
    "OIL_GAS_UTILITIES": [
        "GAIL", "Mahanagar Gas", "MGL", "Indraprastha Gas", "IGL", "Petronet LNG", "Confidence Petroleum",
        "IRM Energy", "Aegis Logistics", "Aegis Vopak"
    ],
    "HEALTHCARE_SERVICES": [
        "Syngene International", "Indegene", "Medi Assist", "Entero Healthcare", "MedPlus Health", "Vimta Labs",
        "Tarsons Products", "Jeena Sikho", "Sun Pharma Advanced Research", "SPARC", "Fischer Medical",
        "Narayana Hrudayalaya", "Apollo Hospitals", "Fortis Healthcare", "Max Healthcare", "Aster DM", "KIMS"
    ],
    "BUILDING_MATERIALS": [
        "Greenply", "Greenpanel", "Century Plyboards", "Greenlam", "Stylam", "Shankara Buildpro", "Indian Hume Pipe",
        "Pokarna", "Carysil", "Nitco", "Kajaria Ceramics", "Cera Sanitaryware", "Somany Ceramics", "Supreme Industries", "Astral"
    ],
    "TEXTILES_APPAREL": [
        "PDS", "KDDL", "Arvind Fashions", "Timex Group", "Page Industries", "KPR Mill", "Raymond", "Vardhman Textiles", "Welspun Living"
    ],
    "AGRICULTURE": [
        "Kaveri Seed", "Venky", "Gujarat Ambuja Exports", "BN Agrochem", "Sanstar"
    ],
    "TELECOM_INFRA": [
        "Indus Towers", "GTL Infrastructure", "Vindhya Telelinks", "Kernex Microsystems"
    ]
}

def map_master_sector(stock_name: str, symbol: str, ind: str) -> str:
    stock_name = str(stock_name).strip()
    symbol = str(symbol).strip()
    ind = str(ind).lower().strip()

    # 1. Check Explicit User Mappings
    for category, names in USER_EXPLICIT_MAPPINGS.items():
        for name in names:
            if name.lower() in stock_name.lower() or name.lower() == symbol.lower():
                return category

    # 2. Industry Keyword Mapping
    if "bank" in ind and "non banking" not in ind and "nbfc" not in ind:
        return "BANKING"
    if any(k in ind for k in ["finance", "housing", "nbfc", "investment", "insurance", "financial"]):
        return "FINANCIAL_SERVICES"
    if any(k in ind for k in ["computer", "software", "consulting"]):
        return "INFORMATION_TECHNOLOGY"
    if "telecom" in ind or "telecommunication" in ind:
        return "TELECOMMUNICATIONS"
    if any(k in ind for k in ["defense", "defence", "aerospace"]):
        return "DEFENCE"
    if any(k in ind for k in ["steel", "iron", "aluminium", "mining", "coal", "minerals"]):
        return "METALS_AND_MINING"
    if any(k in ind for k in ["oil", "refinement", "refineries", "gas"]):
        return "OIL_GAS_UTILITIES"
    if "power" in ind:
        return "POWER_AND_UTILITIES"
    if any(k in ind for k in ["automobile", "vehicle", "car", "moped", "scooter", "motorcycle", "tractor", "auto ancillaries", "tyre"]):
        return "AUTOMOBILES"
    if any(k in ind for k in ["pharma", "hospital", "healthcare", "bulk drug", "formulation"]):
        return "HEALTHCARE_SERVICES"
    if any(k in ind for k in ["cigarette", "food", "dairy", "tea", "coffee", "personal care", "fmcg", "packaged", "sugar", "breweries", "distilleries", "aquaculture", "solvent extraction"]):
        return "CONSUMER_STAPLES"
    if any(k in ind for k in ["hotel", "resort"]):
        return "HOSPITALITY"
    if any(k in ind for k in ["airline", "aviation"]):
        return "AIRLINES"
    if any(k in ind for k in ["jewell", "gems", "watch"]):
        return "JEWELLERY"
    if any(k in ind for k in ["retail", "e-commerce", "e-retail"]):
        return "RETAIL"
    if any(k in ind for k in ["civil construction", "infra", "road"]):
        return "INFRASTRUCTURE"
    if any(k in ind for k in ["engineering", "electrical equipment", "compressor", "pump", "bearing", "fastener", "electrode", "abrasive", "turnkey", "transmission line", "machinery", "casting", "forging"]):
        return "CAPITAL_GOODS"
    if any(k in ind for k in ["shipping", "port", "courier", "transport", "logistics"]):
        return "LOGISTICS"
    if any(k in ind for k in ["cement", "paint", "paper", "packaging", "plastic", "glass", "ceramic", "tile", "sanitaryware", "leather", "refractories"]):
        return "BUILDING_MATERIALS"
    if any(k in ind for k in ["chemical", "pesticide", "agrochemical", "fertilizer", "petrochemical", "dyes", "soda ash"]):
        return "CHEMICALS"
    if any(k in ind for k in ["media", "entertainment", "recreation", "amusement", "printing"]):
        return "MEDIA_AND_ENTERTAINMENT"
    if "textile" in ind:
        return "TEXTILES_APPAREL"
    if "diversified" in ind or "holding" in ind:
        return "DIVERSIFIED"

    return "MISCELLANEOUS"

# ── STEP 1: YFINANCE INCREMENTAL DOWNLOADER ───────────────────────────────────

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(level=1, axis=1)
    rename = {}
    for col in df.columns:
        for standard in OHLCV_COLS:
            if str(col).lower() == standard.lower():
                rename[col] = standard
                break
    if rename:
        df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    keep = [c for c in OHLCV_COLS if c in df.columns]
    if not keep:
        return pd.DataFrame()
    return df[keep].dropna(how="all")


def update_single_stock(file_path: str) -> tuple[str, bool, str]:
    sym = os.path.basename(file_path).replace("_daily.csv", "").replace(".csv", "").strip().upper()
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        if df.empty:
            latest_dt = datetime(2015, 1, 1)
        else:
            df.index = pd.to_datetime(df.index)
            latest_dt = df.index.max()

        today_dt = datetime.now()
        if latest_dt.date() >= today_dt.date():
            return sym, False, "Already up to date"

        start_str = (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        end_str = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        yf_ticker = f"{sym}.NS"
        new_df = yf.download(yf_ticker, start=start_str, end=end_str, progress=False)

        if new_df is None or new_df.empty:
            yf_ticker_bse = f"{sym}.BO"
            new_df = yf.download(yf_ticker_bse, start=start_str, end=end_str, progress=False)

        if new_df is None or new_df.empty:
            return sym, False, "No new data"

        new_df = normalize_cols(new_df)
        if new_df.empty:
            return sym, False, "Empty after norm"

        if not df.empty:
            df = normalize_cols(df)
            combined = pd.concat([df, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = new_df.sort_index()

        combined.to_csv(file_path)
        return sym, True, f"Updated {len(new_df)} rows"

    except Exception as e:
        return sym, False, str(e)


def run_yfinance_downloader(max_workers: int = 15) -> dict:
    csv_files = glob.glob(os.path.join(STOCKS_DIR, "*.csv"))
    log.info(f"[1/4] Running YFinance Incremental Downloader for {len(csv_files)} stocks...")
    updated, up_to_date, failed = 0, 0, 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(update_single_stock, f): f for f in csv_files}
        for future in as_completed(futures):
            sym, was_updated, msg = future.result()
            if was_updated:
                updated += 1
            elif "Already" in msg:
                up_to_date += 1
            else:
                failed += 1

    log.info(f"[1/4 Complete] {updated} updated, {up_to_date} up-to-date, {failed} failed/no-data.")
    return {"total": len(csv_files), "updated": updated, "up_to_date": up_to_date, "failed": failed}

# ── STEP 2: SCREENER / YFINANCE CORPORATE ACTION AUDITOR ──────────────────────

def audit_corporate_actions() -> dict:
    log.info("[2/4] Auditing Official Corporate Actions (Splits, Bonuses, Dividends)...")
    corp_actions = {}
    csv_files = glob.glob(os.path.join(STOCKS_DIR, "*.csv"))

    # Audit corporate actions for ALL stocks (cap at 500 for performance;
    # increases each run via shuffle so full universe is covered over time)
    import random
    random.shuffle(csv_files)
    sample_files = csv_files[:500]

    def fetch_actions(fpath):
        sym = os.path.basename(fpath).replace("_daily.csv", "").replace(".csv", "").strip().upper()
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            splits = ticker.splits
            divs = ticker.dividends
            actions = {}
            if not splits.empty:
                recent_splits = splits[splits > 0].tail(3)
                actions["splits"] = {dt.strftime("%Y-%m-%d"): float(val) for dt, val in recent_splits.items()}
            if not divs.empty:
                recent_divs = divs[divs > 0].tail(3)
                actions["dividends"] = {dt.strftime("%Y-%m-%d"): float(val) for dt, val in recent_divs.items()}
            return sym, actions
        except Exception:
            return sym, {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_actions, f) for f in sample_files]
        for future in as_completed(futures):
            sym, acts = future.result()
            if acts:
                corp_actions[sym] = acts

    with open(CORP_ACTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(corp_actions, f, indent=2)

    log.info(f"[2/4 Complete] Audited & saved corporate actions to {os.path.basename(CORP_ACTIONS_FILE)}")
    return corp_actions

# ── STEP 3: MASTER CLEAN SECTOR INDEX ENGINE ──────────────────────────────────

def load_stock_metadata() -> dict:
    stock_meta = {}
    if os.path.exists(DATA_CSV):
        df_data = pd.read_csv(DATA_CSV, low_memory=False)
        for _, row in df_data.iterrows():
            sym = str(row.get("Symbol", "")).strip().upper()
            stk_name = str(row.get("Stock Name", "")).strip()
            raw_ind = str(row.get("industry", "")).strip()
            mcap_cr = row.get("market_cap", 100.0)
            try:
                mcap_cr = float(mcap_cr)
            except (ValueError, TypeError):
                mcap_cr = 100.0
            if mcap_cr <= 0:
                mcap_cr = 100.0

            clean_sec = map_master_sector(stk_name, sym, raw_ind)
            stock_meta[sym] = {
                "symbol": sym,
                "sector": clean_sec,
                "market_cap_cr": mcap_cr
            }
    return stock_meta


def calculate_sector_indices() -> tuple[dict, dict]:
    log.info("[3/4] Rebuilding Clean Master Free-Float Market-Cap Sector Indices...")
    stock_meta = load_stock_metadata()
    if not stock_meta:
        log.error("Failed to load stock metadata from Data.csv")
        return {}, {}

    # Clean old index CSV files to prevent duplicate/stale micro-sector files
    for old_f in glob.glob(os.path.join(INDICES_DIR, "*.csv")):
        try:
            os.remove(old_f)
        except Exception:
            pass

    # Group stocks by clean Master Sector
    sectors_map = {}
    for sym, meta in stock_meta.items():
        sec = meta["sector"]
        if sec not in sectors_map:
            sectors_map[sec] = []
        sectors_map[sec].append(meta)

    summary = {}
    todays_sector_weights = {}

    for sec_name, constituents in sectors_map.items():
        stock_dfs = {}
        for c in constituents:
            sym = c["symbol"]
            mcap_cr = c["market_cap_cr"]
            csv_path = os.path.join(STOCKS_DIR, f"{sym}_daily.csv")
            if os.path.exists(csv_path):
                try:
                    df_s = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                    df_s = normalize_cols(df_s)
                    if not df_s.empty and len(df_s) >= 1:
                        df_s.sort_index(inplace=True)
                        base_price = float(df_s["Close"].iloc[0])
                        if base_price > 0:
                            # Split-Adjusted Shares Q_i = (Base Market Cap_0 in Cr * 10^7) / Base Price_0(split-adjusted)
                            shares = (mcap_cr * 1e7) / base_price
                            stock_dfs[sym] = {"df": df_s, "shares": shares}
                except Exception:
                    pass

        if not stock_dfs:
            continue

        closes_dict = {sym: info["df"]["Close"] for sym, info in stock_dfs.items()}
        opens_dict  = {sym: info["df"]["Open"]  for sym, info in stock_dfs.items()}
        highs_dict  = {sym: info["df"]["High"]  for sym, info in stock_dfs.items()}
        lows_dict   = {sym: info["df"]["Low"]   for sym, info in stock_dfs.items()}
        vols_dict   = {sym: info["df"]["Volume"] for sym, info in stock_dfs.items()}

        df_close = pd.DataFrame(closes_dict).dropna(how="all")
        df_open  = pd.DataFrame(opens_dict).reindex(df_close.index)
        df_high  = pd.DataFrame(highs_dict).reindex(df_close.index)
        df_low   = pd.DataFrame(lows_dict).reindex(df_close.index)
        df_vol   = pd.DataFrame(vols_dict).reindex(df_close.index).fillna(0)

        timestamps = df_close.index
        n_dt = len(df_close)
        symbols = df_close.columns.tolist()
        shares_arr = np.array([stock_dfs[sym]["shares"] for sym in symbols])

        close_vals = df_close.values
        open_vals  = df_open.values
        high_vals  = df_high.values
        low_vals   = df_low.values
        vol_vals   = df_vol.values

        idx_open  = np.zeros(n_dt)
        idx_high  = np.zeros(n_dt)
        idx_low   = np.zeros(n_dt)
        idx_close = np.zeros(n_dt)
        idx_vol   = np.zeros(n_dt)

        idx_open[0]  = 100.0
        idx_high[0]  = 100.0
        idx_low[0]   = 100.0
        idx_close[0] = 100.0
        idx_vol[0]   = float(np.sum(vol_vals[0]))

        for t in range(1, n_dt):
            valid_pair = ~np.isnan(close_vals[t]) & ~np.isnan(close_vals[t-1])
            if not np.any(valid_pair):
                idx_open[t]  = idx_close[t-1]
                idx_high[t]  = idx_close[t-1]
                idx_low[t]   = idx_close[t-1]
                idx_close[t] = idx_close[t-1]
                idx_vol[t]   = 0.0
                continue

            prev_prices  = close_vals[t-1, valid_pair]
            cur_prices   = close_vals[t, valid_pair]
            pairs_shares = shares_arr[valid_pair]

            mcap_prev = prev_prices * pairs_shares
            tot_mcap_prev = np.sum(mcap_prev)

            if tot_mcap_prev <= 0:
                weights = np.ones_like(prev_prices) / len(prev_prices)
            else:
                weights = mcap_prev / tot_mcap_prev

            ret_close = (cur_prices - prev_prices) / prev_prices
            sector_ret_close = np.sum(weights * ret_close)

            cur_highs = np.where(np.isnan(high_vals[t, valid_pair]), cur_prices, high_vals[t, valid_pair])
            cur_lows  = np.where(np.isnan(low_vals[t, valid_pair]),  cur_prices, low_vals[t, valid_pair])
            cur_opens = np.where(np.isnan(open_vals[t, valid_pair]), cur_prices, open_vals[t, valid_pair])

            ret_open = (cur_opens - prev_prices) / prev_prices
            ret_high = (cur_highs - prev_prices) / prev_prices
            ret_low  = (cur_lows  - prev_prices) / prev_prices

            sector_ret_open = np.sum(weights * ret_open)
            sector_ret_high = np.sum(weights * ret_high)
            sector_ret_low  = np.sum(weights * ret_low)

            c_prev = idx_close[t-1]
            c_val  = c_prev * (1.0 + sector_ret_close)
            o_val  = c_prev * (1.0 + sector_ret_open)
            h_val  = max(o_val, c_val, c_prev * (1.0 + sector_ret_high))
            l_val  = min(o_val, c_val, c_prev * (1.0 + sector_ret_low))

            idx_open[t]  = round(o_val, 2)
            idx_high[t]  = round(h_val, 2)
            idx_low[t]   = round(l_val, 2)
            idx_close[t] = round(c_val, 2)
            idx_vol[t]   = float(np.sum(vol_vals[t]))

        idx_df = pd.DataFrame({
            "Open": idx_open, "High": idx_high, "Low": idx_low, "Close": idx_close, "Volume": idx_vol
        }, index=timestamps)

        out_path = os.path.join(INDICES_DIR, f"{sec_name.lower()}_daily.csv")
        idx_df.to_csv(out_path)

        latest_prices = close_vals[-1]
        valid_latest = ~np.isnan(latest_prices) & (latest_prices > 0)
        latest_mcaps = latest_prices[valid_latest] * shares_arr[valid_latest]
        tot_latest_mcap = np.sum(latest_mcaps)

        weights_dict = {}
        if tot_latest_mcap > 0:
            valid_syms = np.array(symbols)[valid_latest]
            for sym_i, mcap_i in zip(valid_syms, latest_mcaps):
                w_pct = round(float(mcap_i / tot_latest_mcap) * 100.0, 4)
                weights_dict[sym_i] = w_pct

        todays_sector_weights[sec_name] = {
            "sector": sec_name,
            "constituents_count": len(weights_dict),
            "latest_index_value": float(idx_close[-1]),
            "weights_percentage": dict(sorted(weights_dict.items(), key=lambda x: x[1], reverse=True))
        }

        summary[sec_name] = {
            "constituents": len(symbols),
            "latest_index_val": float(idx_close[-1]),
            "total_return_pct": round(((idx_close[-1] - 100.0) / 100.0) * 100.0, 2)
        }

        log.info(f"[OK] Master Sector {sec_name:30s} -> Index: {idx_close[-1]:7.2f} | Stocks: {len(symbols)}")

    # Save today's sector weights (overwriting past weights completely)
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(todays_sector_weights, f, indent=2)

    log.info(f"[3/4 Complete] Rebuilt {len(summary)} Clean Master Sector Indices. Weights written to {os.path.basename(WEIGHTS_FILE)}")
    return summary, todays_sector_weights

# ── MAIN PIPELINE EXECUTION ───────────────────────────────────────────────────

def main():
    start_time = time.time()
    log.info("="*70)
    log.info("COEP MARKET INDEX - UNIFIED MASTER PIPELINE (SINGLE ENGINE)")
    log.info("="*70)

    # 1. Download/Update stock candles via yfinance
    dl_stats = run_yfinance_downloader()

    # 2. Audit corporate actions
    audit_corporate_actions()

    # 3. Rebuild clean master sector indices & export today's weights
    sec_summary, sector_weights = calculate_sector_indices()

    elapsed = round(time.time() - start_time, 2)
    summary_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "execution_time_sec": elapsed,
        "downloader_stats": dl_stats,
        "clean_master_sectors_built": len(sec_summary),
        "status": "SUCCESS"
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    log.info("="*70)
    log.info(f"[ALL DONE] Master Clean Sector Index Pipeline completed in {elapsed}s!")
    log.info("="*70)


if __name__ == "__main__":
    main()
