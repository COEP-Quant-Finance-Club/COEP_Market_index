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
import requests
from urllib3.util import Retry
from requests.adapters import HTTPAdapter
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_session():
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

SESSION = get_session()

# ── PATHS & GLOBALS ───────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(BASE_DIR, "Data.csv")
STOCKS_DIR = os.path.join(BASE_DIR, "OHLCV", "Stocks", "Daily")
STOCKS_1H_DIR = os.path.join(BASE_DIR, "OHLCV", "Stocks", "1H")
INDICES_DIR = os.path.join(BASE_DIR, "OHLCV", "Indices", "Daily")
INDICES_1H_DIR = os.path.join(BASE_DIR, "OHLCV", "Indices", "1H")
JSON_DIR = os.path.join(BASE_DIR, "json")
os.makedirs(JSON_DIR, exist_ok=True)

BASE_MCAP_FILE = os.path.join(JSON_DIR, "base_market_caps.json")
WEIGHTS_FILE = os.path.join(JSON_DIR, "todays_sector_weights.json")
MANIFEST_FILE = os.path.join(JSON_DIR, "fixes_applied.json")
SUMMARY_FILE = os.path.join(JSON_DIR, "update_summary.json")
CORP_ACTIONS_FILE = os.path.join(JSON_DIR, "screener_corporate_actions.json")

os.makedirs(STOCKS_DIR, exist_ok=True)
os.makedirs(STOCKS_1H_DIR, exist_ok=True)
os.makedirs(INDICES_DIR, exist_ok=True)
os.makedirs(INDICES_1H_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
log = logging.getLogger("COEPMarketIndex")

OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]

