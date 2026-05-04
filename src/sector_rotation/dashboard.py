"""Streamlit dashboard for the sector-rotation pipeline outputs.

Run with:
    streamlit run src/sector_rotation/dashboard.py
or via CLI:
    sector-rotation dashboard
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from sector_rotation.config import CACHE_DIR, REPORTS_DIR
from sector_rotation.prices import ADJCLOSE_PARQUET

st.set_page_config(
    page_title="Sector Rotation",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------- data loaders (cached) -----------------------

@st.cache_data(show_spinner=False)
def load_prices() -> pd.DataFrame:
    if ADJCLOSE_PARQUET.exists():
        return pd.read_parquet(ADJCLOSE_PARQUET)
    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_tags() -> pd.DataFrame:
    p = CACHE_DIR / "tags_long.parquet"
    if p.exists():
        return pd.read_parquet(p)
    return pd.DataFrame(columns=["ticker", "tag", "source"])


@st.cache_data(show_spinner=False)
def load_fundamentals() -> pd.DataFrame:
    p = CACHE_DIR / "fundamentals.parquet"
    if p.exists():
        return pd.read_parquet(p)
    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_csv(name: str) -> pd.DataFrame:
    p = REPORTS_DIR / name
    if p.exists() and p.stat().st_size > 10:
        return pd.read_csv(p)
    return pd.DataFrame()


# ----------------------- shared helpers -----------------------

def fmt_pct(x) -> str:
    if pd.isna(x):
        return "—"
    return f"{x*100:+.1f}%"


def members_list(s: object) -> list[str]:
    if not isinstance(s, str) or not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


# ----------------------- sidebar -----------------------

st.sidebar.title("📊 Sector Rotation")
prices = load_prices()
tags = load_tags()
fund = load_fundamentals()

if prices.empty:
    st.error(
        "No data cached yet. Run `sector-rotation ingest && sector-rotation tag && "
        "sector-rotation analyze` first."
    )
    st.stop()

st.sidebar.markdown(
    f"""
