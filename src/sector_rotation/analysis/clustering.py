"""Discover groups of co-moving stocks from price returns alone.

This complements ETF/EDGAR/Wikidata tagging — clusters that don't match any
known theme tag often surface emergent groupings (e.g. 'GLP-1 winners 2023',
'AI-PC beneficiaries 2024').

Method:
  1. Compute daily returns over a window (e.g. last 252 trading days).
  2. Build a correlation distance matrix d_ij = sqrt(0.5 * (1 - corr_ij)).
  3. Hierarchical clustering (average linkage) -> flat clusters at threshold.
  4. Score each cluster by mean return and median pairwise correlation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

log = logging.getLogger(__name__)


@dataclass
class ClusterResult:
    labels: pd.Series  # ticker -> cluster id
    summary: pd.DataFrame  # per-cluster stats


def _corr_distance(returns: pd.DataFrame) -> np.ndarray:
    corr = returns.corr().fillna(0.0).clip(-1.0, 1.0)
    dist = np.sqrt(0.5 * (1.0 - corr.values))
    np.fill_diagonal(dist, 0.0)
    return dist


def cluster_window(
    daily_ret: pd.DataFrame,
    window_end: pd.Timestamp | None = None,
    window_days: int = 252,
    distance_threshold: float = 0.55,
    min_cluster_size: int = 3,
) -> ClusterResult:
    """Cluster tickers active in [window_end - window_days, window_end].

    `distance_threshold` is in correlation-distance units (0=identical, 1=anticorrelated).
    Lower => tighter clusters.
    """
    if daily_ret.empty:
        return ClusterResult(pd.Series(dtype=int), pd.DataFrame())

    end = window_end or daily_ret.index.max()
    window = daily_ret.loc[:end].tail(window_days)
    # Drop tickers with too many NaNs in the window.
    valid = window.dropna(thresh=int(0.8 * len(window)), axis=1)
    if valid.shape[1] < min_cluster_size:
        return ClusterResult(pd.Series(dtype=int), pd.DataFrame())

    valid = valid.fillna(0.0)
    dist = _corr_distance(valid)
    condensed = squareform(dist, checks=False)
    Z = linkage(condensed, method="average")
    flat = fcluster(Z, t=distance_threshold, criterion="distance")
    labels = pd.Series(flat, index=valid.columns, name="cluster")

    # Summary stats per cluster.
    rows: list[dict] = []
    corr = valid.corr()
    for cid in sorted(labels.unique()):
        members = labels[labels == cid].index.tolist()
        if len(members) < min_cluster_size:
            continue
        sub = valid[members]
        cum_ret = (1.0 + sub).prod() - 1.0
        # Median off-diagonal correlation.
        sub_corr = corr.loc[members, members].values
        iu = np.triu_indices_from(sub_corr, k=1)
        rows.append(
            {
                "cluster": int(cid),
                "size": len(members),
                "members": members,
                "mean_return": float(cum_ret.mean()),
                "median_return": float(cum_ret.median()),
                "median_intracluster_corr": float(np.median(sub_corr[iu])) if len(iu[0]) else np.nan,
            }
        )
    summary = (
        pd.DataFrame(rows)
        .sort_values("mean_return", ascending=False)
        .reset_index(drop=True)
        if rows
        else pd.DataFrame()
    )
    return ClusterResult(labels, summary)


def label_clusters_with_tags(
    cluster_labels: pd.Series, tags_long: pd.DataFrame, top_k: int = 3
) -> pd.DataFrame:
    """For each cluster, return the most over-represented tags vs the universe.

    Uses simple support: fraction of cluster members carrying the tag, minus
    fraction of the rest of the universe carrying it. Tags with at least 50%
    cluster support and positive lift are kept.
    """
    if cluster_labels.empty or tags_long.empty:
        return pd.DataFrame(columns=["cluster", "tag", "support", "lift"])

    universe = set(cluster_labels.index)
    tag_to_tickers = tags_long.groupby("tag")["ticker"].apply(lambda s: set(s))

    rows: list[dict] = []
    for cid in sorted(cluster_labels.unique()):
        members = set(cluster_labels[cluster_labels == cid].index)
        non_members = universe - members
        if not members:
            continue
        scored: list[tuple[str, float, float]] = []
        for tag, tickers in tag_to_tickers.items():
            in_cluster = len(tickers & members) / max(1, len(members))
            in_other = len(tickers & non_members) / max(1, len(non_members))
            if in_cluster < 0.5:
                continue
            lift = in_cluster - in_other
            if lift > 0:
                scored.append((tag, in_cluster, lift))
        scored.sort(key=lambda x: x[2], reverse=True)
        for tag, support, lift in scored[:top_k]:
            rows.append({"cluster": int(cid), "tag": tag, "support": support, "lift": lift})
    return pd.DataFrame(rows)