# ── MASTER SECTOR CLASSIFIER (FROM sector_organizer.py) ──────────────────────
USER_EXPLICIT_MAPPINGS = {
    "PAINTS_AND_VARNISHES": [
        "Sirca Paints", "Asian Paints", "Berger Paints", "Kansai Nerolac", "Akzo Nobel",
        "Indigo Paints", "Shalimar Paints", "JSW Dulux"
    ],
    "CABLES_AND_WIRES": [
        "Polycab India", "KEI Industries", "RR Kabel", "Finolex Cables", "Paramount Communications",
        "Universal Cables", "Vindhya Telelinks", "Plaza Wires", "Dynamic Cables"
    ],
    "CEMENT_AND_BUILDING_MATERIALS": [
        "UltraTech Cement", "Ambuja Cements", "ACC", "Shree Cement", "Dalmia Bharat",
        "The Ramco Cements", "JK Lakshmi Cement", "Birla Corporation", "HeidelbergCement", "Orient Cement",
        "NCL Industries", "Sagar Cements", "KCP Limited", "Deccan Cements"
    ],
    "PAPER_AND_PACKAGING": [
        "JK Paper", "West Coast Paper", "Seshasayee Paper", "Century Textiles", "Andhra Paper",
        "TCPL Packaging", "Uflex", "Mold-Tek Packaging", "EPL Limited", "Polyplex Corporation", "Cosmo First", "Jindal Poly"
    ],
    "PLASTICS_AND_PIPES": [
        "Supreme Industries", "Astral", "Finolex Industries", "Prince Pipes", "Responsive Industries",
        "Shaily Engineering", "Kingfa Science", "Premier Polyfilm", "Jain Irrigation"
    ],
    "FERTILIZERS_AND_AGROCHEMICALS": [
        "Coromandel International", "UPL", "PI Industries", "Sumitomo Chemical", "Bayer CropScience",
        "Chambal Fertilisers", "Rashtriya Chemicals", "Fertilisers And Chemicals Travancore", "Sharda Cropchem",
        "Dhanuka Agritech", "Nova Agritech", "BN Agrochem", "Sanstar", "Venky's", "Kaveri Seed", "Gujarat Ambuja Exports"
    ],
    "SUGAR_AND_BIOENERGY": [
        "EID Parry", "Balrampur Chini", "Triveni Engineering", "Shree Renuka Sugars", "Dwarikesh Sugar",
        "Dhampur Sugar", "Uttam Sugar", "Dalmia Bharat Sugar", "Avadh Sugar", "TruAlt Bioenergy"
    ],
    "BREWERIES_AND_DISTILLERIES": [
        "United Spirits", "United Breweries", "Radico Khaitan", "Allied Blenders", "Tilaknagar Industries",
        "Som Distilleries", "Associated Alcohols", "Globus Spirits", "Piccadily Agro", "SDBL", "GM Breweries"
    ],
    "FOOTWEAR_AND_ACCESSORIES": [
        "Bata India", "Relaxo Footwears", "Campus Activewear", "Metro Brands", "Liberty Shoes",
        "Mirza International", "Redtape", "Khadim India", "Sreeleathers", "Mayur Uniquoters"
    ],
    "MEDIA_AND_ENTERTAINMENT": [
        "Sun TV Network", "Zee Entertainment", "PVR INOX", "TV18 Broadcast", "Network18",
        "Saregama India", "Tips Music", "Nazara Technologies", "Prime Focus", "Signpost India",
        "D.B. Corp", "Jagran Prakashan", "Navneet Education", "Flair Writing", "Doms Industries"
    ],
    "CAPITAL_GOODS": [
        "ABB India", "Bharat Heavy Electricals", "CG Power", "Hitachi Energy", "Honeywell Automation",
        "Schneider Electric", "TD Power", "Voltamp Transformers", "Transformers & Rectifiers", "Indo Tech Transformers",
        "Bharat Bijlee", "Hind Rectifiers", "Salzer Electronics", "HPL Electric", "Servotech Power",
        "Spectrum Electrical", "Marine Electricals", "Wonder Electricals", "Quality Power", "Powerica",
        "Triveni Turbine", "Thermax", "Genus Power", "Shilchar Technologies", "Vidya Wires", "Tembo Global",
        "Pix Transmission", "Rishabh Instruments", "Honda Siel Power", "Marsons", "Atlanta Electricals",
        "Hmt Limited", "Apar Industries", "Kirloskar Oil Engines", "Cummins India", "Siemens", "GE Vernova T&D",
        "3m India", "Eternal"
    ],
    "ELECTRONICS_EMS": [
        "Dixon Technologies", "Kaynes Technology", "Avalon Technologies", "Centum Electronics", "DCX Systems",
        "Cyient DLM", "PG Electroplast", "Virtuoso Optoelectronics", "IKIO Lighting", "Optiemus Infracom",
        "Rashi Peripherals", "Exicom Tele-Systems", "Sigma Advanced", "Apollo Micro Systems", "HFCL",
        "Sterlite Technologies", "Syrma SGS", "Epack Durable", "GNG Electronics", "Aditya Infotech",
        "Amber Enterprises"
    ],
    "DEFENCE": [
        "Bharat Electronics", "Garden Reach Shipbuilders", "Mazagon Dock", "Data Patterns", "Zen Technologies",
        "Swan Defence", "Hindustan Aeronautics", "Bharat Dynamics", "Cochin Shipyard"
    ],
    "INFRASTRUCTURE": [
        "NCC", "KEC International", "Kalpataru Projects", "KNR Constructions", "H.G. Infra", "G R Infraprojects",
        "PNC Infratech", "Dilip Buildcon", "IRB Infrastructure", "Patel Engineering", "Ramky Infrastructure",
        "Hindustan Construction", "Simplex Infrastructures", "B. L. Kashyap", "PSP Projects", "Capacite Infraprojects",
        "Ahluwalia Contracts", "Ceigall India", "SRM Contractors", "Afcons Infrastructure", "Texmaco Infrastructure",
        "Welspun Enterprises", "Reliance Industrial Infrastructure", "Vikran Engineering", "Gpt Infraprojects",
        "J.kumar Infraprojects", "Jyoti Structures", "Sanghvi Movers", "Kapston Services",
        "Rites Ltd", "Security & Intelligence Services", "Ircon International", "Adani Enterprises", "Nbcc",
        "Rail Vikas Nigam", "Cemindia Projects", "Bluspring Enterprises", "Quess Corp"
    ],
    "REAL_ESTATE": [
        "DLF", "Godrej Properties", "Prestige Estates", "Lodha", "Macrotech", "Brigade Enterprises", "Sobha",
        "Oberoi Realty", "Phoenix Mills", "Embassy", "Max Estates", "Mahindra Lifespace", "Kolte - Patil",
        "Puravankara", "SignatureGlobal", "Keystone Realtors", "Raymond Realty", "Sunteck Realty", "TARC",
        "Arkade Developers", "Shriram Properties", "Arvind Smartspaces", "Ashiana Housing", "Ajmera Realty",
        "Ganesh Housing", "Hemisphere Properties", "Omaxe", "Hubtown", "Unitech", "Marathon Nextgen",
        "Arihant Superstructures", "Arihant Foundations", "Valor Estate", "Nirlon", "Indiqube Spaces",
        "Man Infraconstruction", "Alembic Limited", "AGI Infra", "AWFIS Space", "EFC (I)", "TCC Concept",
        "Wework India", "Nesco", "Smartworks Coworking", "Sri Lotus Developers", "Aditya Birla Real Estate",
        "Kalpataru Limited", "Kalpataru", "Anant Raj Limited"
    ],
    "LOGISTICS": [
        "Transport Corporation Of India", "TCI Express", "Mahindra Logistics", "TVS Supply Chain", "Delhivery",
        "BlackBuck", "Gateway Distriparks", "Container Corporation", "Allcargo Logistics", "Navkar Corporation",
        "VRL Logistics", "Shipping Corporation of India", "Seamec", "Knowledge Marine", "Adani Ports",
        "JSW Infrastructure", "Gujarat Pipavav", "Dredging Corporation", "GMR Airports", "Sindhu Trade Links",
        "Balmer Lawrie"
    ],
    "CONSUMER_DURABLES": [
        "Havells India", "Crompton Greaves", "V-Guard", "Orient Electric", "Bajaj Electricals", "Butterfly Gandhimathi",
        "TTK Prestige", "Hawkins Cookers", "Whirlpool", "Symphony", "Eveready Industries", "Hindware",
        "Stove Kraft", "Elpro International", "Bosch Home Comfort", "Onida Electronics", "Ifb Industries",
        "Eureka Forbes", "Sheela Foam", "Cello World", "Voltas", "LG Electronics", "Blue Star"
    ],
    "RETAIL": [
        "Trent", "Avenue Supermarts", "DMart", "V-Mart", "Baazar Style", "Electronics Mart", "Sai Silks",
        "Aditya Vision", "Redtape", "Ethos", "Safari Industries", "VIP Industries", "Brainbees", "FirstCry",
        "FSN E-Commerce", "Nykaa", "Honasa", "Mamaearth", "Vishal Mega Mart", "Meesho", "Shanti Educational",
        "Elitecon International", "Redington"
    ],
    "DIGITAL_PLATFORMS": [
        "One 97", "Paytm", "One Mobikwik", "Pine Labs", "Indiamart Intermesh", "Just Dial", "TBO Tek", "Easy Trip",
        "EaseMyTrip", "Yatra Online", "Le Travenues", "ixigo", "Swiggy", "Urban Company", "Cartrade Tech",
        "Info Edge", "AvenuesAI", "Arisinfra Solutions", "Crizac", "Veranda Learning", "Jaro Institute",
        "Indian Railway Catering", "Physicswallah", "Vouchagram", "Gyftr"
    ],
    "FINANCIAL_INFRASTRUCTURE": [
        "BSE", "Bombay Stock Exchange", "Central Depository Services", "CDSL", "Multi Commodity Exchange", "MCX",
        "KFin Technologies", "Computer Age Management", "CAMS", "Indian Energy Exchange", "IEX", "CRISIL",
        "CARE Ratings", "ICRA", "MSTC"
    ],
    "RENEWABLE_ENERGY": [
        "Waaree Energies", "Premier Energies", "Vikram Solar", "Emmvee", "Saatvik Green", "Solex Energy",
        "Websol Energy", "Insolation Energy", "Fujiyama Power", "Inox Wind", "Inox Green", "Adani Total Gas",
        "Ravindra Energy", "Suzlon", "Indosolar"
    ],
    "OIL_GAS_UTILITIES": [
        "GAIL", "Mahanagar Gas", "MGL", "Indraprastha Gas", "IGL", "Petronet LNG", "Confidence Petroleum",
        "IRM Energy", "Aegis Logistics", "Aegis Vopak"
    ],
    "HEALTHCARE_SERVICES": [
        "Syngene International", "Indegene", "Medi Assist", "Entero Healthcare", "MedPlus Health", "Vimta Labs",
        "Tarsons Products", "Jeena Sikho", "Sun Pharma Advanced Research", "SPARC", "Fischer Medical",
        "Narayana Hrudayalaya", "Apollo Hospitals", "Fortis Healthcare", "Max Healthcare", "Aster DM", "KIMS",
        "Suven Life Sciences", "Ttk Healthcare"
    ],
    "BUILDING_MATERIALS": [
        "Greenply", "Greenpanel", "Century Plyboards", "Greenlam", "Stylam", "Shankara Buildpro", "Indian Hume Pipe",
        "Pokarna", "Carysil", "Nitco", "Kajaria Ceramics", "Cera Sanitaryware", "Somany Ceramics",
        "Euro Pratik", "M & B Engineering", "SG Mart"
    ],
    "TEXTILES_APPAREL": [
        "PDS", "Arvind Fashions", "Timex Group", "Page Industries", "KPR Mill", "Raymond", "Vardhman Textiles",
        "Welspun Living", "Bhartiya International"
    ],
    "CHEMICALS": [
        "Grp Limited", "Refex Industries", "Apcotex Industries", "Tinna Rubber", "Kothari Industrial", "Rain Industries",
        "Gocl Corporation", "Dcm Shriram"
    ],
    "AUTOMOBILES": [
        "Swaraj Engines", "Landmark Cars", "Igarashi Motors", "Sedemac Mechatronics", "Greaves Cotton", "Carraro India",
        "Munjal Auto"
    ],
    "CONSUMER_STAPLES": [
        "Vintage Coffee", "Amir Chand Jagdish", "Hindustan Foods"
    ],
    "HOSPITALITY": [
        "Thomas Cook"
    ],
    "JEWELLERY": [
        "PNGS Reva", "International Gemmological"
    ],
    "TELECOMMUNICATIONS": [
        "D-link", "Indus Towers", "GTL Infrastructure", "Kernex Microsystems"
    ],
    "POWER_AND_UTILITIES": [
        "Antony Waste", "Ptc India", "Gujarat Energy"
    ],
    "METALS_AND_MINING": [
        "Midwest Ltd", "CMR Green", "Lloyds Enterprises", "Mmtc Limited", "Central Mine Planning", "Nava Ltd"
    ],
    "FINANCIAL_SERVICES": [
        "CMS Info Systems", "Max Financial", "SBI Funds", "Piramal Finance", "Indiabulls Limited",
        "Teamlease Services", "Updater Services"
    ],
    "DIVERSIFIED": [
        "3M India", "DCM Shriram", "Balmer Lawrie", "Refex Industries",
        "GOCL Corporation", "Nava Ltd", "Kothari Industrial"
    ],
    "INFORMATION_TECHNOLOGY": [
        "Hexaware Technologies"
    ]
}

