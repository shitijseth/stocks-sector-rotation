"""Reporting helpers — CSV exports + matplotlib heatmaps for sector rotation."""
from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .config import REPORTS_DIR  # noqa: E402

log = logging.getLogger(__name__)


def write_leadership_csv(leadership: pd.DataFrame, path: Path | None = None) -> Path:
    path = path or REPORTS_DIR / "leadership.csv"
    leadership.to_csv(path, index=False)
    return path


def write_momentum_csv(momentum: pd.DataFrame, path: Path | None = None) -> Path:
    path = path or REPORTS_DIR / "momentum.csv"
    momentum.to_csv(path)
    return path


def write_cluster_summary(
    summary: pd.DataFrame, labelled_tags: pd.DataFrame, path: Path | None = None
) -> Path:
    path = path or REPORTS_DIR / "clusters.csv"
    if summary.empty:
        pd.DataFrame().to_csv(path)
        return path
    out = summary.copy()
    out["members"] = out["members"].apply(lambda xs: ",".join(xs))
    if not labelled_tags.empty:
        # Aggregate top tags per cluster into a single column.
        tag_str = (
            labelled_tags.assign(
                txt=lambda d: d["tag"] + " (" + (d["lift"] * 100).round(1).astype(str) + "%)"
            )
            .groupby("cluster")["txt"]
            .apply(" | ".join)
            .rename("top_tags")
        )
        out = out.merge(tag_str, left_on="cluster", right_index=True, how="left")
    out.to_csv(path, index=False)
    return path


def plot_leadership_heatmap(
    leadership: pd.DataFrame,
    top_n_tags: int = 25,
    path: Path | None = None,
) -> Path | None:
    """Heatmap of top-N tags' returns across periods."""
    if leadership.empty:
        return None
    path = path or REPORTS_DIR / "leadership_heatmap.png"

    # Pick the tags that appear most often in the top ranks.
    counts = leadership.groupby("tag").size().sort_values(ascending=False).head(top_n_tags)
    keep = counts.index.tolist()
    sub = leadership[leadership["tag"].isin(keep)]
    pivot = sub.pivot_table(index="tag", columns="period", values="return", aggfunc="last")
    pivot = pivot.reindex(keep)

    fig, ax = plt.subplots(figsize=(max(8, pivot.shape[1] * 0.4), max(6, len(keep) * 0.3)))
    vmax = np.nanmax(np.abs(pivot.values)) if pivot.size else 0.1
    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn", vmin=-vmax, vmax=vmax)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index, fontsize=8)
    ax.set_xticks(range(pivot.shape[1]))
    ax.set_xticklabels(
        [t.strftime("%Y-%m") for t in pivot.columns], rotation=90, fontsize=7
    )
    ax.set_title("Tag rolling return — top-N most-frequent leaders")
    fig.colorbar(im, ax=ax, label="rolling return")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def plot_momentum_bars(momentum: pd.DataFrame, horizon: str = "3m", top_n: int = 30,
                       path: Path | None = None) -> Path | None:
    if momentum.empty or horizon not in momentum.columns:
        return None
    path = path or REPORTS_DIR / f"momentum_{horizon}.png"
    s = momentum[horizon].dropna().sort_values(ascending=True).tail(top_n)
    fig, ax = plt.subplots(figsize=(8, max(4, len(s) * 0.25)))
    colors = ["#2c8a4a" if v >= 0 else "#b03a2e" for v in s.values]
    ax.barh(range(len(s)), s.values, color=colors)
    ax.set_yticks(range(len(s)))
    ax.set_yticklabels(s.index, fontsize=8)
    ax.axvline(0, color="black", linewidth=0.5)
    ax.set_xlabel(f"{horizon} return")
    ax.set_title(f"Top {top_n} tags — {horizon} return")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path
