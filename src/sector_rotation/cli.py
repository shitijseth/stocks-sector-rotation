"""End-to-end orchestration: universe -> data -> tags -> analysis -> reports."""
from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path

# Process-wide network timeout. yfinance occasionally connects to endpoints
# that hang forever; a default socket timeout means any blocking read/connect
# raises socket.timeout (caught by yfinance/requests as a normal failure)
# instead of pinning a worker thread indefinitely.
socket.setdefaulttimeout(20)

import click
import pandas as pd

from .analysis import clustering, group_perf
from .config import (
    CACHE_DIR,
    ETF_MIN_WEIGHT,
    LOOKBACK_YEARS,
    MIN_AVG_DOLLAR_VOLUME,
    REPORTS_DIR,
    THEMATIC_ETFS,
)
from .fundamentals import derive_quant_tags, fetch_fundamentals
from .prices import fetch_prices
from . import report
from .tags import combine, edgar, etfs, finnhub, fmp, wikidata
from .universe import UniverseFilters, fetch_universe

log = logging.getLogger("sector_rotation")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )


@click.group()
@click.option("-v", "--verbose", is_flag=True)
def cli(verbose: bool) -> None:
    """Sector-rotation analysis CLI."""
    _setup_logging(verbose)


@cli.command()
@click.option("--refresh", is_flag=True, help="Force-refresh the universe download.")
@click.option("--max-tickers", type=int, default=None, help="Cap universe size (for testing).")
@click.option("--include-etfs", is_flag=True)
def universe(refresh: bool, max_tickers: int | None, include_etfs: bool) -> None:
    """Build the tradable US universe."""
    filt = UniverseFilters(include_etfs=include_etfs)
    df = fetch_universe(filt, refresh=refresh)
    if max_tickers:
        df = df.head(max_tickers)
    click.echo(f"Universe size: {len(df)}")
    click.echo(df["exchange"].value_counts().to_string())


@cli.command()
@click.option("--limit", type=int, default=None, help="Subset for quick testing.")
@click.option("--include-etfs", is_flag=True)
@click.option("--liquidity-filter/--no-liquidity-filter", default=True)
def ingest(limit: int | None, include_etfs: bool, liquidity_filter: bool) -> None:
    """Pull universe + 5y prices + fundamentals (cached)."""
    u = fetch_universe(UniverseFilters(include_etfs=include_etfs))
    if limit:
        u = u.head(limit)
    tickers = u["ticker"].tolist()
    log.info("Ingesting prices for %d tickers", len(tickers))
    fetch_prices(tickers)
    log.info("Ingesting fundamentals for %d tickers", len(tickers))
    fetch_fundamentals(tickers)
    if liquidity_filter:
        from .prices import liquid_universe

        kept = liquid_universe(tickers, MIN_AVG_DOLLAR_VOLUME)
        log.info("Liquid (>=$%.0f ADV): %d / %d", MIN_AVG_DOLLAR_VOLUME, len(kept), len(tickers))


