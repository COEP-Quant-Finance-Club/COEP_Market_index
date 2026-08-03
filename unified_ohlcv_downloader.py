"""
Unified Complete Master OHLCV Pipeline (Smart Hybrid Fetcher)
================================================──────────────
Strategy:
1. Short Incremental Updates (<= 7 days / Today's data):
   - Fetches via yfinance FIRST (Ultra-fast, 0.2s per stock, no API rate-limit delays).
2. Deep Historical Data (Multi-Year 10-Year Daily & 4-Hour backfills):
   - Fetches via Angel One SmartAPI FIRST (Deep historical depth, max chunking: 2000d Daily, 400d 1H->4H).
3. Disk Caching:
   - Caches yfinance split records to disk (OHLCV/official_splits_cache.json).
   - Instant same-day skip for up-to-date stocks.

Usage:
  python unified_ohlcv_downloader.py                  # Complete end-to-end run
  python unified_ohlcv_downloader.py --phase daily    # Run Phase 1 (Daily stocks)
  python unified_ohlcv_downloader.py --phase 4hour    # Run Phase 2 (4-Hour stocks)
  python unified_ohlcv_downloader.py --phase indices  # Run Phase 3 (Indices)
  python unified_ohlcv_downloader.py --phase adjust   # Run Phase 4 (Corporate Action Adjuster only)
  python unified_ohlcv_downloader.py --reset          # Clear progress and restart
"""

import os
import sys
import json
import time
import logging
import argparse
import requests
import pyotp
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from SmartApi import SmartConnect

# ── CREDENTIALS ──────────────────────────────────────────────────────────────
API_KEY   = "Gu8zxuOs"
CLIENT_ID = "Y52625417"
PIN       = "7777"
TOTP_KEY  = "FF5AIWENWOAETU2YFK5AV77YU4"

# ── PATHS ─────────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_CSV      = os.path.join(BASE_DIR, "Data.csv")
OHLCV_DIR     = os.path.join(BASE_DIR, "OHLCV")
STOCKS_DAILY  = os.path.join(OHLCV_DIR, "Stocks", "Daily")
STOCKS_4H     = os.path.join(OHLCV_DIR, "Stocks", "4Hour")
INDICES_DAILY = os.path.join(OHLCV_DIR, "Indices", "Daily")
INDICES_4H    = os.path.join(OHLCV_DIR, "Indices", "4Hour")
PROGRESS_FILE = os.path.join(OHLCV_DIR, "unified_progress.json")
SCRIP_CACHE   = os.path.join(OHLCV_DIR, "scrip_master_cache.json")
SPLITS_CACHE  = os.path.join(OHLCV_DIR, "official_splits_cache.json")

# ── SETTINGS ─────────────────────────────────────────────────────────────────
DEFAULT_START_DATE = "2015-01-01"
CHUNK_DAYS_DAILY   = 2000   # Max chunk size for Angel One daily
CHUNK_DAYS_1H      = 400    # Max chunk size for Angel One 1H
REQUEST_DELAY_SEC  = 0.3    # Pause between Angel One requests
OHLCV_COLS         = ["Open", "High", "Low", "Close", "Volume"]

