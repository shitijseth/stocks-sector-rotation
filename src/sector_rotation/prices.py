"""Daily price history via yfinance with parquet caching.

We store one wide parquet per field (close / adjclose / volume) indexed by date,
columns = ticker. Append-only updates: on subsequent runs we only fetch from the
last cached date forward.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
from tqdm import tqdm

from .config import CACHE_DIR, LOOKBACK_YEARS, PRICE_BATCH_SIZE

log = logging.getLogger(__name__)

ADJCLOSE_PARQUET = CACHE_DIR / "adjclose.parquet"
VOLUME_PARQUET = CACHE_DIR / "volume.parquet"


def _load(path) -> pd.DataFrame | None:
    if path.exists():
        return pd.read_parquet(path)
    return None


def _save(df: pd.DataFrame, path) -> None:
    df.sort_index(inplace=True)
    df.to_parquet(path)


def _batched(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def fetch_prices(
    tickers: list[str],
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    refresh: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (adj_close, volume) wide DataFrames for the requested tickers.

    Caches incrementally — only missing tickers / dates are fetched.
    """
    if start is None:
        start = (datetime.utcnow() - timedelta(days=365 * LOOKBACK_YEARS + 30)).strftime("%Y-%m-%d")
    if end is None:
        end = datetime.utcnow().strftime("%Y-%m-%d")

    cached_close = _load(ADJCLOSE_PARQUET) if not refresh else None
    cached_vol = _load(VOLUME_PARQUET) if not refresh else None

    have = set(cached_close.columns) if cached_close is not None else set()
    last_cached = (
        cached_close.index.max() if cached_close is not None and not cached_close.empty else None
    )

    # Decide what to fetch.
    missing = [t for t in tickers if t not in have]
    needs_extension = (
        last_cached is not None
        and pd.Timestamp(end).normalize() > last_cached
        and len(have & set(tickers)) > 0
    )

    new_close_frames: list[pd.DataFrame] = []
    new_vol_frames: list[pd.DataFrame] = []

    # 1. Fully fetch missing tickers across full window.
    if missing:
        log.info("Fetching %d new tickers from yfinance", len(missing))
        for batch in tqdm(list(_batched(missing, PRICE_BATCH_SIZE)), desc="prices(new)"):
            df = yf.download(
                batch,
                start=start,
                end=end,
                auto_adjust=False,
                progress=False,
                threads=True,
                group_by="column",
            )
            if df is None or df.empty:
                continue
            close = _extract_field(df, batch, "Adj Close")
            vol = _extract_field(df, batch, "Volume")
            new_close_frames.append(close)
            new_vol_frames.append(vol)

    # 2. Extend existing tickers from last_cached -> end.
    if needs_extension:
        ext_start = (last_cached + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        existing = sorted(have & set(tickers))
        log.info("Extending %d cached tickers from %s", len(existing), ext_start)
        for batch in tqdm(list(_batched(existing, PRICE_BATCH_SIZE)), desc="prices(ext)"):
            df = yf.download(
                batch,
                start=ext_start,
                end=end,
                auto_adjust=False,
                progress=False,
                threads=True,
                group_by="column",
            )
            if df is None or df.empty:
                continue
            new_close_frames.append(_extract_field(df, batch, "Adj Close"))
            new_vol_frames.append(_extract_field(df, batch, "Volume"))

    close_df = _merge(cached_close, new_close_frames)
    vol_df = _merge(cached_vol, new_vol_frames)

    if close_df is not None:
        _save(close_df, ADJCLOSE_PARQUET)
    if vol_df is not None:
        _save(vol_df, VOLUME_PARQUET)

    # Subset to requested tickers (in the universe) for return.
    cols = [t for t in tickers if close_df is not None and t in close_df.columns]
    return (
        close_df[cols] if close_df is not None else pd.DataFrame(),
        vol_df[cols] if vol_df is not None else pd.DataFrame(),
    )


def _extract_field(df: pd.DataFrame, batch: list[str], field: str) -> pd.DataFrame:
    """yf.download returns differently shaped frames depending on len(batch)."""
    if isinstance(df.columns, pd.MultiIndex):
        if field in df.columns.get_level_values(0):
            sub = df[field]
        elif field in df.columns.get_level_values(-1):
            sub = df.xs(field, axis=1, level=-1)
        else:
            return pd.DataFrame()
        sub = sub.copy()
    else:
        if field not in df.columns:
            return pd.DataFrame()
        sub = df[[field]].rename(columns={field: batch[0]})
    sub.index = pd.to_datetime(sub.index).tz_localize(None)
    return sub


def _merge(cached: pd.DataFrame | None, new_frames: list[pd.DataFrame]) -> pd.DataFrame | None:
    if not new_frames and cached is None:
        return None
    parts = []
    if cached is not None:
        parts.append(cached)
    parts.extend([f for f in new_frames if f is not None and not f.empty])
    if not parts:
        return cached
    out = pd.concat(parts, axis=1)
    # Collapse duplicate columns (later frames overwrite earlier on overlap).
    out = out.loc[:, ~out.columns.duplicated(keep="last")]
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out


def liquid_universe(
    tickers: list[str], min_dollar_vol: float, lookback_days: int = 60
) -> list[str]:
    """Filter tickers by trailing average dollar volume."""
    close_df, vol_df = fetch_prices(tickers)
    if close_df.empty or vol_df.empty:
        return []
    recent = close_df.tail(lookback_days) * vol_df.tail(lookback_days)
    adv = recent.mean(axis=0)
    return adv[adv >= min_dollar_vol].index.tolist()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    close, vol = fetch_prices(["AAPL", "MSFT", "NVDA"])
    print(close.tail())