def map_master_sector(stock_name: str, symbol: str, ind: str) -> str:
    stock_name = str(stock_name).strip()
    symbol = str(symbol).strip()
    ind = str(ind).lower().strip()

    # 1. Check Explicit Mappings
    for category, names in USER_EXPLICIT_MAPPINGS.items():
        for name in names:
            if name.lower() in stock_name.lower() or name.lower() == symbol.lower():
                return category

    # 2. Industry Keyword Mapping
    if "paint" in ind or "varnish" in ind:
        return "PAINTS_AND_VARNISHES"
    if "cable" in ind:
        return "CABLES_AND_WIRES"
    if "cement" in ind:
        return "CEMENT_AND_BUILDING_MATERIALS"
    if "paper" in ind or "packaging" in ind:
        return "PAPER_AND_PACKAGING"
    if "plastic" in ind or "moulded luggage" in ind:
        return "PLASTICS_AND_PIPES"
    if "pesticide" in ind or "fertilizer" in ind or "agrochemical" in ind or "seed" in ind or "solvent extraction" in ind:
        return "FERTILIZERS_AND_AGROCHEMICALS"
    if "sugar" in ind:
        return "SUGAR_AND_BIOENERGY"
    if "breweries" in ind or "distilleries" in ind:
        return "BREWERIES_AND_DISTILLERIES"
    if "footwear" in ind or "leather" in ind:
        return "FOOTWEAR_AND_ACCESSORIES"
    if "tyre" in ind or "auto ancillar" in ind or "auto parts" in ind or "bearing" in ind:
        return "AUTOMOBILES"

    if "bank" in ind and "non banking" not in ind and "nbfc" not in ind:
        return "BANKING"
    if any(k in ind for k in ["finance", "housing", "nbfc", "investment", "insurance", "financial", "credit", "asset management"]):
        return "FINANCIAL_SERVICES"
    if any(k in ind for k in ["computer", "software", "consulting", "information technology"]):
        return "INFORMATION_TECHNOLOGY"
    if "telecom" in ind or "telecommunication" in ind or "transmisson line" in ind:
        return "TELECOMMUNICATIONS"
    if any(k in ind for k in ["defense", "defence", "aerospace"]):
        return "DEFENCE"
    if any(k in ind for k in ["steel", "iron", "aluminium", "mining", "coal", "minerals", "castings & forgings", "refractories"]):
        return "METALS_AND_MINING"
    if any(k in ind for k in ["oil", "refinery", "gas", "petrochemical"]):
        return "OIL_GAS_UTILITIES"
    if "power" in ind or "dry cells" in ind:
        return "POWER_AND_UTILITIES"
    if any(k in ind for k in ["automobile", "vehicle", "car", "moped", "scooter", "motorcycle", "tractor"]):
        return "AUTOMOBILES"
    if any(k in ind for k in ["pharma", "hospital", "healthcare", "bulk drug", "formulation"]):
        return "HEALTHCARE_SERVICES"
    if any(k in ind for k in ["cigarette", "food", "dairy", "tea", "coffee", "personal care", "fmcg", "packaged", "aquaculture"]):
        return "CONSUMER_STAPLES"
    if any(k in ind for k in ["hotel", "resort", "travel", "recreation", "amusement"]):
        return "HOSPITALITY"
    if "airline" in ind:
        return "LOGISTICS"
    if any(k in ind for k in ["jewell", "gems", "diamond", "watch"]):
        return "JEWELLERY"
    if any(k in ind for k in ["retail", "e-commerce", "e-retail", "departmental"]):
        return "RETAIL"
    if any(k in ind for k in ["civil construction", "infra", "road", "construction"]):
        return "INFRASTRUCTURE"
    if any(k in ind for k in ["engineering", "electrical equipment", "compressor", "pump", "fastener", "electrode", "abrasive", "machinery"]):
        return "CAPITAL_GOODS"
    if any(k in ind for k in ["shipping", "port", "courier", "transport", "logistics"]):
        return "LOGISTICS"
    if any(k in ind for k in ["ceramic", "tile", "sanitaryware", "glass"]):
        return "BUILDING_MATERIALS"
    if any(k in ind for k in ["chemical", "dyes", "soda ash", "chlor alkali"]):
        return "CHEMICALS"
    if any(k in ind for k in ["media", "entertainment", "printing", "stationery"]):
        return "MEDIA_AND_ENTERTAINMENT"
    if "textile" in ind or "jute" in ind:
        return "TEXTILES_APPAREL"
    if "electronics" in ind:
        return "ELECTRONICS_EMS"

    return "CAPITAL_GOODS"