# ── SECTORAL & MAJOR INDICES DEFINITIONS ──────────────────────────────────────
INDICES = [
    {"name": "Nifty_50",          "exchange": "NSE", "token": "99926000", "yf_ticker": "^NSEI"},
    {"name": "Nifty_Bank",        "exchange": "NSE", "token": "99926009", "yf_ticker": "^NSEBANK"},
    {"name": "Nifty_FinService",  "exchange": "NSE", "token": "99926037", "yf_ticker": "^NSEFINANCE.NS"},
    {"name": "Nifty_IT",          "exchange": "NSE", "token": "99926008", "yf_ticker": "^CNXIT"},
    {"name": "Nifty_Metal",       "exchange": "NSE", "token": "99926030", "yf_ticker": "^CNXMETAL"},
    {"name": "Nifty_Auto",        "exchange": "NSE", "token": "99926029", "yf_ticker": "^CNXAUTO"},
    {"name": "Nifty_Pharma",      "exchange": "NSE", "token": "99926023", "yf_ticker": "^CNXPHARMA"},
    {"name": "Nifty_FMCG",        "exchange": "NSE", "token": "99926021", "yf_ticker": "^CNXFMCG"},
    {"name": "Nifty_Energy",      "exchange": "NSE", "token": "99926020", "yf_ticker": "^CNXENERGY"},
    {"name": "Nifty_Realty",      "exchange": "NSE", "token": "99926018", "yf_ticker": "^CNXREALTY"},
    {"name": "Nifty_Media",       "exchange": "NSE", "token": "99926031", "yf_ticker": "^CNXMEDIA"},
    {"name": "Nifty_PSUBank",     "exchange": "NSE", "token": "99926025", "yf_ticker": "^CNXPSUBANK"},
    {"name": "Nifty_Infra",       "exchange": "NSE", "token": "99926019", "yf_ticker": "^CNXINFRA"},
    {"name": "Nifty_Commodities", "exchange": "NSE", "token": "99926035", "yf_ticker": "^CNXCOMMODITIES"},
    {"name": "Nifty_PSE",         "exchange": "NSE", "token": "99926024", "yf_ticker": "^CNXPSE"},
    {"name": "Nifty_MNC",         "exchange": "NSE", "token": "99926022", "yf_ticker": "^CNXMNC"},
    {"name": "Nifty_ServSector",  "exchange": "NSE", "token": "99926026", "yf_ticker": "^CNXSERVICE"},
    {"name": "Nifty_Next50",      "exchange": "NSE", "token": "99926013", "yf_ticker": "^NSMIDCP50"},
    {"name": "Nifty_Midcap100",   "exchange": "NSE", "token": "99926011", "yf_ticker": "^CNXMIDCAP"},
    {"name": "Nifty_Midcap50",    "exchange": "NSE", "token": "99926014", "yf_ticker": "^NIFTYMID50.NS"},
    {"name": "Nifty_Smallcap100", "exchange": "NSE", "token": "99926032", "yf_ticker": "^CNXSC"},
    {"name": "Nifty_500",         "exchange": "NSE", "token": "99926004", "yf_ticker": "^CRSLDX"},
    {"name": "Nifty_200",         "exchange": "NSE", "token": "99926033", "yf_ticker": "^CNX200"},
    {"name": "Nifty_100",         "exchange": "NSE", "token": "99926012", "yf_ticker": "^CNX100"},
    {"name": "Nifty_Consumption", "exchange": "NSE", "token": "99926036", "yf_ticker": "^CNXCONSUM"},
    {"name": "Nifty_DivOpps50",   "exchange": "NSE", "token": "99926034", "yf_ticker": "^CNXDIVOPP"},
    {"name": "Nifty_GrowSect15",  "exchange": "NSE", "token": "99926001", "yf_ticker": "^NIFTYGS15.NS"},
    {"name": "Sensex",            "exchange": "BSE", "token": "99919000", "yf_ticker": "^BSESN"},
]

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(BASE_DIR, "unified_ohlcv.log"), encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# Rate limit phrase detector
_RATE_LIMIT_PHRASES = ("access denied", "exceeding access rate", "rate limit", "ag8001", "exceeded", "blocked")

def is_rate_limited(err_str: str) -> bool:
    return any(p in err_str.lower() for p in _RATE_LIMIT_PHRASES)

# ── HELPERS & TRANSFORMATIONS ─────────────────────────────────────────────────

def make_dirs():
    for d in [STOCKS_DAILY, STOCKS_4H, INDICES_DAILY, INDICES_4H]:
        os.makedirs(d, exist_ok=True)

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)

