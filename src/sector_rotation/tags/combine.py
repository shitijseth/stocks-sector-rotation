"""Merge tag layers from all sources into one long table + a wide pivot."""
from __future__ import annotations

import logging

import pandas as pd

from ..config import CACHE_DIR

log = logging.getLogger(__name__)

TAGS_LONG_PARQUET = CACHE_DIR / "tags_long.parquet"


def combine_tags(*layers: pd.DataFrame) -> pd.DataFrame:
    """Concatenate per-source tag tables, dedupe, and persist.

    Each layer must have columns [ticker, tag, source]. Output is the unique
    union across sources.
    """
    parts = [df for df in layers if df is not None and not df.empty]
    if not parts:
        return pd.DataFrame(columns=["ticker", "tag", "source"])
    out = pd.concat(parts, ignore_index=True)
    out = out.dropna(subset=["ticker", "tag"]).copy()
    out["ticker"] = out["ticker"].str.upper()
    out = out.drop_duplicates(subset=["ticker", "tag", "source"]).reset_index(drop=True)
    out.to_parquet(TAGS_LONG_PARQUET, index=False)
    return out


def tag_membership(tags_long: pd.DataFrame, tag_prefixes: tuple[str, ...] | None = None) -> dict[str, list[str]]:
    """Return {tag: [tickers]} optionally filtered to tags starting with any prefix."""
    if tags_long.empty:
        return {}
    df = tags_long
    if tag_prefixes:
        df = df[df["tag"].str.startswith(tag_prefixes)]
    return df.groupby("tag")["ticker"].apply(lambda s: sorted(set(s))).to_dict()
