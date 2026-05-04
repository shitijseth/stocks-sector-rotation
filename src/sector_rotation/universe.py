"""Build the tradable US equity universe from NASDAQ Trader symbol files.

The NASDAQ Trader directory publishes daily pipe-delimited files for both NASDAQ
and the other primary listing venues (NYSE, NYSE American, etc.) — together they
cover essentially every common stock and ETF tradable in the US, which is a
near-superset of Robinhood's tradable list.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

import pandas as pd
import requests

from .config import CACHE_DIR

log = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

UNIVERSE_PARQUET = CACHE_DIR / "universe.parquet"


@dataclass
class UniverseFilters:
    include_etfs: bool = False
    exclude_test_issues: bool = True
    exclude_warrants_units: bool = True


def _fetch_pipe_file(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    text = resp.text
    # Files end with a "File Creation Time" footer line — strip it.
    lines = [ln for ln in text.splitlines() if "|" in ln and not ln.startswith("File Creation")]
    return pd.read_csv(io.StringIO("\n".join(lines)), sep="|")


def _normalize(nasdaq: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    n = nasdaq.rename(columns={"Symbol": "ticker", "Security Name": "name"}).copy()
    n["exchange"] = "NASDAQ"
    n["etf"] = n.get("ETF", "N").map({"Y": True, "N": False}).fillna(False)
    n["test_issue"] = n.get("Test Issue", "N").map({"Y": True, "N": False}).fillna(False)

    o = other.rename(columns={"ACT Symbol": "ticker", "Security Name": "name"}).copy()
    # `Exchange` is a single-letter code: A=NYSE MKT, N=NYSE, P=NYSE Arca, Z=Cboe, V=IEX
    exch_map = {"A": "NYSE_AMERICAN", "N": "NYSE", "P": "NYSE_ARCA", "Z": "CBOE", "V": "IEX"}
    o["exchange"] = o.get("Exchange", "").map(exch_map).fillna("OTHER")
    o["etf"] = o.get("ETF", "N").map({"Y": True, "N": False}).fillna(False)
    o["test_issue"] = o.get("Test Issue", "N").map({"Y": True, "N": False}).fillna(False)

    keep = ["ticker", "name", "exchange", "etf", "test_issue"]
    return pd.concat([n[keep], o[keep]], ignore_index=True)


def fetch_universe(filters: UniverseFilters | None = None, refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame of tradable tickers. Cached locally as parquet."""
    filters = filters or UniverseFilters()
    if UNIVERSE_PARQUET.exists() and not refresh:
        df = pd.read_parquet(UNIVERSE_PARQUET)
    else:
        log.info("Downloading NASDAQ Trader symbol files")
        nasdaq = _fetch_pipe_file(NASDAQ_LISTED_URL)
        other = _fetch_pipe_file(OTHER_LISTED_URL)
        df = _normalize(nasdaq, other)
        df.to_parquet(UNIVERSE_PARQUET, index=False)

    if filters.exclude_test_issues:
        df = df[~df["test_issue"]]
    if not filters.include_etfs:
        df = df[~df["etf"]]
    if filters.exclude_warrants_units:
        # Heuristic: tickers ending in W (warrant), R (rights), U (unit), or containing dots.
        bad = df["ticker"].str.match(r".*[\.\$].*|.*[WRU]$") & df["name"].str.contains(
            r"warrant|right|unit", case=False, regex=True, na=False
        )
        df = df[~bad]
    df = df.dropna(subset=["ticker"]).drop_duplicates(subset=["ticker"]).reset_index(drop=True)
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    u = fetch_universe(refresh=True)
    print(u.head())
    print(f"Total tickers: {len(u)}")
    print(u["exchange"].value_counts())