def load_splits_cache() -> dict:
    if os.path.exists(SPLITS_CACHE):
        try:
            with open(SPLITS_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_splits_cache(cache: dict):
    with open(SPLITS_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

def strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if hasattr(df.index, 'tz') and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert('Asia/Kolkata').tz_localize(None)
    return df

def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = strip_tz(df)
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

def save_df(df: pd.DataFrame, path: str, label: str):
    df = normalize_cols(df)
    if df.empty:
        log.warning(f"  No valid OHLCV data to save for {label}")
        return
    if os.path.exists(path):
        try:
            existing = pd.read_csv(path, index_col=0, parse_dates=True)
            if not existing.empty:
                existing = normalize_cols(existing)
                df = pd.concat([existing, df])
                df = df[~df.index.duplicated(keep="last")].sort_index()
        except Exception as e:
            log.warning(f"  Existing file read issue ({os.path.basename(path)}): {e}")
    df.to_csv(path)
    log.info(f"  [OK] Saved {len(df)} rows -> {os.path.basename(path)}")

def inspect_file_date_bounds(file_path: str) -> tuple[datetime | None, datetime | None]:
    if not os.path.exists(file_path):
        return None, None
    try:
        df = pd.read_csv(file_path, index_col=0, parse_dates=True)
        if df.empty:
            return None, None
        df.index = pd.to_datetime(df.index)
        df = df[~df.index.is_na()]
        if df.empty:
            return None, None
        return df.index.min(), df.index.max()
    except Exception:
        return None, None

def get_required_date_ranges(file_path: str, target_start_str: str, target_end_str: str, is_already_done: bool = False, last_updated_date: str = "") -> list[tuple[str, str]]:
    today_str = datetime.now().strftime("%Y-%m-%d")

    if is_already_done and last_updated_date == today_str:
        return []

    target_start = datetime.strptime(target_start_str, "%Y-%m-%d")
    target_end   = datetime.strptime(target_end_str,   "%Y-%m-%d")

    earliest_dt, latest_dt = inspect_file_date_bounds(file_path)

    if earliest_dt is None or latest_dt is None:
        return [(target_start_str, target_end_str)]

    ranges = []

    # Historical Backfill (only if stock has NOT been marked done in prior run)
    if not is_already_done and (earliest_dt - target_start).days > 7:
        backfill_end = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        log.info(f"  [Historical Backfill Needed] File starts at {earliest_dt.strftime('%Y-%m-%d')}, backfilling from {target_start_str} to {backfill_end}")
        ranges.append((target_start_str, backfill_end))

    # Forward Incremental Update
    latest_date_str = latest_dt.strftime("%Y-%m-%d")
    if latest_date_str < target_end_str:
        update_start = (latest_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        if update_start <= target_end_str:
            log.info(f"  [Incremental Update Needed] File ends at {latest_date_str}, updating from {update_start} to {target_end_str}")
            ranges.append((update_start, target_end_str))

    return ranges

def get_date_chunks(start_date: str, end_date: str, chunk_days: int) -> list:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end   = datetime.strptime(end_date,   "%Y-%m-%d")
    chunks = []
    current = start
    while current < end:
        chunk_end = min(current + timedelta(days=chunk_days), end)
        chunks.append((
            current.strftime("%Y-%m-%d 09:00"),
            chunk_end.strftime("%Y-%m-%d 15:30")
        ))
        current = chunk_end + timedelta(days=1)
    return chunks

def resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    if df_1h.empty:
        return pd.DataFrame()
    df = normalize_cols(df_1h)
    if df.empty:
        return pd.DataFrame()
    df_4h = df.resample("4h", offset="9h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna(subset=["Open", "Close"])
    return df_4h

# ── OFFICIAL CORPORATE ACTIONS / SPLIT ADJUSTER MODULE (CACHED) ───────────────

def get_official_splits_cached(symbol: str, splits_cache: dict) -> dict:
    symbol = symbol.strip().upper()
    today_str = datetime.now().strftime("%Y-%m-%d")

    if symbol in splits_cache:
        cached_entry = splits_cache[symbol]
        return cached_entry.get("splits", {})

    yf_ticker = f"{symbol}.NS"
    split_dict = {}
    try:
        t = yf.Ticker(yf_ticker)
        splits = t.splits
        if splits is not None and not splits.empty:
            valid_splits = splits[splits != 1.0]
            for dt, val in valid_splits.items():
                dt_str = pd.to_datetime(dt).strftime("%Y-%m-%d")
                split_dict[dt_str] = float(val)
    except Exception:
        pass

    splits_cache[symbol] = {
        "last_checked": today_str,
        "splits": split_dict
    }
    save_splits_cache(splits_cache)
    return split_dict

def adjust_file_with_official_splits(path: str, symbol: str, split_dict: dict = None, splits_cache: dict = None) -> bool:
    if not os.path.exists(path):
        return False

    if split_dict is None:
        if splits_cache is None:
            splits_cache = load_splits_cache()
        split_dict = get_official_splits_cached(symbol, splits_cache)

    if not split_dict:
        return False

    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.empty or len(df) < 5:
            return False

        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)
        was_modified = False

        for split_date_str, factor in sorted(split_dict.items(), reverse=True):
            if factor <= 0 or factor == 1.0:
                continue

            pre_split_df = df[df.index < split_date_str]
            post_split_df = df[df.index >= split_date_str]

            if pre_split_df.empty or post_split_df.empty:
                continue

            last_pre_close = pre_split_df["Close"].iloc[-1]
            first_post_close = post_split_df["Close"].iloc[0]

            if first_post_close <= 0:
                continue

            ratio = last_pre_close / first_post_close

            if abs(ratio - factor) / factor < 0.25:
                mask = df.index < split_date_str
                df.loc[mask, ["Open", "High", "Low", "Close"]] = df.loc[mask, ["Open", "High", "Low", "Close"]] / factor
                df.loc[mask, "Volume"] = df.loc[mask, "Volume"] * factor
                was_modified = True
                log.info(f"  [OK] Split Adjuster: Applied {factor}x split before {split_date_str} to {os.path.basename(path)}")

        if was_modified:
            df.to_csv(path)
            return True
    except Exception as e:
        log.warning(f"  Split Adjuster error on {os.path.basename(path)}: {e}")

    return False

# ── ANGEL ONE AUTH & SCRIP MASTER ─────────────────────────────────────────────

def login_angel_one() -> SmartConnect | None:
    log.info("Logging in to Angel One SmartAPI...")
    try:
        totp_code = pyotp.TOTP(TOTP_KEY).now()
        smart_api = SmartConnect(api_key=API_KEY)
        session = smart_api.generateSession(CLIENT_ID, PIN, totp_code)
        if session and session.get("status"):
            name = session["data"].get("name", "N/A")
            log.info(f"Angel One Login SUCCESSFUL! Welcome, {name}")
            return smart_api
        else:
            log.warning(f"Angel One Login FAILED: {session}")
            return None
    except Exception as e:
        log.warning(f"Angel One Login Exception: {e}")
        return None

def load_scrip_master() -> pd.DataFrame:
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(SCRIP_CACHE):
        mtime = datetime.fromtimestamp(os.path.getmtime(SCRIP_CACHE)).strftime("%Y-%m-%d")
        if mtime == today:
            try:
                with open(SCRIP_CACHE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                df = pd.DataFrame(data)
                log.info(f"Scrip Master loaded from cache: {len(df)} instruments")
                return df
            except Exception:
                pass
    log.info("Downloading Scrip Master from Angel One...")
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    for attempt in range(1, 4):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            data = r.json()
            os.makedirs(OHLCV_DIR, exist_ok=True)
            with open(SCRIP_CACHE, "w", encoding="utf-8") as f:
                json.dump(data, f)
            df = pd.DataFrame(data)
            log.info(f"Scrip Master downloaded & cached: {len(df)} instruments")
            return df
        except Exception as e:
            log.warning(f"Scrip Master download attempt {attempt}/3 failed: {e}")
            time.sleep(5)
    return pd.DataFrame()

def resolve_angel_token(symbol: str, scrips: pd.DataFrame) -> tuple[str, str] | None:
    if scrips.empty:
        return None
    match = scrips[(scrips["exch_seg"] == "NSE") & (scrips["symbol"] == f"{symbol}-EQ")]
    if not match.empty:
        return ("NSE", str(match.iloc[0]["token"]))
    match = scrips[(scrips["exch_seg"] == "NSE") & (scrips["symbol"] == symbol)]
    if not match.empty:
        return ("NSE", str(match.iloc[0]["token"]))
    match = scrips[(scrips["exch_seg"] == "BSE") & (scrips["symbol"] == symbol)]
    if not match.empty:
        return ("BSE", str(match.iloc[0]["token"]))
    return None

# ── ANGEL ONE CANDLE FETCHING ─────────────────────────────────────────────────

def fetch_angel_candles(smart_api: SmartConnect, exchange: str, token: str, interval: str, from_dt: str, to_dt: str, retries: int = 3) -> tuple[list, bool]:
    if smart_api is None:
        return [], True
    params = {
        "exchange":    exchange,
        "symboltoken": token,
        "interval":    interval,
        "fromdate":    from_dt,
        "todate":      to_dt,
    }
    backoff = 10
    for attempt in range(1, retries + 1):
        try:
            response = smart_api.getCandleData(params)
            if response and response.get("status") and response.get("data"):
                return response["data"], False
            errorcode = str(response.get("errorcode", "")) if response else ""
            errmsg    = str(response.get("message",   "")) if response else ""
            if is_rate_limited(errorcode + errmsg):
                log.warning(f"  [Angel One Rate Limit] attempt {attempt}/{retries}, sleeping {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
            else:
                time.sleep(1)
        except Exception as e:
            err_str = str(e)
            if is_rate_limited(err_str):
                log.warning(f"  [Angel One Exceeded Limit] {err_str}")
                return [], True
            time.sleep(1)
    return [], False

# ── YFINANCE FALLBACK FETCHING ────────────────────────────────────────────────

def fetch_yfinance_stock(symbol: str, interval: str, start_date: str, end_date: str) -> pd.DataFrame:
    yf_ticker = f"{symbol.strip().upper()}.NS"
    try:
        if interval == "1d":
            df = yf.download(yf_ticker, start=start_date, end=end_date, interval="1d", auto_adjust=True, progress=False)
        else:
            start_1h = max(
                datetime.strptime(start_date, "%Y-%m-%d"),
                datetime.now() - timedelta(days=729)
            ).strftime("%Y-%m-%d")
            df = yf.download(yf_ticker, start=start_1h, end=end_date, interval="1h", auto_adjust=True, progress=False)
        return normalize_cols(df)
    except Exception as e:
        log.warning(f"  [yfinance Error] {symbol}: {e}")
        return pd.DataFrame()

# ── PHASE 1: ALL STOCKS DAILY DATA ───────────────────────────────────────────

def run_phase_1_stocks_daily(smart_api: SmartConnect, scrips: pd.DataFrame, stock_symbols: list, target_start: str, progress: dict):
    total = len(stock_symbols)
    today_str = datetime.now().strftime("%Y-%m-%d")
    log.info(f"PHASE 1: STOCKS DAILY DATA ({total} STOCKS)")

    angel_quota_exceeded = (smart_api is None)

    for i, symbol in enumerate(stock_symbols, 1):
        safe_symbol = symbol.replace("/", "_").replace(" ", "_")
        out_path    = os.path.join(STOCKS_DAILY, f"{safe_symbol}_daily.csv")

        prog_entry = progress.get(f"STOCK_{symbol}", {})
        is_done = (prog_entry.get("daily") == "done")
        last_updated = prog_entry.get("daily_last_updated", "")

        required_ranges = get_required_date_ranges(out_path, target_start, today_str, is_already_done=is_done, last_updated_date=last_updated)

        if not required_ranges:
            earliest_dt, latest_dt = inspect_file_date_bounds(out_path)
            e_str = earliest_dt.strftime('%Y-%m-%d') if earliest_dt else 'N/A'
            l_str = latest_dt.strftime('%Y-%m-%d') if latest_dt else 'N/A'
            log.info(f"[{i}/{total}] Daily: {symbol} -> [SKIP] Fully up to date ({e_str} to {l_str})")
            progress.setdefault(f"STOCK_{symbol}", {})["daily"] = "done"
            progress[f"STOCK_{symbol}"]["daily_last_updated"] = today_str
            continue

        log.info(f"[{i}/{total}] Daily: {symbol} -> Fetching {len(required_ranges)} range(s)...")

        for req_start, req_end in required_ranges:
            downloaded = False
            days_span = (datetime.strptime(req_end, "%Y-%m-%d") - datetime.strptime(req_start, "%Y-%m-%d")).days

            # Fast Strategy: For short incremental updates (<= 7 days), use yfinance FIRST
            if days_span <= 7:
                log.info(f"  -> Fast yfinance update [{req_start} to {req_end}] for {symbol}...")
                df_yf = fetch_yfinance_stock(symbol, "1d", req_start, req_end)
                if not df_yf.empty:
                    save_df(df_yf, out_path, f"{symbol} Daily (yfinance)")
                    downloaded = True

            # Deep Historical Strategy: For multi-year backfills (> 7 days), use Angel One FIRST
            if not downloaded and not angel_quota_exceeded:
                token_res = resolve_angel_token(symbol, scrips)
                if token_res:
                    exchange, token = token_res
                    chunks = get_date_chunks(req_start, req_end, CHUNK_DAYS_DAILY)
                    all_chunks = []
                    for from_dt, to_dt in chunks:
                        raw, hard_limit = fetch_angel_candles(smart_api, exchange, token, "ONE_DAY", from_dt, to_dt)
                        if hard_limit:
                            log.warning(f"  [Angel One Quota Hit] Switching to yfinance fallback.")
                            angel_quota_exceeded = True
                            break
                        if raw:
                            df_c = pd.DataFrame(raw, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
                            df_c["timestamp"] = pd.to_datetime(df_c["timestamp"])
                            df_c.set_index("timestamp", inplace=True)
                            all_chunks.append(df_c)
                        time.sleep(REQUEST_DELAY_SEC)

                    if all_chunks and not angel_quota_exceeded:
                        df_final = pd.concat(all_chunks)
                        save_df(df_final, out_path, f"{symbol} Daily (Angel One)")
                        downloaded = True

            if not downloaded:
                log.info(f"  -> Fallback yfinance fetch [{req_start} to {req_end}] for {symbol}...")
                df_yf = fetch_yfinance_stock(symbol, "1d", req_start, req_end)
                if not df_yf.empty:
                    save_df(df_yf, out_path, f"{symbol} Daily (yfinance)")
                    downloaded = True

        progress.setdefault(f"STOCK_{symbol}", {})["daily"] = "done"
        progress[f"STOCK_{symbol}"]["daily_last_updated"] = today_str
        save_progress(progress)

# ── PHASE 2: ALL STOCKS 4-HOUR DATA ──────────────────────────────────────────

def run_phase_2_stocks_4hour(smart_api: SmartConnect, scrips: pd.DataFrame, stock_symbols: list, target_start: str, progress: dict):
    total = len(stock_symbols)
    today_str = datetime.now().strftime("%Y-%m-%d")
    log.info(f"PHASE 2: STOCKS 4-HOUR DATA ({total} STOCKS)")

    angel_quota_exceeded = (smart_api is None)

    for i, symbol in enumerate(stock_symbols, 1):
        safe_symbol = symbol.replace("/", "_").replace(" ", "_")
        out_path    = os.path.join(STOCKS_4H, f"{safe_symbol}_4hour.csv")

        prog_entry = progress.get(f"STOCK_{symbol}", {})
        is_done = (prog_entry.get("4hour") == "done")
        last_updated = prog_entry.get("4hour_last_updated", "")

        required_ranges = get_required_date_ranges(out_path, target_start, today_str, is_already_done=is_done, last_updated_date=last_updated)

        if not required_ranges:
            earliest_dt, latest_dt = inspect_file_date_bounds(out_path)
            e_str = earliest_dt.strftime('%Y-%m-%d') if earliest_dt else 'N/A'
            l_str = latest_dt.strftime('%Y-%m-%d') if latest_dt else 'N/A'
            log.info(f"[{i}/{total}] 4-Hour: {symbol} -> [SKIP] Fully up to date ({e_str} to {l_str})")
            progress.setdefault(f"STOCK_{symbol}", {})["4hour"] = "done"
            progress[f"STOCK_{symbol}"]["4hour_last_updated"] = today_str
            continue

        log.info(f"[{i}/{total}] 4-Hour: {symbol} -> Fetching {len(required_ranges)} range(s)...")

        for req_start, req_end in required_ranges:
            downloaded = False
            days_span = (datetime.strptime(req_end, "%Y-%m-%d") - datetime.strptime(req_start, "%Y-%m-%d")).days

            # Fast Strategy: For short incremental updates (<= 7 days), use yfinance FIRST
            if days_span <= 7:
                log.info(f"  -> Fast yfinance 4H update [{req_start} to {req_end}] for {symbol}...")
                df_1h_yf = fetch_yfinance_stock(symbol, "1h", req_start, req_end)
                if not df_1h_yf.empty:
                    df_4h = resample_to_4h(df_1h_yf)
                    if not df_4h.empty:
                        save_df(df_4h, out_path, f"{symbol} 4H (yfinance)")
                        downloaded = True

            # Deep Historical Strategy: For multi-year backfills (> 7 days), use Angel One FIRST
            if not downloaded and not angel_quota_exceeded:
                token_res = resolve_angel_token(symbol, scrips)
                if token_res:
                    exchange, token = token_res
                    chunks = get_date_chunks(req_start, req_end, CHUNK_DAYS_1H)
                    all_chunks = []
                    for from_dt, to_dt in chunks:
                        raw, hard_limit = fetch_angel_candles(smart_api, exchange, token, "ONE_HOUR", from_dt, to_dt)
                        if hard_limit:
                            log.warning(f"  [Angel One Quota Hit] Switching to yfinance fallback for 4H.")
                            angel_quota_exceeded = True
                            break
                        if raw:
                            df_c = pd.DataFrame(raw, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
                            df_c["timestamp"] = pd.to_datetime(df_c["timestamp"])
                            df_c.set_index("timestamp", inplace=True)
                            all_chunks.append(df_c)
                        time.sleep(REQUEST_DELAY_SEC)

                    if all_chunks and not angel_quota_exceeded:
                        df_1h = pd.concat(all_chunks)
                        df_4h = resample_to_4h(df_1h)
                        if not df_4h.empty:
                            save_df(df_4h, out_path, f"{symbol} 4H (Angel One)")
                            downloaded = True

            if not downloaded:
                log.info(f"  -> Fallback yfinance fetch [{req_start} to {req_end}] for {symbol}...")
                df_1h_yf = fetch_yfinance_stock(symbol, "1h", req_start, req_end)
                if not df_1h_yf.empty:
                    df_4h = resample_to_4h(df_1h_yf)
                    if not df_4h.empty:
                        save_df(df_4h, out_path, f"{symbol} 4H (yfinance)")
                        downloaded = True

        progress.setdefault(f"STOCK_{symbol}", {})["4hour"] = "done"
        progress[f"STOCK_{symbol}"]["4hour_last_updated"] = today_str
        save_progress(progress)

# ── PHASE 3: SECTORAL & MAJOR INDICES ─────────────────────────────────────────

def run_phase_3_indices(smart_api: SmartConnect, target_start: str, progress: dict):
    total = len(INDICES)
    today_str = datetime.now().strftime("%Y-%m-%d")
    log.info(f"PHASE 3: SECTOR & MAJOR INDICES ({total} INDICES)")

    for i, idx in enumerate(INDICES, 1):
        name      = idx["name"]
        exchange  = idx["exchange"]
        token     = idx["token"]
        yf_ticker = idx["yf_ticker"]

        daily_path = os.path.join(INDICES_DAILY, f"{name}_daily.csv")
        fourh_path = os.path.join(INDICES_4H,    f"{name}_4hour.csv")

        is_done_d = (progress.get(f"INDEX_{name}", {}).get("daily") == "done")
        req_daily = get_required_date_ranges(daily_path, target_start, today_str, is_already_done=is_done_d)
        if not req_daily:
            log.info(f"[{i}/{total}] Index Daily: {name} -> [SKIP] Up to date")
        else:
            log.info(f"[{i}/{total}] Index Daily: {name} -> Fetching update...")
            for req_start, req_end in req_daily:
                downloaded = False
                days_span = (datetime.strptime(req_end, "%Y-%m-%d") - datetime.strptime(req_start, "%Y-%m-%d")).days
                if days_span <= 7:
                    try:
                        df_yf = yf.download(yf_ticker, start=req_start, end=req_end, interval="1d", auto_adjust=True, progress=False)
                        df_yf = normalize_cols(df_yf)
                        if not df_yf.empty:
                            save_df(df_yf, daily_path, f"{name} Daily (yf)")
                            downloaded = True
                    except Exception:
                        pass
                if not downloaded and smart_api:
                    chunks = get_date_chunks(req_start, req_end, CHUNK_DAYS_DAILY)
                    all_c = []
                    for f_dt, t_dt in chunks:
                        raw, _ = fetch_angel_candles(smart_api, exchange, token, "ONE_DAY", f_dt, t_dt)
                        if raw:
                            df_c = pd.DataFrame(raw, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
                            df_c["timestamp"] = pd.to_datetime(df_c["timestamp"])
                            df_c.set_index("timestamp", inplace=True)
                            all_c.append(df_c)
                        time.sleep(0.3)
                    if all_c:
                        save_df(pd.concat(all_c), daily_path, f"{name} Daily (Angel)")
                        downloaded = True
        progress.setdefault(f"INDEX_{name}", {})["daily"] = "done"

        is_done_4h = (progress.get(f"INDEX_{name}", {}).get("4hour") == "done")
        req_4h = get_required_date_ranges(fourh_path, target_start, today_str, is_already_done=is_done_4h)
        if not req_4h:
            log.info(f"[{i}/{total}] Index 4-Hour: {name} -> [SKIP] Up to date")
        else:
            log.info(f"[{i}/{total}] Index 4-Hour: {name} -> Fetching update...")
            for req_start, req_end in req_4h:
                downloaded = False
                days_span = (datetime.strptime(req_end, "%Y-%m-%d") - datetime.strptime(req_start, "%Y-%m-%d")).days
                if days_span <= 7:
                    try:
                        start_1h = max(datetime.strptime(req_start, "%Y-%m-%d"), datetime.now() - timedelta(days=729)).strftime("%Y-%m-%d")
                        df_yf = yf.download(yf_ticker, start=start_1h, end=req_end, interval="1h", auto_adjust=True, progress=False)
                        df_4h = resample_to_4h(df_yf)
                        if not df_4h.empty:
                            save_df(df_4h, fourh_path, f"{name} 4H (yf)")
                            downloaded = True
                    except Exception:
                        pass
                if not downloaded and smart_api:
                    chunks = get_date_chunks(req_start, req_end, CHUNK_DAYS_1H)
                    all_c = []
                    for f_dt, t_dt in chunks:
                        raw, _ = fetch_angel_candles(smart_api, exchange, token, "ONE_HOUR", f_dt, t_dt)
                        if raw:
                            df_c = pd.DataFrame(raw, columns=["timestamp", "Open", "High", "Low", "Close", "Volume"])
                            df_c["timestamp"] = pd.to_datetime(df_c["timestamp"])
                            df_c.set_index("timestamp", inplace=True)
                            all_c.append(df_c)
                        time.sleep(0.3)
                    if all_c:
                        df_4h = resample_to_4h(pd.concat(all_c))
                        if not df_4h.empty:
                            save_df(df_4h, fourh_path, f"{name} 4H (Angel)")
                            downloaded = True
        progress.setdefault(f"INDEX_{name}", {})["4hour"] = "done"

        save_progress(progress)

# ── PHASE 4: GLOBAL CORPORATE ACTION ADJUSTMENT AUDIT (CACHED & FAST) ─────────

def run_phase_4_global_adjustments(stock_symbols: list):
    log.info("PHASE 4: RUNNING ROBUST CORPORATE ACTION ADJUSTER & REBUILD PIPELINE")
    adjuster_script = os.path.join(BASE_DIR, "corporate_action_adjuster.py")
    os.system(f'python "{adjuster_script}" --fix --rebuild')
    log.info("[OK] Phase 4 Complete! All stock data normalized and all sector indices/terminals rebuilt.")

# ── MAIN DRIVER ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Unified Master OHLCV Pipeline (Smart Hybrid)")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="Start date YYYY-MM-DD (default: 2015-01-01)")
    parser.add_argument("--phase", choices=["all", "daily", "4hour", "indices", "adjust"], default="all", help="Phase selection")
    parser.add_argument("--reset", action="store_true", help="Clear progress and restart")
    args = parser.parse_args()

    make_dirs()

    if args.reset and os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        log.info("Progress cleared. Restarting fresh.")

    progress  = load_progress()
    smart_api = login_angel_one() if args.phase != "adjust" else None
    scrips    = load_scrip_master() if smart_api else pd.DataFrame()

    try:
        df_stocks = pd.read_csv(DATA_CSV, encoding="utf-8")
    except UnicodeDecodeError:
        df_stocks = pd.read_csv(DATA_CSV, encoding="latin1")

    symbol_col = next((c for c in df_stocks.columns if c.lower() == "symbol"), None)
    if symbol_col is None:
        log.error("Could not find 'Symbol' column in Data.csv")
        sys.exit(1)

    stock_symbols = df_stocks[symbol_col].dropna().astype(str).str.strip().unique().tolist()
    log.info(f"Loaded {len(stock_symbols)} stocks from {DATA_CSV}")

    if args.phase in ["all", "daily"]:
        run_phase_1_stocks_daily(smart_api, scrips, stock_symbols, args.start_date, progress)

    if args.phase in ["all", "4hour"]:
        run_phase_2_stocks_4hour(smart_api, scrips, stock_symbols, args.start_date, progress)

    if args.phase in ["all", "indices"]:
        run_phase_3_indices(smart_api, args.start_date, progress)

    if args.phase in ["all", "adjust"]:
        run_phase_4_global_adjustments(stock_symbols)

    log.info(f"MASTER OHLCV PIPELINE FINISHED SUCCESSFULLY! Output location: {OHLCV_DIR}")

if __name__ == "__main__":
    main()
