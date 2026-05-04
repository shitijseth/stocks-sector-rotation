"""Per-ticker fundamental snapshot via yfinance + quantitative bucket tags.

We pull `Ticker.info` (cached) for sector / industry / market cap / valuation
multiples / margins, then derive bucket tags such as `mcap-largecap`,
`pe-cheap`, `eps-growth-high` so they can join the unified tag table.
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf
from tqdm import tqdm

from .config import CACHE_DIR

log = logging.getLogger(__name__)

FUNDAMENTALS_PARQUET = CACHE_DIR / "fundamentals.parquet"
INFO_RAW_DIR = CACHE_DIR / "info_raw"
INFO_RAW_DIR.mkdir(parents=True, exist_ok=True)

INFO_FIELDS = [
    "shortName",
    "longName",
    "sector",
    "industry",
    "industryKey",
    "industryDisp",
    "country",
    "marketCap",
    "sharesOutstanding",
    "trailingPE",
    "forwardPE",
    "priceToBook",
    "priceToSalesTrailing12Months",
    "enterpriseToEbitda",
    "trailingEps",
    "forwardEps",
    "earningsGrowth",
    "earningsQuarterlyGrowth",
    "revenueGrowth",
    "profitMargins",
    "operatingMargins",
    "grossMargins",
    "returnOnEquity",
    "dividendYield",
    "beta",
    "longBusinessSummary",
]


def _fetch_one(ticker: str) -> dict | None:
    cache = INFO_RAW_DIR / f"{ticker}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            cache.unlink(missing_ok=True)
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance .info failed for %s: %s", ticker, e)
        return None
    if not info:
        return None
    record = {"ticker": ticker, **{k: info.get(k) for k in INFO_FIELDS}}
    cache.write_text(json.dumps(record, default=str))
    return record


def fetch_fundamentals(tickers: list[str], max_workers: int = 8, refresh: bool = False) -> pd.DataFrame:
    """Pull `.info` for each ticker. Disk-cached one JSON per ticker.

    Returns a DataFrame indexed by ticker.
    """
    if FUNDAMENTALS_PARQUET.exists() and not refresh:
        existing = pd.read_parquet(FUNDAMENTALS_PARQUET)
        missing = [t for t in tickers if t not in existing.index]
    else:
        existing = pd.DataFrame()
        missing = list(tickers)

    rows: list[dict] = []
    if missing:
        log.info("Fetching .info for %d tickers", len(missing))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(_fetch_one, t): t for t in missing}
            for fut in tqdm(as_completed(futs), total=len(futs), desc="fundamentals"):
                rec = fut.result()
                if rec:
                    rows.append(rec)
                # be polite: small jitter
                time.sleep(0.01)

    new_df = pd.DataFrame(rows).set_index("ticker") if rows else pd.DataFrame()
    out = pd.concat([existing, new_df]) if not existing.empty else new_df
    if not out.empty:
        out = out[~out.index.duplicated(keep="last")]
        out.to_parquet(FUNDAMENTALS_PARQUET)
    return out.loc[[t for t in tickers if t in out.index]] if not out.empty else out


# ---------- bucket tags ----------

MCAP_BUCKETS = [
    (0, 50e6, "mcap-nano"),
    (50e6, 300e6, "mcap-micro"),
    (300e6, 2e9, "mcap-small"),
    (2e9, 10e9, "mcap-mid"),
    (10e9, 200e9, "mcap-large"),
    (200e9, float("inf"), "mcap-mega"),
]


def _bucket(value, edges_labels):
    if value is None or pd.isna(value):
        return None
    for lo, hi, label in edges_labels:
        if lo <= value < hi:
            return label
    return None


def derive_quant_tags(fund: pd.DataFrame) -> pd.DataFrame:
    """Return a long DataFrame [ticker, tag, source='quant'] of bucket tags."""
    if fund.empty:
        return pd.DataFrame(columns=["ticker", "tag", "source"])

    rows: list[tuple[str, str]] = []
    for tk, row in fund.iterrows():
        if (b := _bucket(row.get("marketCap"), MCAP_BUCKETS)):
            rows.append((tk, b))

        pe = row.get("trailingPE")
        if pe is not None and not pd.isna(pe):
            if pe < 0:
                rows.append((tk, "pe-negative"))
            elif pe < 12:
                rows.append((tk, "pe-cheap"))
            elif pe < 25:
                rows.append((tk, "pe-fair"))
            elif pe < 50:
                rows.append((tk, "pe-rich"))
            else:
                rows.append((tk, "pe-very-rich"))

        eg = row.get("earningsGrowth")
        if eg is not None and not pd.isna(eg):
            if eg > 0.30:
                rows.append((tk, "eps-growth-high"))
            elif eg > 0.10:
                rows.append((tk, "eps-growth-mid"))
            elif eg > 0:
                rows.append((tk, "eps-growth-low"))
            else:
                rows.append((tk, "eps-growth-negative"))

        pm = row.get("profitMargins")
        if pm is not None and not pd.isna(pm):
            if pm > 0.20:
                rows.append((tk, "margin-high"))
            elif pm > 0.05:
                rows.append((tk, "margin-mid"))
            elif pm > 0:
                rows.append((tk, "margin-low"))
            else:
                rows.append((tk, "margin-negative"))

        dy = row.get("dividendYield")
        if dy is not None and not pd.isna(dy) and dy > 0:
            # yfinance returns dividendYield as a percentage (e.g. 2.5 = 2.5%) in
            # recent versions. Normalize: anything > 1 we treat as percent.
            dy_frac = dy / 100.0 if dy > 1.0 else dy
            if dy_frac > 0.05:
                rows.append((tk, "dividend-high"))
            elif dy_frac > 0.02:
                rows.append((tk, "dividend-mid"))
            else:
                rows.append((tk, "dividend-low"))

        beta = row.get("beta")
        if beta is not None and not pd.isna(beta):
            if beta > 1.5:
                rows.append((tk, "beta-high"))
            elif beta > 0.8:
                rows.append((tk, "beta-mid"))
            elif beta >= 0:
                rows.append((tk, "beta-low"))

        sec = row.get("sector")
        if isinstance(sec, str) and sec:
            rows.append((tk, f"gics-sector:{sec.lower().replace(' ', '-')}"))
        ind = row.get("industry")
        if isinstance(ind, str) and ind:
            rows.append((tk, f"gics-industry:{ind.lower().replace(' ', '-')}"))

    df = pd.DataFrame(rows, columns=["ticker", "tag"])
    df["source"] = "quant"
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fund = fetch_fundamentals(["AAPL", "MSFT", "NVDA", "MU"])
    print(fund[["sector", "industry", "marketCap"]])
    print(derive_quant_tags(fund))