# ── STEP 1: YFINANCE INCREMENTAL DOWNLOADER ───────────────────────────────────

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)

    # --- Robust MultiIndex flattening (handles all yfinance versions) ---
    # yfinance >= 0.2.40 returns MultiIndex like ('Close', 'RELIANCE.NS')
    # yfinance older returns MultiIndex like ('Close', '') for single tickers
    # We always take the first level (the OHLCV field name) as the column name.
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [str(col[0]) if isinstance(col, tuple) else str(col) for col in df.columns]

    # Normalise column names to standard OHLCV casing
    rename = {}
    for col in df.columns:
        for standard in OHLCV_COLS:
            if str(col).strip().lower() == standard.lower():
                rename[col] = standard
                break
        # Also handle 'Adj Close' -> 'Close' (yfinance auto_adjust=False fallback)
        if str(col).strip().lower() in ("adj close", "adj_close") and "Close" not in rename.values():
            rename[col] = "Close"
    if rename:
        df = df.rename(columns=rename)
    df = df.loc[:, ~df.columns.duplicated(keep="first")]
    keep = [c for c in OHLCV_COLS if c in df.columns]
    if not keep:
        return pd.DataFrame()
    df = df[keep].dropna(how="all")

    # Purge zero-volume flat placeholder bars injected on non-trading days
    if "Volume" in df.columns and "Open" in df.columns and "Close" in df.columns:
        df = df[~((df["Volume"] == 0) & (df["Open"] == df["Close"]))].copy()

    for pcol in ["Open", "High", "Low", "Close"]:
        if pcol in df.columns:
            df[pcol] = pd.to_numeric(df[pcol], errors="coerce")
            df = df[df[pcol] > 0]
    return df.dropna(subset=["Close"])


