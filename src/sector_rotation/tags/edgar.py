"""SEC EDGAR taxonomy: ticker -> CIK -> SIC code (industry).

EDGAR is free and authoritative for SIC. We do NOT pull 10-K Item 1 by default
because parsing that text is costly and the user wants to defer NLP. The
function is provided so the NLP layer can opt-in later.
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

TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

EDGAR_CACHE_DIR = CACHE_DIR / "edgar"
EDGAR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
TICKER_CIK_PARQUET = CACHE_DIR / "edgar_ticker_cik.parquet"
EDGAR_PROFILE_PARQUET = CACHE_DIR / "edgar_profile.parquet"

# SEC requests no more than 10 req/s and a descriptive UA.
_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
_REQUEST_DELAY = 0.12  # ~8 req/s

# A small SIC->theme map for the broad sectors EDGAR is most useful at.
# We deliberately keep these LABEL-prefixed with `sic:` so they don't collide
# with finer ETF themes.
SIC_HINTS = {
    "2836": "pharma-preparations",
    "3674": "semiconductors",
    "7372": "software-prepackaged",
    "7370": "computer-services",
    "1311": "oil-gas-extraction",
    "6022": "state-commercial-banks",
    "6798": "reits",
    "1040": "gold-mining",
    "1044": "silver-mining",
    "3711": "motor-vehicles",
    "3812": "search-detection-nav",
    "2834": "pharma-preparations",
    "3825": "lab-instruments",
    "3841": "medical-instruments",
    "3845": "electromedical",
    "3669": "communications-equipment",
    "3559": "special-industry-machinery",
    "1381": "drilling-oil-gas",
}


def fetch_ticker_cik_map(refresh: bool = False) -> pd.DataFrame:
    if TICKER_CIK_PARQUET.exists() and not refresh:
        return pd.read_parquet(TICKER_CIK_PARQUET)
    log.info("Fetching SEC ticker -> CIK map")
    resp = requests.get(TICKER_CIK_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    rows = [
        {"ticker": v["ticker"].upper(), "cik": int(v["cik_str"]), "name": v["title"]}
        for v in data.values()
    ]
    df = pd.DataFrame(rows)
    df.to_parquet(TICKER_CIK_PARQUET, index=False)
    return df


def _fetch_submissions(cik: int) -> dict | None:
    cache = EDGAR_CACHE_DIR / f"{cik:010d}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:
            cache.unlink(missing_ok=True)
    url = SUBMISSIONS_URL.format(cik=cik)
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    cache.write_text(json.dumps(data))
    time.sleep(_REQUEST_DELAY)
    return data


def fetch_edgar_profiles(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    """For each ticker, fetch SIC/sicDescription/category from EDGAR submissions."""
    if EDGAR_PROFILE_PARQUET.exists() and not refresh:
        cached = pd.read_parquet(EDGAR_PROFILE_PARQUET)
    else:
        cached = pd.DataFrame()

    cik_map = fetch_ticker_cik_map(refresh=refresh)
    cik_lookup = dict(zip(cik_map["ticker"], cik_map["cik"]))

    have = set(cached["ticker"]) if not cached.empty else set()
    todo = [t for t in tickers if t not in have and t in cik_lookup]

    rows: list[dict] = []
    if todo:
        log.info("Fetching EDGAR submissions for %d tickers", len(todo))
        for tk in todo:
            cik = cik_lookup[tk]
            try:
                data = _fetch_submissions(cik)
            except Exception as e:  # noqa: BLE001
                log.debug("EDGAR submissions failed for %s (CIK %s): %s", tk, cik, e)
                continue
            if not data:
                continue
            rows.append(
                {
                    "ticker": tk,
                    "cik": cik,
                    "sic": str(data.get("sic") or ""),
                    "sicDescription": data.get("sicDescription") or "",
                    "category": data.get("category") or "",
                }
            )

    new_df = pd.DataFrame(rows)
    out = pd.concat([cached, new_df], ignore_index=True) if not cached.empty else new_df
    if not out.empty:
        out = out.drop_duplicates(subset=["ticker"], keep="last")
        out.to_parquet(EDGAR_PROFILE_PARQUET, index=False)
    return out[out["ticker"].isin(tickers)] if not out.empty else out


def derive_edgar_tags(profiles: pd.DataFrame) -> pd.DataFrame:
    """Long DataFrame [ticker, tag, source='edgar'] from SIC profile data."""
    if profiles.empty:
        return pd.DataFrame(columns=["ticker", "tag", "source"])
    rows: list[tuple[str, str]] = []
    for _, row in profiles.iterrows():
        tk = row["ticker"]
        sic = row.get("sic") or ""
        desc = row.get("sicDescription") or ""
        if sic:
            rows.append((tk, f"sic:{sic}"))
        if desc:
            slug = desc.lower().replace("&", "and").replace(",", "").replace("/", "-")
            slug = "-".join(slug.split())
            rows.append((tk, f"sic-desc:{slug}"))
        hint = SIC_HINTS.get(sic)
        if hint:
            rows.append((tk, f"theme:{hint}"))
    df = pd.DataFrame(rows, columns=["ticker", "tag"])
    df["source"] = "edgar"
    return df.drop_duplicates(["ticker", "tag"]).reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    profs = fetch_edgar_profiles(["AAPL", "MSFT", "MU", "COHR"])
    print(profs)
    print(derive_edgar_tags(profs))
