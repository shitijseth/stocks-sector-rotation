"""Finnhub `/stock/profile2` — optional tag source. Skips silently without API key.

Free tier: 60 calls/min, no card required. Provides ~150-bucket `finnhubIndustry`.
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from ..config import CACHE_DIR, FINNHUB_API_KEY

log = logging.getLogger(__name__)

ENDPOINT = "https://finnhub.io/api/v1/stock/profile2"
CACHE = CACHE_DIR / "finnhub_profile.parquet"


def fetch_finnhub_profiles(tickers: list[str], refresh: bool = False) -> pd.DataFrame:
    if not FINNHUB_API_KEY:
        log.info("FINNHUB_API_KEY not set — skipping Finnhub layer")
        return pd.DataFrame(columns=["ticker", "finnhubIndustry", "name", "country", "ipo"])

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
                ENDPOINT,
                params={"symbol": tk, "token": FINNHUB_API_KEY},
                timeout=15,
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                continue
            data = resp.json() or {}
            if not data:
                continue
            rows.append(
                {
                    "ticker": tk,
                    "finnhubIndustry": data.get("finnhubIndustry") or "",
                    "name": data.get("name") or "",
                    "country": data.get("country") or "",
                    "ipo": data.get("ipo") or "",
                }
            )
        except Exception as e:  # noqa: BLE001
            log.debug("Finnhub failed for %s: %s", tk, e)
        # 60/min => ~1 req/s
        time.sleep(1.05)

    new_df = pd.DataFrame(rows)
    out = pd.concat([cached, new_df], ignore_index=True) if not cached.empty else new_df
    if not out.empty:
        out = out.drop_duplicates(subset=["ticker"], keep="last")
        out.to_parquet(CACHE, index=False)
    return out[out["ticker"].isin(tickers)] if not out.empty else out


def derive_finnhub_tags(profiles: pd.DataFrame) -> pd.DataFrame:
    if profiles.empty:
        return pd.DataFrame(columns=["ticker", "tag", "source"])
    rows: list[tuple[str, str]] = []
    for _, r in profiles.iterrows():
        ind = r.get("finnhubIndustry")
        if isinstance(ind, str) and ind:
            slug = "-".join(ind.lower().replace("&", "and").split())
            rows.append((r["ticker"], f"finnhub-industry:{slug}"))
    df = pd.DataFrame(rows, columns=["ticker", "tag"])
    df["source"] = "finnhub"
    return df.drop_duplicates(["ticker", "tag"]).reset_index(drop=True)