def update_single_stock(file_path: str) -> tuple[str, bool, str]:
    sym = os.path.basename(file_path).replace("_daily.csv", "").replace(".csv", "").strip().upper()
    try:
        # Determine start date: check if file exists and has rows to update incrementally
        start_date = "2015-01-01"
        df_old = None
        if os.path.exists(file_path) and os.path.getsize(file_path) > 100:
            try:
                df_old = pd.read_csv(file_path, index_col=0, parse_dates=True)
                if not df_old.empty:
                    df_old.sort_index(inplace=True)
                    last_dt = df_old.index[-1]
                    # Fetch starting 14 days ago to capture weekend gaps / audit modifications / splits
                    start_date = (last_dt - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
            except Exception:
                pass

        time.sleep(0.1)  # small rate-limit safety sleep
        df_new = yf.download(f"{sym}.NS", start=start_date, progress=False, auto_adjust=True, session=SESSION)
        if df_new is None or df_new.empty:
            df_new = yf.download(f"{sym}.BO", start=start_date, progress=False, auto_adjust=True, session=SESSION)

        if df_new is None or df_new.empty:
            if df_old is not None and not df_old.empty:
                return sym, True, "No new daily data (kept old)"
            return sym, False, "No data found on NSE/BSE"

        df_new = normalize_cols(df_new)
        if df_new.empty:
            if df_old is not None and not df_old.empty:
                return sym, True, "No new daily data after norm (kept old)"
            return sym, False, "Empty after norm"

        if df_old is not None and not df_old.empty:
            if df_old.index.tz is not None:
                df_old.index = df_old.index.tz_localize(None)
            if df_new.index.tz is not None:
                df_new.index = df_new.index.tz_localize(None)
            df_combined = pd.concat([df_old, df_new])
        else:
            df_combined = df_new

        df_combined = df_combined[~df_combined.index.duplicated(keep="last")].sort_index()
        df_combined.to_csv(file_path)
        return sym, True, f"Updated up to {df_combined.index[-1].date()}"

    except Exception as e:
        return sym, False, str(e)


def run_yfinance_downloader(max_workers: int = 5) -> dict:
    if not os.path.exists(DATA_CSV):
        return {"total": 0, "updated": 0, "failed": 0}

    df_data = pd.read_csv(DATA_CSV, low_memory=False)
    symbols = sorted(list(set(df_data["Symbol"].dropna().astype(str).str.strip().str.upper())))
    nse_tickers = [f"{sym}.NS" for sym in symbols if sym]

    log.info(f"[1/4] Running YFinance Batch EOD Downloader for {len(nse_tickers)} stocks...")
    
    batch_df = None
    try:
        batch_df = yf.download(nse_tickers, period="1mo", group_by="ticker", threads=True, progress=False)
    except Exception as e:
        log.error(f"Batch download error: {e}")

    updated, failed = 0, 0

    for sym in symbols:
        if not sym:
            continue
        file_path = os.path.join(STOCKS_DIR, f"{sym}_daily.csv")
        ticker = f"{sym}.NS"

        try:
            df_old = None
            if os.path.exists(file_path) and os.path.getsize(file_path) > 100:
                try:
                    df_old = pd.read_csv(file_path, index_col=0, parse_dates=True)
                    if not df_old.empty:
                        df_old.sort_index(inplace=True)
                except Exception:
                    pass

            df_new = None
            if batch_df is not None and ticker in batch_df.columns.levels[0]:
                try:
                    df_sub = batch_df[ticker].dropna(how="all")
                    df_new = normalize_cols(df_sub)
                except Exception:
                    pass

            if df_old is not None and not df_old.empty:
                if df_new is not None and not df_new.empty:
                    if df_old.index.tz is not None:
                        df_old.index = df_old.index.tz_localize(None)
                    if df_new.index.tz is not None:
                        df_new.index = df_new.index.tz_localize(None)
                    df_combined = pd.concat([df_old, df_new])
                    df_combined = df_combined[~df_combined.index.duplicated(keep="last")].sort_index()
                    df_combined.to_csv(file_path)
                    updated += 1
                else:
                    updated += 1
            elif df_new is not None and not df_new.empty:
                df_new.to_csv(file_path)
                updated += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    log.info(f"[1/4 Complete] {updated} stocks updated up to latest date ({datetime.now().strftime('%Y-%m-%d')}), {failed} failed.")
    return {"total": len(symbols), "updated": updated, "failed": failed}

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

def sanitize_rogue_spikes() -> int:
    log.info("[2.5] Sanitizing multi-day rogue YFinance price spikes/bad ticks...")
    csv_files = glob.glob(os.path.join(STOCKS_DIR, "*.csv"))
    total_fixed_bars = 0
    cleaned_stocks = 0

    for f in csv_files:
        sym = os.path.basename(f).replace("_daily.csv", "").replace(".csv", "").strip().upper()
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if df.empty or len(df) < 20 or "Close" not in df.columns:
                continue

            df.sort_index(inplace=True)
            closes = df["Close"]
            modified = False
            n = len(df)
            i = 0
            while i < n - 1:
                prev_idx = max(0, i - 1)
                p_prev = closes.iloc[prev_idx]
                p_curr = closes.iloc[i]

                if p_prev > 0 and (p_curr / p_prev > 3.0 or p_curr / p_prev < 0.33):
                    end_j = -1
                    for j in range(i + 1, min(n, i + 20)):
                        p_next = closes.iloc[j]
                        if p_prev > 0 and abs(p_next - p_prev) / p_prev < 0.50:
                            end_j = j
                            break
                    
                    if end_j != -1 and (end_j - i) <= 15:
                        start_val = closes.iloc[prev_idx]
                        end_val = closes.iloc[end_j]
                        num_steps = end_j - prev_idx
                        
                        for step_k, k in enumerate(range(prev_idx + 1, end_j)):
                            interp_p = start_val + (end_val - start_val) * ((step_k + 1) / num_steps)
                            interp_p = round(float(interp_p), 2)
                            dt = df.index[k]
                            df.loc[dt, ["Open", "High", "Low", "Close"]] = interp_p
                            total_fixed_bars += 1
                        
                        modified = True
                        i = end_j
                        continue
                i += 1

            if modified:
                df.to_csv(f)
                cleaned_stocks += 1

        except Exception:
            pass

    # PERMANENT CORPORATE ACTION DEMERGER & SPLIT BACKWARD RATIO ADJUSTER
    log.info("[2.6] Applying Corporate Action Demerger & Split Backward Ratio Adjustments...")
    demerger_fixed_files = 0
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            if df.empty or len(df) < 10 or "Close" not in df.columns:
                continue

            date_cols = [c for c in df.columns if 'date' in c.lower() or 'time' in c.lower()]
            if not date_cols:
                continue
            dcol = date_cols[0]

            df[dcol] = pd.to_datetime(df[dcol])
            df.sort_values(by=dcol, inplace=True)
            df.reset_index(drop=True, inplace=True)

            closes = df["Close"].values
            n = len(closes)
            was_modified = False

            for i in range(1, n):
                prev_close = closes[i - 1]
                curr_close = closes[i]

                if prev_close <= 0 or curr_close <= 0:
                    continue

                chg = (curr_close - prev_close) / prev_close

                # Detect unadjusted demerger drop (>35% drop)
                if chg < -0.35:
                    curr_open = df.loc[i, "Open"] if "Open" in df.columns else curr_close
                    if abs(curr_open - curr_close) / curr_close < 0.20:
                        factor = curr_close / prev_close
                        for col in ["Open", "High", "Low", "Close"]:
                            if col in df.columns:
                                df.loc[:i-1, col] = df.loc[:i-1, col] * factor
                        closes = df["Close"].values
                        was_modified = True

            if was_modified:
                df[dcol] = df[dcol].dt.strftime('%Y-%m-%d')
                df.to_csv(f, index=False)
                demerger_fixed_files += 1
        except Exception:
            pass

    log.info(f"[2.6 Complete] Applied backward ratio adjustments to {demerger_fixed_files} demerging stock files.")
    log.info(f"[2.5 Complete] Sanitized {total_fixed_bars} rogue YFinance spike bars across {cleaned_stocks} stocks.")
    return total_fixed_bars


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


def update_1h_data_via_yfinance(max_workers: int = 5):
    log.info("[2.7] Running Incremental 1H Data Downloader and Corporate Action Adjuster via YFinance...")
    if not os.path.exists(DATA_CSV):
        log.error("Data.csv not found, skipping 1H updates.")
        return

    df_data = pd.read_csv(DATA_CSV, low_memory=False)
    symbols = sorted(list(set(df_data["Symbol"].dropna().astype(str).str.strip().str.upper())))
    nse_tickers = [f"{sym}.NS" for sym in symbols if sym]

    log.info(f"[2.7] Running YFinance Batch 1H Downloader for {len(nse_tickers)} stocks...")
    batch_df_1h = None
    try:
        batch_df_1h = yf.download(nse_tickers, period="5d", interval="1h", group_by="ticker", threads=True, progress=False)
    except Exception as e:
        log.error(f"1H Batch download error: {e}")

    updated, failed = 0, 0

    for sym in symbols:
        if not sym: continue
        out_path = os.path.join(STOCKS_1H_DIR, f"{sym}_1h.csv")
        ticker = f"{sym}.NS"

        try:
            df_old = None
            if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                try:
                    df_old = pd.read_csv(out_path, index_col=0, parse_dates=True)
                    if not df_old.empty: df_old.sort_index(inplace=True)
                except Exception: pass

            df_new = None
            if batch_df_1h is not None and ticker in batch_df_1h.columns.levels[0]:
                try:
                    df_sub = batch_df_1h[ticker].dropna(how="all")
                    df_new = normalize_cols(df_sub)
                except Exception: pass

            if df_old is not None and not df_old.empty:
                if df_new is not None and not df_new.empty:
                    if df_old.index.tz is not None: df_old.index = df_old.index.tz_localize(None)
                    if df_new.index.tz is not None: df_new.index = df_new.index.tz_localize(None)
                    df_combined = pd.concat([df_old, df_new])
                    df_combined = df_combined[~df_combined.index.duplicated(keep="last")].sort_index()
                    df_combined.to_csv(out_path)
                    updated += 1
                else: updated += 1
            elif df_new is not None and not df_new.empty:
                df_new.to_csv(out_path)
                updated += 1
            else: failed += 1
        except Exception:
            failed += 1

    log.info(f"[2.7 Complete] 1H data update finished: {updated} updated, {failed} failed.")


def calculate_sector_indices() -> tuple[dict, dict]:
    log.info("[3/4] Rebuilding Clean Master Free-Float Market-Cap Sector Indices from 1H Candles...")
    stock_meta = load_stock_metadata()
    if not stock_meta:
        log.error("Failed to load stock metadata from Data.csv")
        return {}, {}

    # Clean old index CSV files to prevent duplicate/stale micro-sector files
    for old_f in glob.glob(os.path.join(INDICES_DIR, "*.csv")) + glob.glob(os.path.join(INDICES_1H_DIR, "*.csv")):
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
            if not os.path.exists(csv_path):
                csv_path = os.path.join(STOCKS_1H_DIR, f"{sym}_1h.csv")
            if os.path.exists(csv_path):
                try:
                    df_s = pd.read_csv(csv_path, index_col=0, parse_dates=True)
                    if df_s.index.tz is not None:
                        df_s.index = df_s.index.tz_localize(None)
                    if not df_s.empty and len(df_s) >= 1:
                        df_s.sort_index(inplace=True)
                        latest_price = float(df_s["Close"].iloc[-1])
                        if latest_price > 0:
                            shares = (mcap_cr * 1e7) / latest_price
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

        df_close = pd.DataFrame(closes_dict).dropna(how="all").sort_index()
        df_open  = pd.DataFrame(opens_dict).reindex(df_close.index)
        df_high  = pd.DataFrame(highs_dict).reindex(df_close.index)
        df_low   = pd.DataFrame(lows_dict).reindex(df_close.index)
        df_vol   = pd.DataFrame(vols_dict).reindex(df_close.index).fillna(0)

        df_ret = df_close.pct_change()
        shares = {sym: stock_dfs[sym]["shares"] for sym in df_close.columns}
        df_mcap = df_close.copy()
        for sym in df_close.columns:
            df_mcap[sym] = df_mcap[sym] * shares[sym]

        n_dt = len(df_close)
        idx_open  = np.zeros(n_dt)
        idx_high  = np.zeros(n_dt)
        idx_low   = np.zeros(n_dt)
        idx_close = np.zeros(n_dt)
        idx_vol   = np.zeros(n_dt)

        idx_close[0] = 100.0
        idx_open[0]  = 100.0
        idx_high[0]  = 100.0
        idx_low[0]   = 100.0
        idx_vol[0]   = df_vol.iloc[0].sum()

        for t in range(1, n_dt):
            prev_mcaps = df_mcap.iloc[t-1]
            curr_rets  = df_ret.iloc[t]
            
            valid_mask = (~prev_mcaps.isna()) & (~curr_rets.isna()) & (prev_mcaps > 0)
            
            if not valid_mask.any():
                idx_close[t] = idx_close[t-1]
                idx_open[t]  = idx_close[t-1]
                idx_high[t]  = idx_close[t-1]
                idx_low[t]   = idx_close[t-1]
                idx_vol[t]   = 0
                continue

            valid_mcaps = prev_mcaps[valid_mask]
            valid_rets  = curr_rets[valid_mask]
            
            total_prev_mcap = valid_mcaps.sum()
            weights = valid_mcaps / total_prev_mcap
            
            daily_idx_ret = (weights * valid_rets).sum()
            
            curr_opens = (df_open.iloc[t] - df_close.iloc[t-1]) / df_close.iloc[t-1]
            curr_highs = (df_high.iloc[t] - df_close.iloc[t-1]) / df_close.iloc[t-1]
            curr_lows  = (df_low.iloc[t]  - df_close.iloc[t-1]) / df_close.iloc[t-1]
            
            ret_open = (weights * curr_opens[valid_mask].fillna(0)).sum()
            ret_high = (weights * curr_highs[valid_mask].fillna(0)).sum()
            ret_low  = (weights * curr_lows[valid_mask].fillna(0)).sum()

            prev_c = idx_close[t-1]
            c_val = prev_c * (1.0 + daily_idx_ret)
            o_val = prev_c * (1.0 + ret_open)
            h_val = max(c_val, o_val, prev_c * (1.0 + ret_high))
            l_val = min(c_val, o_val, prev_c * (1.0 + ret_low))

            idx_close[t] = c_val
            idx_open[t]  = o_val
            idx_high[t]  = h_val
            idx_low[t]   = l_val
            idx_vol[t]   = df_vol.iloc[t].sum()

        idx_df = pd.DataFrame({
            "Open": np.round(idx_open, 4), 
            "High": np.round(idx_high, 4), 
            "Low": np.round(idx_low, 4), 
            "Close": np.round(idx_close, 4), 
            "Volume": idx_vol.astype(int)
        }, index=df_close.index)

        # Save 1H Index CSV
        out_path_1h = os.path.join(INDICES_1H_DIR, f"{sec_name.lower()}_1h.csv")
        idx_df.to_csv(out_path_1h)

        # Aggregate to daily Index levels (fully calculated from 1H candles)
        idx_df["_date"] = idx_df.index.normalize()
        daily_idx_df = idx_df.groupby("_date").agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        })
        daily_idx_df.index.name = "Date"

        # Save Daily Index CSV
        out_path_daily = os.path.join(INDICES_DIR, f"{sec_name.lower()}_daily.csv")
        daily_idx_df.to_csv(out_path_daily)

        latest_index_val = float(daily_idx_df["Close"].iloc[-1])
        
        # Calculate constituents weights for weights JSON
        symbols = df_close.columns.tolist()
        latest_prices = df_close.iloc[-1].fillna(df_close.iloc[-2] if len(df_close) > 1 else 0)
        latest_mcaps = np.array([latest_prices[sym] * stock_dfs[sym]["shares"] for sym in symbols])
        tot_latest_mcap = np.sum(latest_mcaps)

        weights_dict = {}
        if tot_latest_mcap > 0:
            for sym_i, mcap_i in zip(symbols, latest_mcaps):
                w_pct = round(float(mcap_i / tot_latest_mcap) * 100.0, 4)
                weights_dict[sym_i] = w_pct

        todays_sector_weights[sec_name] = {
            "sector": sec_name,
            "constituents_count": len(weights_dict),
            "latest_index_value": round(latest_index_val, 2),
            "weights_percentage": dict(sorted(weights_dict.items(), key=lambda x: x[1], reverse=True))
        }

        summary[sec_name] = {
            "constituents": len(symbols),
            "latest_index_val": round(latest_index_val, 2),
            "total_return_pct": round(((latest_index_val - 100.0) / 100.0) * 100.0, 2)
        }

        log.info(f"[OK] Master Sector {sec_name:30s} -> Index: {latest_index_val:7.2f} | Stocks: {len(symbols)}")

    # Save today's sector weights
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(todays_sector_weights, f, indent=2)

    log.info(f"[3/4 Complete] Rebuilt {len(summary)} Clean Master Sector Indices. Weights written to {os.path.basename(WEIGHTS_FILE)}")
    return summary, todays_sector_weights

