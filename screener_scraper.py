"""
Screener.in Production Financial Scraper & Incremental Updater
===============================================================
Features:
1. Full Financial Scraper:
   - Key Metrics (Market Cap, P/E, Book Value, Dividend Yield, ROCE, ROE, etc.)
   - Pros & Cons
   - Quarterly Results (Sales, Expenses, Operating Profit, OPM %, Net Profit, EPS, etc.)
   - Profit & Loss (Annual history)
   - Compounded Growth Rates (Sales, Profit, Stock CAGR, ROE - ranges-table)
   - Balance Sheet
   - Cash Flows
   - Ratios (ROCE %, ROE %, Debtor Days, Inventory Days, etc.)
   - Shareholding Pattern (Quarterly & Yearly) - both divs parsed correctly
2. Smart Incremental Updating:
   - Tracks last scraped timestamp for each company.
   - When re-run, automatically checks Screener for newly published quarters (e.g. Q1/Q2/Q3/Q4)
     or new annual reports.
   - Merges new metrics into existing Data_Enhanced.csv without losing historical data.
3. Proxy & Anti-Bot Protection:
   - Auto-detects local Tor proxy (ports 9150 / 9050).
   - Exponential backoff & rate-limit (429 / 503) handling.
   - User-Agent rotation.

Bug Fixes vs original:
  - FIX 1: Shareholding yearly table detected via ancestor div#yearly-shp (not unreliable t_idx).
  - FIX 2: Key Metrics (Market Cap, PE, ROE, etc.) from top-ratios ul now scraped.
  - FIX 3: ranges-table (Compounded Growth) now captured for ALL sections, not just profit-loss.
  - FIX 4: Metric name cleanup: removes trailing '+' chars and 'Raw PDF' rows.
  - FIX 5: Unicode-safe output (encode errors suppressed).
  - FIX 6: Header row uses first <tr> in <thead>, handles colspan headers gracefully.
  - FIX 7: Empty/None value cells are skipped instead of stored as empty strings.

Usage:
  python screener_scraper.py                  # Scrape pending or check updates (>7 days old)
  python screener_scraper.py --update         # Force check all companies for newly declared results
  python screener_scraper.py --check-days 14  # Re-check companies last scraped > 14 days ago
"""

import os
import sys
import time
import json
import random
import logging
import argparse
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── LOGGING SETUP ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%H:%M:%S"
)

TRY_CURL_CFFI = False
try:
    from curl_cffi import requests as curl_requests
    TRY_CURL_CFFI = True
except ImportError:
    pass

try:
    import requests
except ImportError:
    logging.error("The 'requests' module is required. Please run: pip install requests")
    sys.exit(1)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
]

BACKOFF_LOCK = Lock()
LAST_429_TIME = 0
GLOBAL_PROXIES = None

def auto_detect_tor_proxy():
    test_ports = [9150, 9050]
    for port in test_ports:
        proxy_url = f"socks5://127.0.0.1:{port}"
        try:
            r = requests.get("https://api.ipify.org?format=json", proxies={"http": proxy_url, "https": proxy_url}, timeout=2)
            if r.status_code == 200:
                logging.info(f"Auto-detected active Tor proxy on port {port} (Exit IP: {r.json().get('ip')})")
                return {"http": proxy_url, "https": proxy_url}
        except Exception:
            pass
    return None

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.screener.in/",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }

def handle_rate_limit_backoff():
    global LAST_429_TIME
    with BACKOFF_LOCK:
        now = time.time()
        if now - LAST_429_TIME < 15:
            pause_duration = 15 - (now - LAST_429_TIME)
            logging.info(f"Rate limited (429/503). Pausing worker threads for {pause_duration:.1f}s...")
            time.sleep(pause_duration)
        LAST_429_TIME = time.time()

def _html_has_data(html: str) -> bool:
    """
    Returns True if the HTML has at least one financial table with real column
    headers (period labels like 'Mar 2024'). Returns False if tables are empty
    shells (consolidated URL exists but has no data for this company).
    """
    try:
        from bs4 import BeautifulSoup as _BS
        soup = _BS(html, "html.parser")
        for sec_id in ["quarters", "profit-loss", "balance-sheet"]:
            section = soup.find("section", id=sec_id)
            if not section:
                continue
            table = section.find("table", class_="data-table")
            if not table:
                continue
            thead = table.find("thead")
            if not thead:
                continue
            # Check if there are any non-blank <th> cells (i.e. period headers)
            ths = [th.get_text(strip=True) for th in thead.find_all("th")]
            non_blank = [t for t in ths if t]
            if non_blank:
                return True
        return False
    except Exception:
        return True  # If check fails, assume data is present and proceed

