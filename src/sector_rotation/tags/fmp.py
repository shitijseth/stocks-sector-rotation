"""Financial Modeling Prep `/profile/{symbol}` — optional. Skips without API key.

Free tier: 250 calls/day. Provides industry + sector + business description.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from ..config import CACHE_DIR, FMP_API_KEY

log = logging.getLogger(__name__)

ENDPOINT = "https://financialmodelingprep.com/api/v3/profile/{symbol}"
CACHE = CACHE_DIR / "fmp_profile.parquet"


def fetch_fmp_profiles(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    if not FMP_API_KEY:
        log.info("FMP_API_KEY not set — skipping FMP layer")
        return pd.DataFrame(columns=["ticker", "industry", "sector", "description"])

    if CACHE.exists() and not refresh:
        cached = pd.read_parquet(CACHE)
    else:
        cached = pd.DataFrame()

    have = set(cached["ticker"]) if not cached.empty else set()
    todo = [t for t in tickers if t not in have]

    rows: list[dict] = []
    for tk in todo:
        try:
            resp = requests.get(
                ENDPOINT.format(symbol=tk),
                params={"apikey": FMP_API_KEY},
                timeout=15,
            )
            if resp.status_code == 429:
                log.warning("FMP rate-limited; aborting batch")
                break
            if resp.status_code != 200:
                continue
            data = resp.json() or []
            if not data:
                continue
            d = data[0]
            rows.append(
                {
                    "ticker": tk,
                    "industry": d.get("industry") or "",
                    "sector": d.get("sector") or "",
                    "description": d.get("description") or "",
                }
            )
        except Exception as e:  # noqa: BLE001
            log.debug("FMP failed for %s: %s", tk, e)
        time.sleep(0.3)  # 250/day cap; be polite within the day too

    new_df = pd.DataFrame(rows)
    out = pd.concat([cached, new_df], ignore_index=True) if not cached.empty else new_df
    if not out.empty:
        out = out.drop_duplicates(subset=["ticker"], keep="last")
        out.to_parquet(CACHE, index=False)
    return out[out["ticker"].isin(tickers)] if not out.empty else out


def derive_fmp_tags(profiles: pd.DataFrame) -> pd.DataFrame:
    if profiles.empty:
        return pd.DataFrame(columns=["ticker", "tag", "source"])
    rows: list[tuple[str, str]] = []
    for _, r in profiles.iterrows():
        ind = r.get("industry")
        if isinstance(ind, str) and ind:
            slug = "-".join(ind.lower().replace("&", "and").split())
            rows.append((r["ticker"], f"fmp-industry:{slug}"))
    df = pd.DataFrame(rows, columns=["ticker", "tag"])
    df["source"] = "fmp"
    return df.drop_duplicates(["ticker", "tag"]).reset_index(drop=True)