# ── MAIN PIPELINE EXECUTION ───────────────────────────────────────────────────

def update_readme_leaderboard(summary: dict) -> None:
    readme_path = os.path.join(BASE_DIR, "README.md")
    if not os.path.exists(readme_path):
        log.warning("README.md not found, skipping leaderboard update.")
        return

    try:
        # Sort sectors by total return descending
        sorted_sectors = sorted(
            summary.items(),
            key=lambda x: x[1]["total_return_pct"],
            reverse=True
        )

        # Build markdown table
        lines = [
            "| Rank | Sector | Index Level | Total Return | Constituents |",
            "|:---:|:---|:---:|:---:|:---:|",
        ]
        for i, (sec_name, stats) in enumerate(sorted_sectors, 1):
            medals = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}.get(i, "")
            name = sec_name.replace("_", " ").title().replace("Ems", "EMS").replace("It", "IT")
            idx_val = stats["latest_index_val"]
            ret_val = stats["total_return_pct"]
            consts = stats["constituents"]
            lines.append(f"| {medals}{i} | {name} | {idx_val:,.1f} | {ret_val:+,.1f}% | {consts} |")

        table_content = "\n".join(lines)

        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find position to insert table bounded by header marker and separator
        header_marker = "## 📊 Live Sector Leaderboard\n\n> All indices base-100 from **Jan 2015**. Rebuilt daily via YFinance."
        
        if header_marker in content:
            parts = content.split(header_marker, 1)
            after = parts[1]
            if "---" in after:
                sub_parts = after.split("---", 1)
                new_content = parts[0] + header_marker + "\n\n" + table_content + "\n\n---" + sub_parts[1]
            else:
                new_content = parts[0] + header_marker + "\n\n" + table_content
            
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            log.info("Successfully updated README.md live sector leaderboard table.")
        else:
            log.warning("Could not find sector leaderboard section marker in README.md.")
    except Exception as e:
        log.error(f"Failed to update README.md leaderboard: {e}")