def fetch_url_html(url: str, symbol: str) -> str:
    """
    Fetches HTML from a specific Screener.in URL with retries, backoff, and rate-limit handling.
    """
    for attempt in range(4):
        try:
            headers = get_headers()
            if TRY_CURL_CFFI:
                r = curl_requests.get(url, headers=headers, proxies=GLOBAL_PROXIES, impersonate="chrome120", timeout=15)
            else:
                r = requests.get(url, headers=headers, proxies=GLOBAL_PROXIES, timeout=15)

            if r.status_code == 200:
                if _html_has_data(r.text):
                    return r.text
                else:
                    return None
            elif r.status_code == 404:
                return None
            elif r.status_code in [429, 503]:
                logging.warning(f"Rate limited ({r.status_code}) for {symbol} on {url} (Attempt {attempt+1}/4). Backing off...")
                handle_rate_limit_backoff()
                time.sleep(3 * (attempt + 1))
        except Exception:
            time.sleep(2)
    return None

def _clean_metric_name(raw: str) -> str:
    """Strip trailing '+', non-breaking spaces, and other noise from metric names."""
    return raw.replace("+", "").replace("\xa0", " ").replace("\u20b9", "Rs").strip()

def _clean_value(raw: str) -> str:
    """Strip whitespace and currency symbols from values. Return empty string if blank."""
    return raw.replace("\xa0", " ").strip()

def is_valid_financial_value(val: str) -> bool:
    """
    Returns True if the value is a valid financial number or text.
    Returns False if it is just a currency/percent symbol or empty.
    """
    if not val:
        return False
    # Strip symbols
    val_clean = val.replace("\u20b9", "").replace("₹", "").replace("%", "").replace("Rs", "").replace("/", "").replace("-", "").strip()
    if not val_clean:
        return False
    # Must contain at least one digit or letter
    return any(c.isdigit() or c.isalpha() for c in val_clean)

def smart_merge(dict_std: dict, dict_cons: dict) -> dict:
    """
    Merges standalone and consolidated parsed tables.
    Consolidated is prioritized (overwrites standalone) but only if it contains
    a valid populated value. If consolidated is empty/invalid, standalone is preserved.
    """
    merged = dict_std.copy()
    for k, v in dict_cons.items():
        if k in merged:
            if is_valid_financial_value(v):
                merged[k] = v
            else:
                if not is_valid_financial_value(merged[k]):
                    merged[k] = v
        else:
            merged[k] = v
    return merged

def _find_ancestor_with_id(tag, target_id: str) -> bool:
    """Walk up the DOM tree to check if any ancestor has a specific id."""
    el = tag.parent
    while el is not None:
        if el.get("id") == target_id:
            return True
        el = el.parent
    return False

