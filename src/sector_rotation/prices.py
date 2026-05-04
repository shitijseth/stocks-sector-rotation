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

# yfinance logs every 404 / delisting at ERROR. For bulk pulls across thousands
# of tickers (preferreds, OTC, foreign listings) this drowns the real signal.
# Per-ticker failures are not actionable; we already skip absent columns.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

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

    new_cols_close: list[pd.DataFrame] = []  # full window for new tickers (concat axis=1)
    new_cols_vol: list[pd.DataFrame] = []
    new_rows_close: list[pd.DataFrame] = []  # extension rows for existing tickers (concat axis=0)
    new_rows_vol: list[pd.DataFrame] = []

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
            new_cols_close.append(_extract_field(df, batch, "Adj Close"))
            new_cols_vol.append(_extract_field(df, batch, "Volume"))

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
            new_rows_close.append(_extract_field(df, batch, "Adj Close"))
            new_rows_vol.append(_extract_field(df, batch, "Volume"))

    close_df = _merge(cached_close, new_cols_close, new_rows_close)
    vol_df = _merge(cached_vol, new_cols_vol, new_rows_vol)

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


def _merge(
    cached: pd.DataFrame | None,
    new_col_frames: list[pd.DataFrame],
    new_row_frames: list[pd.DataFrame],
) -> pd.DataFrame | None:
    """Combine cached prices with newly-fetched data.

    `new_col_frames` add new ticker columns over the full window — concat axis=1.
    `new_row_frames` add new dates to existing ticker columns — concat axis=0.
    """
    new_col_frames = [f for f in new_col_frames if f is not None and not f.empty]
    new_row_frames = [f for f in new_row_frames if f is not None and not f.empty]
    if cached is None and not new_col_frames and not new_row_frames:
        return None

    # Step 1: widen by adding new ticker columns.
    if new_col_frames:
        new_cols = pd.concat(new_col_frames, axis=1)
        new_cols = new_cols.loc[:, ~new_cols.columns.duplicated(keep="last")]
        if cached is None:
            wide = new_cols
        else:
            # Avoid clobbering cached columns: only add columns that aren't already present.
            extra = [c for c in new_cols.columns if c not in cached.columns]
            wide = pd.concat([cached, new_cols[extra]], axis=1) if extra else cached
    else:
        wide = cached

    # Step 2: append new dates onto existing ticker columns.
    if new_row_frames and wide is not None:
        ext = pd.concat(new_row_frames, axis=1)
        ext = ext.loc[:, ~ext.columns.duplicated(keep="last")]
        # Reindex extension columns to wide schema (NaN for cols not in extension).
        ext = ext.reindex(columns=wide.columns)
        wide = pd.concat([wide, ext], axis=0)

    if wide is None:
        return None
    wide = wide.sort_index()
    wide = wide[~wide.index.duplicated(keep="last")]
    return wide


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