@cli.command()
@click.option("--limit", type=int, default=None)
@click.option("--include-wikidata/--no-include-wikidata", default=True)
def tag(limit: int | None, include_wikidata: bool) -> None:
    """Build the unified tag table."""
    u = fetch_universe(UniverseFilters())
    if limit:
        u = u.head(limit)
    tickers = u["ticker"].tolist()

    log.info("Layer 1/5: ETF holdings (%d ETFs)", len(THEMATIC_ETFS))
    holdings = etfs.fetch_etf_holdings()
    etf_tags = etfs.derive_etf_tags(holdings, min_weight=ETF_MIN_WEIGHT)

    log.info("Layer 2/5: yfinance fundamentals -> quant + GICS")
    fund = fetch_fundamentals(tickers)
    quant_tags = derive_quant_tags(fund)

    log.info("Layer 3/5: SEC EDGAR SIC")
    edgar_profiles = edgar.fetch_edgar_profiles(tickers)
    edgar_tags = edgar.derive_edgar_tags(edgar_profiles)

    if include_wikidata:
        log.info("Layer 4/5: Wikidata SPARQL (this is slow)")
        wd = wikidata.fetch_wikidata(tickers)
        wd_tags = wikidata.derive_wikidata_tags(wd)
    else:
        wd_tags = pd.DataFrame(columns=["ticker", "tag", "source"])

    log.info("Layer 5/5: Optional Finnhub + FMP")
    fh = finnhub.fetch_finnhub_profiles(tickers)
    fh_tags = finnhub.derive_finnhub_tags(fh)
    fp = fmp.fetch_fmp_profiles(tickers)
    fp_tags = fmp.derive_fmp_tags(fp)

    merged = combine.combine_tags(etf_tags, quant_tags, edgar_tags, wd_tags, fh_tags, fp_tags)
    log.info("Merged tag table: %d rows, %d unique tags, %d unique tickers",
             len(merged), merged["tag"].nunique(), merged["ticker"].nunique())
    by_source = merged.groupby("source").size()
    click.echo("Rows by source:")
    click.echo(by_source.to_string())


@cli.command()
@click.option("--horizon-days", type=int, default=63, help="Rolling window in trading days.")
@click.option("--top-n", type=int, default=15)
@click.option("--freq", default="ME", help="Resample frequency for leadership table.")
def analyze(horizon_days: int, top_n: int, freq: str) -> None:
    """Run group-performance + clustering analyses on the cached data."""
    tags_long_path = CACHE_DIR / "tags_long.parquet"
    if not tags_long_path.exists():
        raise click.ClickException("No tag table cached. Run `sector-rotation tag` first.")
    tags_long = pd.read_parquet(tags_long_path)

    from .prices import ADJCLOSE_PARQUET

    if not ADJCLOSE_PARQUET.exists():
        raise click.ClickException("No price cache. Run `sector-rotation ingest` first.")
    close = pd.read_parquet(ADJCLOSE_PARQUET)

    daily = group_perf.compute_returns(close)
    momentum = group_perf.momentum_table(daily, tags_long)
    report.write_momentum_csv(momentum)
    report.plot_momentum_bars(momentum, horizon="3m")
    report.plot_momentum_bars(momentum, horizon="1y")

    tag_daily = group_perf.tag_daily_returns(daily, tags_long)
    rolling = group_perf.rolling_tag_returns(tag_daily, window=horizon_days)
    leaders = group_perf.leadership_ranking(rolling, top_n=top_n, freq=freq)
    report.write_leadership_csv(leaders)
    report.plot_leadership_heatmap(leaders)

    log.info("Clustering current %d-day window", horizon_days)
    cluster_res = clustering.cluster_window(daily, window_days=horizon_days)
    cluster_tags = clustering.label_clusters_with_tags(cluster_res.labels, tags_long)
    report.write_cluster_summary(cluster_res.summary, cluster_tags)

    click.echo(f"Reports written to {REPORTS_DIR}")


@cli.command()
def all() -> None:  # noqa: A003
    """Convenience: ingest -> tag -> analyze."""
    ctx = click.get_current_context()
    ctx.invoke(ingest)
    ctx.invoke(tag)
    ctx.invoke(analyze)


@cli.command()
@click.option("--port", type=int, default=8501, help="Port to bind the Streamlit server.")
@click.option("--no-browser", is_flag=True, help="Don't auto-open the browser.")
def dashboard(port: int, no_browser: bool) -> None:
    """Launch the interactive Streamlit dashboard."""
    import subprocess
    from pathlib import Path

    from . import dashboard as dash_mod

    app = Path(dash_mod.__file__).resolve()
    cmd = [
        "streamlit", "run", str(app),
        "--server.port", str(port),
        "--server.headless", "true" if no_browser else "false",
    ]
    log.info("Launching: %s", " ".join(cmd))
    subprocess.run(cmd, check=False)


if __name__ == "__main__":
    cli()