def parse_screener_tables(html_content: str) -> dict:
    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    company_data = {}

    # ── FIX 2: Key Metrics from top-ratios ────────────────────────────────────
    top_ratios_ul = soup.find("ul", id="top-ratios")
    if top_ratios_ul:
        for li in top_ratios_ul.find_all("li"):
            name_span = li.find("span", class_="name")
            # value can be in .value or .number span
            val_span = li.find("span", class_="value") or li.find("span", class_="number")
            if name_span and val_span:
                key = _clean_metric_name(name_span.get_text(strip=True))
                val = _clean_value(val_span.get_text(strip=True))
                if key and val:
                    company_data[f"Key Metrics - {key}"] = val

    # ── Pros & Cons ───────────────────────────────────────────────────────────
    analysis = soup.find("section", id="analysis")
    if analysis:
        pros_div = analysis.find("div", class_="pros")
        if pros_div:
            pros_list = [li.get_text(strip=True) for li in pros_div.find_all("li")]
            if pros_list:
                company_data["Analysis - Pros"] = " ; ".join(pros_list)

        cons_div = analysis.find("div", class_="cons")
        if cons_div:
            cons_list = [li.get_text(strip=True) for li in cons_div.find_all("li")]
            if cons_list:
                company_data["Analysis - Cons"] = " ; ".join(cons_list)

    # ── Main section table extractor ──────────────────────────────────────────
    def extract_section_data(section_id: str, section_title: str):
        section = soup.find("section", id=section_id)
        if not section:
            return

        # ── FIX 1: data-table rows (main financial data) ────────────────────
        tables = section.find_all("table", class_="data-table")
        for table in tables:
            # ── FIX 1: Correctly identify quarterly vs yearly shareholding ──
            if section_id == "shareholding":
                # Walk DOM tree: if ancestor div has id='yearly-shp' → yearly
                if _find_ancestor_with_id(table, "yearly-shp"):
                    prefix = "Shareholding Pattern (Yearly)"
                else:
                    prefix = "Shareholding Pattern (Quarterly)"
            else:
                prefix = section_title

            thead = table.find("thead")
            if not thead:
                continue

            # ── FIX 6: Collect headers from first <tr>, skip blank first th ─
            headers = []
            tr_head = thead.find("tr")
            if tr_head:
                for th in tr_head.find_all("th"):
                    text = th.get_text(strip=True)
                    if text:
                        headers.append(text)

            if not headers:
                continue

            tbody = table.find("tbody")
            if not tbody:
                continue

            for tr in tbody.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if not cells:
                    continue

                # ── FIX 4: Clean metric name, skip 'Raw PDF' and blanks ────
                raw_metric = cells[0].get_text(" ", strip=True)
                metric_name = _clean_metric_name(raw_metric)

                if not metric_name or metric_name.lower() in ["raw pdf", "pdf"]:
                    continue

                value_cells = cells[1:]
                for idx, cell in enumerate(value_cells):
                    if idx < len(headers):
                        period = headers[idx]
                        # ── FIX 7: Skip empty values ─────────────────────
                        value = _clean_value(cell.get_text(strip=True))
                        if value:
                            column_name = f"{prefix} - {metric_name} - {period}"
                            company_data[column_name] = value

        # ── FIX 3: ranges-table (Compounded Growth / CAGR) for ALL sections ─
        range_tables = section.find_all("table", class_="ranges-table")
        for rt in range_tables:
            th_el = rt.find("th")
            growth_title = _clean_metric_name(th_el.get_text(strip=True)) if th_el else "Compounded Growth"
            for tr in rt.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) == 2:
                    period_name = _clean_value(tds[0].get_text(strip=True)).rstrip(":")
                    val = _clean_value(tds[1].get_text(strip=True))
                    if period_name and val:
                        column_name = f"{section_title} - {growth_title} - {period_name}"
                        company_data[column_name] = val

    sections_map = [
        ("quarters",      "Quarterly Results"),
        ("profit-loss",   "Profit & Loss"),
        ("balance-sheet", "Balance Sheet"),
        ("cash-flow",     "Cash Flows"),
        ("ratios",        "Ratios"),
        ("shareholding",  "Shareholding Pattern"),
    ]

    for sec_id, sec_title in sections_map:
        extract_section_data(sec_id, sec_title)

    return company_data

def scrape_company_task(row_tuple, delay_between, existing_entry: dict = None):
    idx, row = row_tuple
    symbol = str(row["Symbol"]).strip()

    # Polite delay before requests
    time.sleep(random.uniform(delay_between * 0.8, delay_between * 1.5))

    cons_url = f"https://www.screener.in/company/{symbol}/consolidated/"
    std_url  = f"https://www.screener.in/company/{symbol}/"

    # Fetch standalone first (since it always exists, and some companies don't have consolidated)
    html_std = fetch_url_html(std_url, symbol)
    standalone_metrics = parse_screener_tables(html_std) if html_std else {}

    # Wait a small delay between requests to avoid rate limits
    time.sleep(random.uniform(0.3, 0.7))

    # Fetch consolidated
    html_cons = fetch_url_html(cons_url, symbol)
    consolidated_metrics = parse_screener_tables(html_cons) if html_cons else {}

    # Smart Merge: standalone first, then consolidated (overwriting only if consolidated has a valid value)
    new_metrics = smart_merge(standalone_metrics, consolidated_metrics)

    if new_metrics:
        new_keys_found = 0
        if existing_entry:
            old_keys = set(existing_entry.keys())
            new_keys = set(new_metrics.keys())
            added_keys = new_keys - old_keys
            new_keys_found = len(added_keys)

        new_metrics["_last_scraped_timestamp"] = datetime.now().isoformat()

        if existing_entry and new_keys_found > 0:
            logging.info(f"[{idx + 1}] UPDATED '{symbol}': Found {new_keys_found} NEW data points from Merged Standalone + Consolidated")
        else:
            logging.info(f"[{idx + 1}] Scraped '{symbol}': {len(new_metrics)-1} data points from Merged Standalone + Consolidated")

        return idx, symbol, new_metrics, True
    else:
        logging.warning(f"[{idx + 1}] Failed to scrape data for '{symbol}' (both Standalone & Consolidated returned 0 metrics)")
        return idx, symbol, existing_entry or {}, False