**Universe**: {prices.shape[1]} tickers
**Price history**: {prices.index.min().date()} → {prices.index.max().date()}
**Tag rows**: {len(tags):,}
**Unique tags**: {tags['tag'].nunique() if not tags.empty else 0}
**Fundamentals**: {len(fund)}
"""
)

page = st.sidebar.radio(
    "View",
    ["Overview", "Themes & momentum", "Leadership over time", "Co-moving clusters", "Ticker drill-down"],
)


# ----------------------- pages -----------------------

def page_overview() -> None:
    st.title("Overview")

    momentum = load_csv("momentum.csv")
    if momentum.empty:
        st.warning("Run `sector-rotation analyze` to populate the reports.")
        return
    momentum = momentum.rename(columns={"Unnamed: 0": "tag"}).set_index("tag")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tickers", f"{prices.shape[1]:,}")
    col2.metric("Unique tags", f"{tags['tag'].nunique():,}" if not tags.empty else "0")
    col3.metric("Tags w/ ≥5 members", f"{(momentum['members'] >= 5).sum():,}")
    col4.metric("Days of history", f"{len(prices):,}")

    st.subheader("Top 25 tags by 3-month return (≥5 members)")
    m = momentum[momentum["members"] >= 5].sort_values("3m", ascending=False).head(25)
    fig = px.bar(
        m.reset_index(),
        x="3m",
        y="tag",
        orientation="h",
        color="3m",
        color_continuous_scale="RdYlGn",
        labels={"3m": "3-month return"},
        height=600,
    )
    fig.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
    fig.update_xaxes(tickformat=".0%")
    st.plotly_chart(fig, width="stretch")

    st.subheader("Top 25 tags by 1-year return (≥5 members)")
    m1y = momentum[momentum["members"] >= 5].sort_values("1y", ascending=False).head(25)
    fig2 = px.bar(
        m1y.reset_index(),
        x="1y",
        y="tag",
        orientation="h",
        color="1y",
        color_continuous_scale="RdYlGn",
        labels={"1y": "1-year return"},
        height=600,
    )
    fig2.update_layout(yaxis={"categoryorder": "total ascending"}, coloraxis_showscale=False)
    fig2.update_xaxes(tickformat=".0%")
    st.plotly_chart(fig2, width="stretch")


def page_themes() -> None:
    st.title("Themes & momentum")

    momentum = load_csv("momentum.csv")
    if momentum.empty:
        st.warning("No momentum data. Run `sector-rotation analyze` first.")
        return
    momentum = momentum.rename(columns={"Unnamed: 0": "tag"})

    col1, col2, col3 = st.columns([2, 1, 1])
    search = col1.text_input("Filter tags (substring)", value="")
    min_members = col2.number_input("Min members", min_value=1, value=5, step=1)
    sort_by = col3.selectbox("Sort by", ["3m", "1m", "6m", "1y", "3y", "5y"], index=0)

    df = momentum.copy()
    if search:
        df = df[df["tag"].str.contains(search, case=False, regex=False)]
    df = df[df["members"] >= min_members]
    df = df.sort_values(sort_by, ascending=False)

    st.write(f"**{len(df)} tags** matching filters")
    pct_cols = ["1m", "3m", "6m", "1y", "3y", "5y"]
    styled = (
        df[["tag", *pct_cols, "members"]]
        .style.format({c: "{:+.1%}" for c in pct_cols})
        .background_gradient(subset=pct_cols, cmap="RdYlGn", vmin=-1, vmax=2)
    )
    st.dataframe(styled, width="stretch", height=500)

    st.divider()
    st.subheader("Tag detail")
    tag_options = df["tag"].tolist()
    if not tag_options:
        return
    chosen = st.selectbox("Pick a tag to inspect", tag_options)
    if not chosen:
        return

    members = sorted(set(tags.loc[tags["tag"] == chosen, "ticker"]))
    cols_avail = [t for t in members if t in prices.columns]
    st.write(f"**{len(members)} members** ({len(cols_avail)} with price data)")
    if not cols_avail:
        st.info("No price data for these members.")
        return

    # Equal-weight cumulative return chart vs SPY if present.
    sub = prices[cols_avail].dropna(thresh=int(0.5 * len(prices)), axis=1)
    daily = sub.pct_change().fillna(0).clip(-0.5, 0.5)  # robust clip
    eq_weight = daily.mean(axis=1)
    cum = (1.0 + eq_weight).cumprod()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=cum.index, y=cum.values, name=chosen, line=dict(width=2)))
    if "SPY" in prices.columns:
        spy = prices["SPY"].pct_change().fillna(0)
        fig.add_trace(go.Scatter(x=spy.index, y=(1 + spy).cumprod().values, name="SPY", line=dict(dash="dot")))
    fig.update_layout(
        title=f"Equal-weight cumulative return — {chosen}",
        yaxis_title="Growth of $1",
        height=420,
    )
    st.plotly_chart(fig, width="stretch")

    st.write("**Members:**")
    st.write(", ".join(members))


def page_leadership() -> None:
    st.title("Leadership over time")
    leaders = load_csv("leadership.csv")
    if leaders.empty:
        st.warning("No leadership data. Run `sector-rotation analyze`.")
        return
    leaders["period"] = pd.to_datetime(leaders["period"])

    # Heatmap of top-N most-frequent leaders.
    top_n = st.slider("How many tags to show in the heatmap", 10, 60, 25)
    drop_sic_dup = st.checkbox("Hide raw `sic:` codes (mirror sic-desc)", value=True)

    df = leaders.copy()
    if drop_sic_dup:
        df = df[~df["tag"].str.startswith("sic:")]
    counts = df.groupby("tag").size().sort_values(ascending=False).head(top_n)
    keep = counts.index.tolist()
    sub = df[df["tag"].isin(keep)]
    pivot = sub.pivot_table(index="tag", columns="period", values="return", aggfunc="last").reindex(keep)
    pivot.columns = [c.strftime("%Y-%m") for c in pivot.columns]

    if pivot.empty:
        st.info("No leadership data after filtering.")
        return

    # Robust cap for color scale (microcap pumps blow up the legend).
    cap = float(pivot.abs().quantile(0.95).max() if pivot.size else 1)
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale="RdYlGn",
            zmin=-cap,
            zmax=cap,
            hovertemplate="%{y}<br>%{x}<br>%{z:.0%}<extra></extra>",
        )
    )
    fig.update_layout(height=max(500, len(pivot) * 22), margin=dict(l=240))
    st.plotly_chart(fig, width="stretch")

    st.divider()
    st.subheader("Top leaders for a specific month")
    months = sorted(leaders["period"].dt.strftime("%Y-%m").unique(), reverse=True)
    chosen = st.selectbox("Month", months)
    if chosen:
        rows = leaders[leaders["period"].dt.strftime("%Y-%m") == chosen].sort_values("rank")
        styled = (
            rows[["rank", "tag", "return"]]
            .style.format({"return": "{:+.1%}"})
            .background_gradient(subset=["return"], cmap="RdYlGn", vmin=-1, vmax=2)
        )
        st.dataframe(styled, width="stretch", height=500)


def page_clusters() -> None:
    st.title("Co-moving clusters")
    window = st.radio("Window", ["1-year", "3-month"], horizontal=True)
    fname = "clusters_1y.csv" if window == "1-year" else "clusters_3m.csv"
    df = load_csv(fname)
    if df.empty:
        st.warning(f"No cluster data ({fname}). Run `sector-rotation analyze`.")
        return

    col1, col2, col3 = st.columns(3)
    min_size = col1.number_input("Min cluster size", min_value=2, value=4, step=1)
    min_corr = col2.slider("Min intra-cluster correlation", 0.0, 1.0, 0.4, 0.05)
    sort_key = col3.selectbox("Sort by", ["mean_return", "size", "median_intracluster_corr"])

    flt = df[(df["size"] >= min_size) & (df["median_intracluster_corr"] >= min_corr)]
    flt = flt.sort_values(sort_key, ascending=False).reset_index(drop=True)
    st.write(f"**{len(flt)} clusters** matching filters")

    # Scatter: size vs mean_return, color by correlation.
    if not flt.empty:
        fig = px.scatter(
            flt,
            x="size",
            y="mean_return",
            color="median_intracluster_corr",
            color_continuous_scale="Viridis",
            hover_data={"top_tags": True, "members": False, "cluster": True},
            labels={"mean_return": "Mean cumulative return", "size": "Cluster size"},
            height=420,
        )
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(fig, width="stretch")

    # Expandable per-cluster.
    for _, r in flt.head(40).iterrows():
        members = members_list(r["members"])
        title = (
            f"Cluster #{int(r['cluster'])} • size {r['size']} • "
            f"mean {fmt_pct(r['mean_return'])} • corr {r['median_intracluster_corr']:.2f}"
        )
        with st.expander(title):
            if isinstance(r.get("top_tags"), str) and r["top_tags"]:
                st.markdown(f"**Top tags:** {r['top_tags']}")
            else:
                st.caption("No tag exceeded the cluster-labelling threshold (heterogeneous group).")
            st.write(", ".join(members))

            cols_avail = [t for t in members if t in prices.columns]
            if cols_avail:
                sub = prices[cols_avail].dropna(thresh=int(0.5 * len(prices)), axis=1)
                if not sub.empty:
                    daily = sub.pct_change().fillna(0).clip(-0.5, 0.5)
                    cum = (1.0 + daily.mean(axis=1)).cumprod()
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=cum.index, y=cum.values, name="cluster (eq-wt)"))
                    if "SPY" in prices.columns:
                        spy = (1 + prices["SPY"].pct_change().fillna(0)).cumprod()
                        fig.add_trace(go.Scatter(x=spy.index, y=spy.values, name="SPY", line=dict(dash="dot")))
                    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig, width="stretch")


def page_ticker() -> None:
    st.title("Ticker drill-down")
    available = sorted(prices.columns)
    chosen = st.selectbox("Ticker", available, index=available.index("NVDA") if "NVDA" in available else 0)
    if not chosen:
        return

    # Snapshot row.
    if not fund.empty and chosen in fund.index:
        row = fund.loc[chosen]
        cols = st.columns(5)
        cols[0].metric("Sector", row.get("sector") or "—")
        cols[1].metric("Industry", row.get("industry") or "—")
        cap = row.get("marketCap")
        cols[2].metric("Market cap", f"${cap/1e9:.1f}B" if pd.notna(cap) else "—")
        pe = row.get("trailingPE")
        cols[3].metric("Trailing P/E", f"{pe:.1f}" if pd.notna(pe) else "—")
        beta = row.get("beta")
        cols[4].metric("Beta", f"{beta:.2f}" if pd.notna(beta) else "—")
        if isinstance(row.get("longBusinessSummary"), str):
            with st.expander("Business summary"):
                st.write(row["longBusinessSummary"])
    else:
        st.info("No fundamentals cached for this ticker.")

    # 5y price.
    s = prices[chosen].dropna()
    if not s.empty:
        fig = go.Figure(go.Scatter(x=s.index, y=s.values, name=chosen))
        fig.update_layout(title=f"{chosen} adjusted close (5y)", height=380)
        st.plotly_chart(fig, width="stretch")

    # All tags carried.
    t_rows = tags[tags["ticker"] == chosen]
    st.subheader(f"Tags ({len(t_rows)})")
    if not t_rows.empty:
        st.dataframe(t_rows[["source", "tag"]].sort_values(["source", "tag"]), width="stretch", height=320)

    # Clusters this ticker is in.
    st.subheader("Clusters this ticker belongs to")
    for window, fname in [("1Y", "clusters_1y.csv"), ("3M", "clusters_3m.csv")]:
        df = load_csv(fname)
        if df.empty:
            continue
        df = df[df["members"].fillna("").str.contains(rf"\b{chosen}\b")]
        if df.empty:
            st.caption(f"{window}: not in any cluster of size ≥ 4.")
            continue
        for _, r in df.iterrows():
            mem = members_list(r["members"])
            st.markdown(
                f"**{window} cluster #{int(r['cluster'])}** • size {r['size']} • "
                f"mean {fmt_pct(r['mean_return'])} • corr {r['median_intracluster_corr']:.2f}"
            )
            if isinstance(r.get("top_tags"), str) and r["top_tags"]:
                st.caption(r["top_tags"])
            st.write(", ".join(mem))


# ----------------------- router -----------------------

PAGES = {
    "Overview": page_overview,
    "Themes & momentum": page_themes,
    "Leadership over time": page_leadership,
    "Co-moving clusters": page_clusters,
    "Ticker drill-down": page_ticker,
}
PAGES[page]()
