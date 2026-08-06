"""
Hedge Fund & Fallback LLM Sector Stock Scoring Runner
======================================================
Usage:
    python run_hedge_fund_scoring.py            # Run deterministic hedge fund scoring across all 32 industry CSVs
    python run_hedge_fund_scoring.py --no-llm   # Run local deterministic model only (no API key required)
    python run_hedge_fund_scoring.py --llm      # Run deterministic + Groq/OpenAI LLM review engine
"""
import os
import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SCORER_SCRIPT = BASE_DIR / "score_industries.py"

if not SCORER_SCRIPT.exists():
    print(f"Error: {SCORER_SCRIPT} not found!")
    sys.exit(1)

cmd = [sys.executable, str(SCORER_SCRIPT)] + sys.argv[1:]
print(f"Launching Hedge Fund Stock Scoring Engine: {' '.join(cmd)}")
sys.exit(subprocess.call(cmd))