def main():
    global GLOBAL_PROXIES

    parser = argparse.ArgumentParser(description="Screener.in Production Scraper & Incremental Results Updater")
    parser.add_argument("--input",      default=r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management\Data.csv", help="Input universe CSV path")
    parser.add_argument("--output",     default=r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management\Data_Enhanced.csv", help="Output enhanced CSV path")
    parser.add_argument("--progress",   default=r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management\screener_progress.json", help="Progress JSON path")
    parser.add_argument("--workers",    type=int, default=2, help="Number of worker threads")
    parser.add_argument("--delay",      type=float, default=1.5, help="Delay between requests (sec)")
    parser.add_argument("--update",     action="store_true", help="Force re-check ALL companies for new quarterly/annual results")
    parser.add_argument("--check-days", type=int, default=7, help="Re-check companies last scraped more than N days ago (default: 7)")
    parser.add_argument("--proxy",      default=None, help="Proxy URL (e.g. socks5://127.0.0.1:9150)")
    args = parser.parse_args()

    if args.proxy:
        GLOBAL_PROXIES = {"http": args.proxy, "https": args.proxy}
        logging.info(f"Using specified proxy: {args.proxy}")
    else:
        tor_proxies = auto_detect_tor_proxy()
        if tor_proxies:
            GLOBAL_PROXIES = tor_proxies

    if not os.path.exists(args.input):
        logging.error(f"Input universe CSV not found: {args.input}")
        return

    df = pd.read_csv(args.input)
    logging.info(f"Loaded {len(df)} companies from {args.input}")

    scraped_progress = {}
    if os.path.exists(args.progress):
        try:
            with open(args.progress, "r", encoding="utf-8") as f:
                scraped_progress = json.load(f)
            logging.info(f"Loaded progress checkpoint: {len(scraped_progress)} companies in database.")
        except Exception as e:
            logging.warning(f"Could not load progress checkpoint: {e}")

    cutoff_time = datetime.now() - timedelta(days=args.check_days)
    rows_to_process = []

    for r in df.iterrows():
        sym = str(r[1]["Symbol"]).strip()
        entry = scraped_progress.get(sym)

        if not entry or len(entry) <= 1:
            rows_to_process.append((r, None))
        elif args.update:
            rows_to_process.append((r, entry))
        else:
            last_scraped_str = entry.get("_last_scraped_timestamp")
            if not last_scraped_str:
                rows_to_process.append((r, entry))
            else:
                try:
                    dt = datetime.fromisoformat(last_scraped_str)
                    if dt < cutoff_time:
                        rows_to_process.append((r, entry))
                except Exception:
                    rows_to_process.append((r, entry))

    logging.info(f"Companies to scrape/check for new results: {len(rows_to_process)} / {len(df)}")

    if rows_to_process:
        completed_count = 0
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(scrape_company_task, r_tuple[0], args.delay, r_tuple[1]): r_tuple[0]
                for r_tuple in rows_to_process
            }

            for future in as_completed(futures):
                idx, symbol, metrics, success = future.result()
                if metrics:
                    # ── Merge new data with existing, preserving historical columns ──
                    if symbol in scraped_progress and scraped_progress[symbol]:
                        merged = smart_merge(scraped_progress[symbol], metrics)
                    else:
                        merged = metrics
                    scraped_progress[symbol] = merged
                completed_count += 1

                if completed_count % 5 == 0:
                    with open(args.progress, "w", encoding="utf-8") as f:
                        json.dump(scraped_progress, f, indent=2, ensure_ascii=False)
                    logging.info(f"Progress saved ({completed_count}/{len(rows_to_process)} done in current run).")

        with open(args.progress, "w", encoding="utf-8") as f:
            json.dump(scraped_progress, f, indent=2, ensure_ascii=False)

    logging.info("Scraping/update complete. Re-building Data_Enhanced.csv...")
    scraped_rows = []
    for _, row in df.iterrows():
        sym = str(row["Symbol"]).strip()
        entry = scraped_progress.get(sym, {}).copy()
        entry.pop("_last_scraped_timestamp", None)
        scraped_rows.append(entry)

    scraped_df = pd.DataFrame(scraped_rows)

    core_metadata_cols = ["Sr.", "Stock Name", "Symbol", "marketcapname", "market_cap", "industry"]
    base_cols = [c for c in df.columns if c in core_metadata_cols]
    base_df = df[base_cols]

    combined_df = pd.concat([base_df.reset_index(drop=True), scraped_df.reset_index(drop=True)], axis=1)
    combined_df.to_csv(args.output, index=False, encoding="utf-8-sig")
    logging.info(f"SUCCESS: Output saved to {args.output} (Total columns: {len(combined_df.columns)}, Total rows: {len(combined_df)})")

if __name__ == "__main__":
    main()
