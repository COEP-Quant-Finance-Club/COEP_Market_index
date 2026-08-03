"""
COEP Market Index - Base Market Cap Seeder Tool
================================================
Usage:
  python mcap_seed_scraper.py

Purpose:
  Runs ONCE to extract and seed base market caps (in Cr) and sector mappings 
  for all stocks into base_market_caps.json.
  Future daily market caps are calculated dynamically by formulas (Price * Shares)
  to ensure 100% zero forward bias.
"""

import os
import sys
import json
import logging
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_CSV = os.path.join(BASE_DIR, "Data.csv")
OUTPUT_JSON = os.path.join(BASE_DIR, "base_market_caps.json")

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
log = logging.getLogger("MCapSeeder")


def clean_sector_name(sec: str) -> str:
    if not isinstance(sec, str) or not sec.strip():
        return "MISCELLANEOUS"
    clean = sec.strip().upper().replace("/", "_").replace("-", "_").replace("&", "AND")
    clean = "_".join(clean.split())
    return clean


def seed_base_market_caps():
    log.info(f"Reading stock metadata & market caps from {os.path.basename(DATA_CSV)}...")
    if not os.path.exists(DATA_CSV):
        log.error(f"Data file not found: {DATA_CSV}")
        sys.exit(1)

    df = pd.read_csv(DATA_CSV, low_memory=False)
    
    sym_col = next((c for c in df.columns if c.lower() == "symbol"), "Symbol")
    sec_col = next((c for c in df.columns if c.lower() == "industry"), "industry")
    mcap_col = next((c for c in df.columns if c.lower() in ["market_cap", "market cap"]), "market_cap")

    if sym_col not in df.columns or mcap_col not in df.columns:
        log.error("Required columns ('Symbol', 'market_cap') not present in Data.csv")
        sys.exit(1)

    base_caps = {}
    valid_count = 0

    for idx, row in df.iterrows():
        sym = str(row[sym_col]).strip().upper()
        if not sym or sym == "NAN":
            continue

        raw_sec = str(row[sec_col]) if sec_col in df.columns else "MISCELLANEOUS"
        clean_sec = clean_sector_name(raw_sec)

        try:
            mcap_cr = float(row[mcap_col])
        except (ValueError, TypeError):
            mcap_cr = 0.0

        if mcap_cr <= 0:
            mcap_cr = 100.0  # Safe minimum fallback for unlisted / micro cap

        base_caps[sym] = {
            "symbol": sym,
            "sector": clean_sec,
            "base_market_cap_cr": round(mcap_cr, 2)
        }
        valid_count += 1

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(base_caps, f, indent=2)

    log.info(f"[SUCCESS] Seeded base market caps for {valid_count} stocks -> {os.path.basename(OUTPUT_JSON)}")


if __name__ == "__main__":
    seed_base_market_caps()
