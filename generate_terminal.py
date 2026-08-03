"""
Quant Club - Standalone Sector Terminal Generator
================================─────────────────
Run this single command anytime you add or update stock CSV data:

    python generate_terminal.py

It will automatically:
1. Rebuild all 44 Sector Indices (Daily & 4-Hour timeframes).
2. Generate dashboard_data.js.
3. Compile the 100% self-contained single HTML file: quant_club_sector_terminal.html.
"""

import os
import sys
import json
import glob
import pandas as pd

BASE_DIR    = r"C:\Users\Yash\Desktop\Quant Club\Portfolio Management"
SCRATCH_DIR = r"C:\Users\Yash\.gemini\antigravity\brain\40e456d3-c44c-4cbd-99b5-f9285df57de5\scratch"

def main():
    print("="*70)
    print("QUANT CLUB - STANDALONE SECTOR TERMINAL GENERATOR")
    print("="*70)

    # 0. Clean and Organize Sector Baskets
    organizer_script = os.path.join(BASE_DIR, "sector_organizer.py")
    print("\n[Step 1/4] Organizing sector baskets & cleaning old duplicates...")
    res0 = os.system(f'python "{organizer_script}"')
    if res0 != 0:
        print("[ERROR] Failed to organize sector baskets!")
        sys.exit(1)

    # 1. Rebuild Sector Indices
    build_indices_script = os.path.join(BASE_DIR, "build_sector_indices.py")
    print("\n[Step 2/4] Rebuilding Sector Indices (Daily & 4-Hour)...")
    res1 = os.system(f'python "{build_indices_script}"')
    if res1 != 0:
        print("[ERROR] Failed to build sector indices!")
        sys.exit(1)

    # 2. Re-generate Dashboard Data JS
    gen_data_script = os.path.join(SCRATCH_DIR, "generate_dash_data.py")
    print("\n[Step 3/4] Generating pre-bundled dashboard_data.js...")
    res2 = os.system(f'python "{gen_data_script}"')
    if res2 != 0:
        print("[ERROR] Failed to generate dashboard_data.js!")
        sys.exit(1)

    # 3. Re-compile Standalone HTML Terminal
    standalone_script = os.path.join(SCRATCH_DIR, "build_standalone_html.py")
    print("\n[Step 4/4] Compiling quant_club_sector_terminal.html...")
    res3 = os.system(f'python "{standalone_script}"')
    if res3 != 0:
        print("[ERROR] Failed to compile quant_club_sector_terminal.html!")
        sys.exit(1)

    out_file = os.path.join(BASE_DIR, "quant_club_sector_terminal.html")
    size_mb = os.path.getsize(out_file) / (1024 * 1024)

    print("\n" + "="*70)
    print("SUCCESS: Standalone Sector Terminal Generated!")
    print(f"File Path: {out_file}")
    print(f"File Size: {size_mb:.2f} MB")
    print("Double-click quant_club_sector_terminal.html to view anywhere offline!")
    print("="*70)

if __name__ == "__main__":
    main()
