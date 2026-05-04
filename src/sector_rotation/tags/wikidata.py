"""Wikidata SPARQL: ticker -> products/materials produced (P1056) + industry (P452).

Coverage is partial (~70% S&P 500, ~30% Russell 3000) but where present the
labels are very granular (e.g. "DRAM", "GPU", "lithium-ion battery"). Treat as
candidate tags rather than ground truth.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd
import requests

from ..config import CACHE_DIR, SEC_USER_AGENT

log = logging.getLogger(__name__)

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_CACHE = CACHE_DIR / "wikidata.parquet"
WIKIDATA_RAW = CACHE_DIR / "wikidata_raw"
WIKIDATA_RAW.mkdir(parents=True, exist_ok=True)

_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept": "application/sparql-results+json",
}

# We query in small batches (VALUES clause) to be polite.
_BATCH_SIZE = 50
_REQUEST_DELAY = 1.0  # seconds between SPARQL calls

_QUERY_TEMPLATE = """
SELECT ?ticker ?company ?companyLabel ?productLabel ?industryLabel WHERE {{
  VALUES ?ticker {{ {values} }}
  ?company wdt:P249 ?ticker.
  OPTIONAL {{ ?company wdt:P1056 ?product. }}
  OPTIONAL {{ ?company wdt:P452 ?industry. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
"""


def _query(tickers: list[str]) -> list[dict]:
    values = " ".join(f'"{t}"' for t in tickers)
    query = _QUERY_TEMPLATE.format(values=values)
    resp = requests.get(
        WIKIDATA_SPARQL,
        params={"query": query, "format": "json"},
        headers=_HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json().get("results", {}).get("bindings", [])


def _slug(s: str) -> str:
    return "-".join(s.lower().replace("&", "and").replace(",", "").split())


def fetch_wikidata(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    """Long DataFrame [ticker, product, industry] from Wikidata."""
    if WIKIDATA_CACHE.exists() and not refresh:
        cached = pd.read_parquet(WIKIDATA_CACHE)
    else:
        cached = pd.DataFrame(columns=["ticker", "product", "industry"])

    have = set(cached["ticker"].unique()) if not cached.empty else set()
    todo = [t for t in tickers if t not in have]

    new_rows: list[dict] = []
    if todo:
        log.info("Querying Wikidata for %d tickers", len(todo))
        for i in range(0, len(todo), _BATCH_SIZE):
            batch = todo[i : i + _BATCH_SIZE]
            try:
                bindings = _query(batch)
            except Exception as e:  # noqa: BLE001
                log.debug("Wikidata batch %s failed: %s", i, e)
                time.sleep(_REQUEST_DELAY * 4)
                continue
            for b in bindings:
                new_rows.append(
                    {
                        "ticker": b["ticker"]["value"],
                        "product": b.get("productLabel", {}).get("value", "") or "",
                        "industry": b.get("industryLabel", {}).get("value", "") or "",
                    }
                )
            # Mark every queried ticker as visited (even if no rows came back).
            for t in batch:
                if not any(r["ticker"] == t for r in new_rows[-len(bindings):]):
                    new_rows.append({"ticker": t, "product": "", "industry": ""})
            time.sleep(_REQUEST_DELAY)

    new_df = pd.DataFrame(new_rows)
    out = pd.concat([cached, new_df], ignore_index=True) if not cached.empty else new_df
    if not out.empty:
        out = out.drop_duplicates(subset=["ticker", "product", "industry"]).reset_index(drop=True)
        out.to_parquet(WIKIDATA_CACHE, index=False)
    if out.empty:
        return out
    return out[out["ticker"].isin(tickers)]


def derive_wikidata_tags(wd: pd.DataFrame) -> pd.DataFrame:
    """Long DataFrame [ticker, tag, source='wikidata']."""
    if wd.empty:
        return pd.DataFrame(columns=["ticker", "tag", "source"])
    rows: list[tuple[str, str]] = []
    for _, r in wd.iterrows():
        if r.get("product"):
            rows.append((r["ticker"], f"product:{_slug(r['product'])}"))
        if r.get("industry"):
            rows.append((r["ticker"], f"wd-industry:{_slug(r['industry'])}"))
    df = pd.DataFrame(rows, columns=["ticker", "tag"])
    df["source"] = "wikidata"
    return df.drop_duplicates(["ticker", "tag"]).reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    wd = fetch_wikidata(["NVDA", "AAPL", "MU", "COHR"], refresh=True)
    print(wd)
    print(derive_wikidata_tags(wd))
