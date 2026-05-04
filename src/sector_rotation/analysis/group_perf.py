"""Rolling group performance: which tags are leading the market when?

Given (1) a wide adj-close DataFrame and (2) a long [ticker, tag] table, we
compute equal-weight tag-level returns at multiple horizons and rank them.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def compute_returns(close: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns. Forward-fills no values; just `pct_change`."""
    return close.sort_index().pct_change()


def tag_daily_returns(
    daily_ret: pd.DataFrame,
    tags_long: pd.DataFrame,
    min_members: int = 3,
) -> pd.DataFrame:
    """Equal-weight average of constituent daily returns per tag.

    Returns wide DataFrame: index=date, columns=tag.
    """
    if daily_ret.empty or tags_long.empty:
        return pd.DataFrame()
    out: dict[str, pd.Series] = {}
    grouped = tags_long.groupby("tag")["ticker"].apply(lambda s: sorted(set(s)))
    for tag, members in grouped.items():
        members = [m for m in members if m in daily_ret.columns]
        if len(members) < min_members:
            continue
        out[tag] = daily_ret[members].mean(axis=1, skipna=True)
    return pd.DataFrame(out)


def rolling_tag_returns(tag_daily: pd.DataFrame, window: int) -> pd.DataFrame:
    """Compounded rolling return over `window` trading days."""
    if tag_daily.empty:
        return tag_daily
    return (1.0 + tag_daily).rolling(window=window, min_periods=window).apply(
        lambda x: np.prod(x) - 1.0, raw=True
    )


def leadership_ranking(
    rolling_ret: pd.DataFrame, top_n: int = 15, freq: str = "ME"
) -> pd.DataFrame:
    """Resample the rolling return series and report top_n tags per period.

    Returns long DataFrame [period, rank, tag, return].
    """
    if rolling_ret.empty:
        return pd.DataFrame(columns=["period", "rank", "tag", "return"])
    sampled = rolling_ret.resample(freq).last().dropna(how="all")
    rows: list[dict] = []
    for ts, row in sampled.iterrows():
        ranked = row.dropna().sort_values(ascending=False)
        for rank, (tag, val) in enumerate(ranked.head(top_n).items(), start=1):
            rows.append({"period": ts, "rank": rank, "tag": tag, "return": float(val)})
    return pd.DataFrame(rows)


def momentum_table(daily_ret: pd.DataFrame, tags_long: pd.DataFrame) -> pd.DataFrame:
    """Single-row-per-tag snapshot at multiple horizons (1m / 3m / 6m / 1y / 3y / 5y)."""
    horizons = {
        "1m": 21,
        "3m": 63,
        "6m": 126,
        "1y": 252,
        "3y": 252 * 3,
        "5y": 252 * 5,
    }
    tag_daily = tag_daily_returns(daily_ret, tags_long)
    if tag_daily.empty:
        return pd.DataFrame()

    cols = {}
    for label, n in horizons.items():
        if len(tag_daily) >= n:
            cols[label] = (1.0 + tag_daily.tail(n)).prod() - 1.0
        else:
            cols[label] = np.nan
    member_counts = (
        tags_long.groupby("tag")["ticker"].nunique().rename("members")
    )
    out = pd.DataFrame(cols)
    out = out.join(member_counts, how="left")
    return out.sort_values("3m", ascending=False)
