# Sector-relative multibagger research scorer

This project analyses the 32 industry-specific stock universes in the parent `Industries` folder and creates a compact, sector-relative research shortlist. It does not predict or guarantee future returns, and it is not investment advice.

## What the program does

For each industry CSV, the program reads every available raw column and derives a disciplined set of investibility signals from the available financial histories. Each company is compared only with companies in the same sector file; it is never compared directly with banks, utilities, infrastructure, software, or other sectors with different economics.

The deterministic baseline assesses:

- Revenue and profit growth, durability, acceleration, and stability
- Operating-margin, ROCE, and ROE quality and improvement
- Earnings quality: cash from operations and free cash flow relative to reported profit
- Cash-flow persistence and volatility
- Balance-sheet trajectory: debt relative to equity, deleveraging, and interest coverage
- Operating discipline: working-capital, debtor-day, and inventory-day trends
- Capital allocation: dilution through equity issuance and return on capital
- Ownership signals: promoter ownership/change and institutional ownership change
- Valuation: P/E and a PEG-style price-versus-growth check
- Data sufficiency: newly listed or short-history companies are scored from available evidence, but their score is pulled toward neutral and the limitation appears in `Cons`

The algorithm ranks every signal against same-sector peers. This means leverage, cyclicality, margins, working-capital needs, and normal loss periods are judged in context. A high score means the company has stronger risk-adjusted fundamentals than its sector peers on the information present; it does **not** mean it will become a multibagger.

## Output

Run output is written to `scored_csv`. Source files are never changed. Each sector output contains only:

| Column | Meaning |
| --- | --- |
| `Stock Name` | Company name from the source universe |
| `Symbol` | Listed symbol |
| `market_cap` | Source market capitalization |
| `industry` | Source sub-industry label |
| `Hedge Fund Score` | 0–100 deterministic sector-relative score from the formula engine |
| `LLM Score` | Independent 0–100 Nemotron assessment from raw company data and peer context, without receiving the deterministic score |
| `Combined Score` | Final ranking score: deterministic score adjusted by at most ±15 using the independent LLM assessment |
| `Pros` | Strongest peer-relative fundamental signals |
| `Cons` | Main peer-relative weaknesses and short-history warning where applicable |

`scoring_manifest.csv` records the source/output mapping and mean score per sector.

`top_bottom_20.csv` contains a global ranking table with the 20 highest and 20 lowest Hedge Fund Scores across the full 1,425-stock universe. It includes the sector basket so that a result can be interpreted in peer context.

`global_ranking.csv` ranks all companies by score and includes a global rank and percentile. It is the preferred file for screening the full universe.

## Configuration

`scoring_config.json` defines the financial and asset-heavy sector groups plus the weights for growth, profitability, balance sheet, cash generation, valuation, and ownership. Financials have low industrial-debt weighting; asset-heavy sectors emphasize cash generation and apply only peer-relative leverage assessment. All weight profiles must sum to 1. `score_spread_multiplier` expands the distance from the neutral score of 50 after all peer and evidence adjustments; it improves portfolio ranking readability without changing the ordering within a sector. The default is 1.75 and valid values are 1.0–3.0.

## Run the full model-assisted analysis

```powershell
cd 'C:\Users\Yash\Desktop\Quant Club\Portfolio Management\Industries\hedge_fund_scoring'
python score_industries.py
```

LLM review is enabled by default. The program first writes all deterministic sector scores, rankings, and shortlist files. Only after those files are safely saved does it run the NVIDIA preflight and generate a review for every stock. This can make up to 1,425 billable API calls. If the key, endpoint, quota, or model fails, the deterministic files remain available.

To run only the reproducible local baseline without any external request:

```powershell
python score_industries.py --no-llm
```

If an existing output CSV is open in Excel, the program stops before writing any files and names the locked file. Close it, or write a separate run with `python score_industries.py --output-dir scored_csv_v2`.

## Optional NVIDIA model review

The model endpoint is NVIDIA `https://integrate.api.nvidia.com/v1/chat/completions` and the configured model is `nvidia/nemotron-3-super-120b-a12b`. Requests enable Nemotron thinking with a 16,384-token reasoning budget and 16,384 maximum output tokens. The model receives every non-empty raw field for the individual company as exact `column_name: value` pairs, plus an embedded data dictionary explaining Key Metrics, quarterly/annual statements, balance sheet, cash flow, ratios, ownership, dates, percentages, TTM, and missing values. It also receives sector-peer context, but **not** the deterministic score. It returns an independent `LLM Score` from 0–100 and tailored `Pros`/`Cons`. `Combined Score` can move up to ±15 from the deterministic score, preserving a bounded quantitative anchor while giving the model more influence. Rankings use `Combined Score`.

Numeric parsing preserves positive and negative signs, parentheses, percentages, commas, rupee symbols, and common Unicode minus characters. Negative profit is treated as a loss, negative cash flow as an outflow, and negative growth/change as contraction unless the named metric requires the opposite interpretation. Missing data is kept as unknown rather than converted to zero.

With LLM review enabled (the default), the program first sends a tiny preflight request. It prints either that the NVIDIA endpoint is connected and the model generated a response, or it stops before sending any stock data. During the run, it logs the number of company reviews being generated for each sector.

Do **not** hard-code an API key. The key exposed in chat should be rotated. To configure a replacement safely:

```powershell
Copy-Item .env.example .env
# Edit .env and set NVIDIA_API_KEY to your replacement key.
python score_industries.py --llm --max-llm-stocks 2
```

`--max-llm-stocks 2` is a low-cost smoke test (two stocks per sector). Remove it to review every stock: that creates up to 1,425 external API calls and may be slow or billable. If the provider context limit is reached, reduce the raw fields sent with `--max-model-fields 900`.

The `.env` file is ignored by Git and is never written into outputs.