def generate_dashboard_data():
    log.info("[5/5] Generating regime_dashboard_data.js for dynamic web dashboard...")
    output_js = os.path.join(BASE_DIR, "Portfolio Management Main", "regime_dashboard_data.js")
    
    # 1. Load Symbol to Stock Name mapping from Data.csv
    symbol_to_name = {}
    if os.path.exists(DATA_CSV):
        try:
            df_data = pd.read_csv(DATA_CSV, low_memory=False)
            for _, row in df_data.iterrows():
                sym = str(row.get("Symbol", "")).strip().upper()
                name = str(row.get("Stock Name", sym)).strip()
                if sym:
                    symbol_to_name[sym] = name
        except Exception as e:
            log.error(f"Error loading Data.csv: {e}")

    # 2. Load todays sector weights to get constituent lists
    sector_constituents = {}
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                weights = json.load(f)
                for sec, sec_obj in weights.items():
                    if isinstance(sec_obj, dict):
                        weights_dict = sec_obj.get("weights_percentage", {})
                        sector_constituents[sec] = sorted(list(weights_dict.keys()))
        except Exception as e:
            log.error(f"Error loading todays_sector_weights.json: {e}")

    # 3. Discretization helper for 3-state macro regimes
    def compute_raw_3state_local(close_series: np.ndarray) -> np.ndarray:
        n = len(close_series)
        if n < 5:
            return np.ones(n, dtype=int)
        rets = np.zeros(n)
        rets[1:] = np.diff(close_series) / (close_series[:-1] + 1e-6) * 100.0
        mom3 = pd.Series(rets).rolling(3, min_periods=1).sum().values
        quantiles = np.percentile(mom3, np.linspace(0, 100, 8))
        quantiles[0] -= 1e-5
        quantiles[-1] += 1e-5
        causal_7state = np.clip(np.digitize(mom3, quantiles) - 1, 0, 6)
        return np.where(causal_7state <= 2, 0, np.where(causal_7state >= 4, 2, 1))

    # 4. Process all indices
    sector_summaries = []
    sector_details = {}
    csv_files = glob.glob(os.path.join(INDICES_DIR, "*.csv"))

    for f in csv_files:
        sec_name = os.path.basename(f).replace("_daily.csv", "").replace(".csv", "").upper()
        if not sec_name:
            continue
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if df.empty or "Close" not in df.columns:
                continue

            df.sort_index(inplace=True)
            close_vals = df["Close"].values
            raw_states = compute_raw_3state_local(close_vals)

            # Compile index daily bars
            bars = []
            for idx, (dt, row) in enumerate(df.iterrows()):
                o_val = round(float(row.get("Open", row["Close"])), 2)
                h_val = round(float(row.get("High", row["Close"])), 2)
                l_val = round(float(row.get("Low", row["Close"])), 2)
                c_val = round(float(row["Close"]), 2)
                v_val = int(row.get("Volume", 0))
                m_state = int(raw_states[idx])

                bars.append({
                    "t": dt.strftime("%Y-%m-%d"),
                    "o": o_val,
                    "h": h_val,
                    "l": l_val,
                    "c": c_val,
                    "v": v_val,
                    "m": m_state
                })

            if not bars:
                continue

            # Load recent prices for constituent stocks (last 30 trading days)
            consts_symbols = sector_constituents.get(sec_name, [])
            consts_list = []
            for sym in consts_symbols:
                stock_path = os.path.join(STOCKS_DIR, f"{sym}_daily.csv")
                prices_dict = {}
                if os.path.exists(stock_path):
                    try:
                        df_stk = pd.read_csv(stock_path, index_col=0, parse_dates=True)
                        if not df_stk.empty and "Close" in df_stk.columns:
                            df_stk.sort_index(inplace=True)
                            recent = df_stk.tail(252)
                            for dt_stk, row_stk in recent.iterrows():
                                prices_dict[dt_stk.strftime("%Y-%m-%d")] = round(float(row_stk["Close"]), 2)
                    except Exception:
                        pass

                consts_list.append({
                    "symbol": sym,
                    "name": symbol_to_name.get(sym, sym),
                    "prices": prices_dict
                })

            cur_val = bars[-1]["c"]
            tot_ret_val = round(((cur_val - 100.0) / 100.0) * 100.0, 2)
            ret_str = f"{'+' if tot_ret_val >= 0 else ''}{tot_ret_val:.2f}%"

            summary_item = {
                "sector": sec_name,
                "current_val": cur_val,
                "total_return_pct": ret_str,
                "stock_count": len(consts_symbols)
            }
            sector_summaries.append(summary_item)

            sector_details[sec_name] = {
                "sector": sec_name,
                "current_val": cur_val,
                "total_return_pct": ret_str,
                "bars": bars,
                "constituents": consts_list
            }
        except Exception as e:
            log.error(f"Error compiling details for sector {sec_name}: {e}")

    # Sort sector summaries by return
    sector_summaries.sort(key=lambda x: float(x["total_return_pct"].replace("%", "")), reverse=True)

    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "max_k": 50,
        "sector_summaries": sector_summaries,
        "sector_details": sector_details
    }

    os.makedirs(os.path.dirname(output_js), exist_ok=True)
    with open(output_js, "w", encoding="utf-8") as f:
        f.write("window.REGIME_ANALYSIS_DATA = ")
        json.dump(payload, f)
        f.write(";\n")

    size_mb = round(os.path.getsize(output_js) / (1024 * 1024), 2)
    log.info(f"[5/5 Complete] Generated regime_dashboard_data.js ({size_mb} MB) for {len(sector_summaries)} master sectors.")


def main():
    start_time = time.time()
    log.info("="*70)
    log.info("COEP MARKET INDEX - UNIFIED MASTER PIPELINE (SINGLE ENGINE)")
    log.info("="*70)

    # 1. Download clean full-history stock daily candles via yfinance
    dl_stats = run_yfinance_downloader()

    # 2. Audit corporate actions on daily data
    audit_corporate_actions()

    # 2.5. Sanitize multi-day rogue YFinance price spikes & bad ticks on daily data
    sanitize_rogue_spikes()

    # 2.7. Download and update 1H data via yfinance fallbacks
    update_1h_data_via_yfinance()

    # 3. Rebuild clean master sector indices (1H & Daily divisor-based) & export weights
    sec_summary, sector_weights = calculate_sector_indices()

    # 4. Automatically update README.md sector leaderboard table
    update_readme_leaderboard(sec_summary)

    # 5. Automatically generate updated dashboard_data.js
    generate_dashboard_data()

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
