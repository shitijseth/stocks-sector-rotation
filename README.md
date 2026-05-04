# stocks-sector-rotation

Multi-layer thematic tagging and sector-rotation analysis for US-listed stocks.

The goal: categorize every US-tradable stock along *many* dimensions (market cap,
valuation multiples, earnings growth, GICS sector, fine-grained themes like
"photonics" / "DRAM memory" / "lithium-battery"), then identify which **groups
of stocks have been moving together** over the last five years.

## Tag layers

Each ticker accumulates tags from several independent sources, joined into one
long table `[ticker, tag, source]`:

| Layer | Source | Granularity | Confidence |
|---|---|---|---|
| `etf` | Issuer-published ETF holdings via [`etf-scraper`](https://pypi.org/project/etf-scraper/) (iShares, SSGA, Invesco, Vanguard, Global X, ARK, …) — ~80 curated thematic ETFs | Theme (semiconductors, cybersecurity, lithium-battery, …) | **High** — issuers are legally bound to publish accurate holdings |
| `quant` | yfinance fundamentals (`marketCap`, `trailingPE`, `earningsGrowth`, `profitMargins`, `dividendYield`, `beta`, GICS sector/industry) | Bucket tags (`mcap-mega`, `pe-cheap`, `eps-growth-high`) + GICS | High for buckets; medium for yfinance GICS coverage |
| `edgar` | SEC EDGAR submissions API → SIC code + description | Industry (~440 codes) | High |
| `wikidata` | Wikidata SPARQL on `P249` (ticker) → `P1056` (product) + `P452` (industry) | Very fine when populated (e.g. `product:dram`, `product:gpu`) | Medium — community-edited, ~70% S&P 500 coverage |
| `finnhub` *(optional)* | `/stock/profile2` → `finnhubIndustry` | ~150 buckets | High; needs free API key (60 req/min) |
| `fmp` *(optional)* | `/profile/{symbol}` → `industry` + `description` | ~140 buckets | High; needs free API key (250/day) |

Skipped paid sources: FactSet RBICS, Refinitiv TRBC (no free tier).

## Analysis layers

1. **Group performance**: equal-weight tag-level daily returns, rolling
   compounded returns at 21 / 63 / 126 / 252 / 756 / 1260 trading days, and a
   month-by-month leadership ranking.
2. **Discovered clusters** (price-only): rolling-window correlation distance →
   hierarchical clustering → flat clusters. Each cluster is then back-labelled
   with the most over-represented tags from the tag table — clusters that don't
   match a known theme are emergent groups worth investigating.

## Install

```bash
pip install -e .
```

Optional API keys (drop into a `.env` at the repo root):

```
FINNHUB_API_KEY=...
FMP_API_KEY=...
SEC_USER_AGENT=your-name your@email
```

## Usage

```bash
# 1. Build the universe (NASDAQ + NYSE + AMEX symbol files)
sector-rotation universe

# 2. Pull 5y prices + fundamentals (parquet-cached, incremental)
sector-rotation ingest --limit 500          # use --limit for a smoke test

# 3. Build the unified tag table (ETF + quant + EDGAR + Wikidata)
sector-rotation tag --limit 500

# 4. Run analyses + write reports/ CSVs and PNGs
sector-rotation analyze --horizon-days 63

# Or run the whole pipeline:
sector-rotation all
```

Outputs land in `reports/`:

- `momentum.csv` / `momentum_3m.png` / `momentum_1y.png` — tag returns at multiple horizons
- `leadership.csv` / `leadership_heatmap.png` — month-by-month tag leaders over the lookback
- `clusters_1y.csv` / `clusters_3m.csv` — discovered co-moving clusters with their over-represented tag labels

## Interactive dashboard

A Streamlit dashboard makes it easy to explore the results.

```bash
pip install -e '.[dashboard]'
sector-rotation dashboard       # opens http://localhost:8501
```

### Password protection

The dashboard is gated by a shared password. Configure it one of three ways:

1. **`.env`** (recommended — already loaded by the project):
   ```
   DASHBOARD_PASSWORD=your-secret
   ```
2. **`.streamlit/secrets.toml`** (Streamlit's own convention):
   ```toml
   dashboard_password = "your-secret"
   ```
3. **Disable the gate entirely** (only safe for localhost-only use):
   ```
   DASHBOARD_NO_AUTH=1
   ```

If no password is configured and `DASHBOARD_NO_AUTH` is unset, the dashboard
refuses to render and shows setup instructions instead — so you can't
accidentally tunnel an unauthenticated app to the public internet.

### Pages

The dashboard has five pages, all driven by the cached parquet + report CSVs:

- **Overview** — universe stats; top-25 tags by 3m and 1y return.
- **Themes & momentum** — searchable, sortable table over all ~800 tags. Pick a
  tag to see the equal-weight cumulative return vs SPY plus member list.
- **Leadership over time** — heatmap of the most frequent monthly leaders over
  the lookback; pick a month to see its top 15.
- **Co-moving clusters** — switch between the 1-year and 3-month windows; filter
  by min size / min intra-cluster correlation; expand any cluster to see members,
  their over-represented tag labels, and a cumulative-return chart.
- **Ticker drill-down** — pick a ticker to see fundamentals snapshot, 5y price,
  every tag it carries, and every cluster it belongs to.

## Layout

```
src/sector_rotation/
├── config.py                 # paths, ETF curation, knobs
├── universe.py               # NASDAQ Trader symbol files
├── prices.py                 # yfinance 5y daily, parquet cache
├── fundamentals.py           # yfinance .info + quant bucket tags
├── tags/
│   ├── etfs.py               # etf-scraper ETF holdings -> theme tags
│   ├── edgar.py              # SEC SIC profiles
│   ├── wikidata.py           # SPARQL P1056 / P452
│   ├── finnhub.py            # optional, free tier
│   ├── fmp.py                # optional, free tier
│   └── combine.py            # merge tag layers
├── analysis/
│   ├── group_perf.py         # rolling tag returns, leadership ranking
│   └── clustering.py         # correlation-distance hierarchical clustering
├── report.py                 # CSVs + matplotlib heatmap/bars
└── cli.py                    # `sector-rotation` Click CLI
```

## Notes

- Data is cached under `data/cache/` (gitignored). Re-running pulls only deltas.
- yfinance is rate-limited and occasionally flaky — the price loader batches
  requests and tolerates partial failures.
- "Robinhood-tradable" is approximated by the NASDAQ + NYSE + AMEX listed common
  stocks. Robinhood doesn't publish an official tradable list; this superset is
  the standard substitute.
