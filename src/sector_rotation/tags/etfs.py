"""Theme tags derived from issuer-published ETF holdings.

Lookup order per ETF:
  1. Direct iShares CSV (full holdings) for any iShares fund.
  2. Direct SSGA XLSX (full holdings) for any SPDR fund.
  3. `etf-scraper` (Invesco / Vanguard) — used opportunistically; v0.1.2 has
     a pandas 3.x bug for some funds, in which case we fall through.
  4. yfinance `funds_data.top_holdings` (top 10 only) as last resort.

Each holding with weight >= ETF_MIN_WEIGHT contributes a tag matching the
ETF's curated theme label.
"""
from __future__ import annotations

import io
import logging
from typing import Iterable

import pandas as pd
import requests
from tqdm import tqdm

from ..config import CACHE_DIR, ETF_MIN_WEIGHT, SEC_USER_AGENT, THEMATIC_ETFS

log = logging.getLogger(__name__)

ETF_HOLDINGS_PARQUET = CACHE_DIR / "etf_holdings.parquet"


def _normalize_ticker(t: object) -> str | None:
    if not isinstance(t, str):
        return None
    t = t.strip().upper()
    if not t or t in {"--", "N/A", "USD", "CASH"}:
        return None
    # Strip class suffix dots that yfinance / Robinhood don't use (BRK.B, BF.B handled separately)
    return t


def _extract_weight(row: pd.Series) -> float | None:
    for key in ("weight", "Weight", "% of Net Assets", "weight_pct", "Weighting", "weighting"):
        if key in row and pd.notna(row[key]):
            v = float(row[key])
            # Some sources publish as percent (1.23) others as fraction (0.0123).
            return v / 100.0 if v > 1.0 else v
    return None


_ISHARES_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
}


def _from_ishares(ticker: str, product_url: str) -> pd.DataFrame:
    """Fetch full iShares holdings CSV directly.

    URL pattern: {product_url}/1467271812596.ajax?fileType=csv&fileName={TICKER}_holdings&dataType=fund
    The CSV starts with metadata rows and a blank line, then a "Ticker,Name,..." header.
    """
    csv_url = (
        f"{product_url.rstrip('/')}/1467271812596.ajax"
        f"?fileType=csv&fileName={ticker}_holdings&dataType=fund"
    )
    resp = requests.get(csv_url, headers=_ISHARES_HEADERS, timeout=30)
    if resp.status_code != 200:
        return pd.DataFrame(columns=["ticker", "weight"])
    lines = resp.text.splitlines()
    header_idx = next(
        (i for i, ln in enumerate(lines) if ln.lstrip('"').startswith("Ticker,") or ln.startswith('"Ticker"')),
        None,
    )
    if header_idx is None:
        return pd.DataFrame(columns=["ticker", "weight"])
    body = "\n".join(lines[header_idx:])
    try:
        df = pd.read_csv(io.StringIO(body), thousands=",")
    except Exception:
        return pd.DataFrame(columns=["ticker", "weight"])
    if "Ticker" not in df.columns:
        return pd.DataFrame(columns=["ticker", "weight"])
    weight_col = next((c for c in df.columns if "weight" in c.lower()), None)
    if weight_col is None:
        return pd.DataFrame(columns=["ticker", "weight"])
    out = pd.DataFrame(
        {
            "ticker": df["Ticker"].map(_normalize_ticker),
            # iShares publishes weight as a percent ("3.21").
            "weight": pd.to_numeric(
                df[weight_col].astype(str).str.replace(",", "", regex=False).str.replace("%", ""),
                errors="coerce",
            )
            / 100.0,
        }
    ).dropna(subset=["ticker"])
    out = out[out["weight"].fillna(0) > 0]
    return out


def _from_ssga(ticker: str) -> pd.DataFrame:
    """SPDR / SSGA daily holdings XLSX. Skips header rows."""
    url = f"https://www.ssga.com/us/en/intermediary/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{ticker.lower()}.xlsx"
    resp = requests.get(url, headers=_ISHARES_HEADERS, timeout=30)
    if resp.status_code != 200:
        return pd.DataFrame(columns=["ticker", "weight"])
    try:
        # The header row is usually around row 5-6; let pandas guess by looking for "Ticker" col.
        for skip in (4, 5, 6, 3):
            try:
                df = pd.read_excel(
                    io.BytesIO(resp.content), engine="openpyxl", skiprows=skip
                )
                if "Ticker" in df.columns:
                    break
            except Exception:
                continue
        else:
            return pd.DataFrame(columns=["ticker", "weight"])
    except Exception:
        return pd.DataFrame(columns=["ticker", "weight"])
    weight_col = next((c for c in df.columns if "weight" in str(c).lower()), None)
    if weight_col is None:
        return pd.DataFrame(columns=["ticker", "weight"])
    out = pd.DataFrame(
        {
            "ticker": df["Ticker"].map(_normalize_ticker),
            "weight": pd.to_numeric(df[weight_col], errors="coerce") / 100.0,
        }
    ).dropna(subset=["ticker"])
    out = out[out["weight"].fillna(0) > 0]
    return out


def _from_etf_scraper(etf: str) -> pd.DataFrame:
    from etf_scraper import ETFScraper  # heavy import; defer

    scraper = ETFScraper()
    df = scraper.query_holdings(ticker=etf, holdings_date=None)
    if df is None or df.empty:
        return pd.DataFrame(columns=["ticker", "weight"])
    tcol = next(
        (c for c in df.columns if c.lower() in {"ticker", "holding_ticker", "symbol"}), None
    )
    if tcol is None:
        return pd.DataFrame(columns=["ticker", "weight"])
    out = pd.DataFrame(
        {
            "ticker": df[tcol].map(_normalize_ticker),
            "weight": df.apply(_extract_weight, axis=1),
        }
    ).dropna(subset=["ticker"])
    return out


_LISTINGS_DF: pd.DataFrame | None = None


def _listings_lookup(ticker: str) -> tuple[str | None, str | None]:
    """Return (provider, product_url) from etf-scraper's listings DB."""
    global _LISTINGS_DF
    if _LISTINGS_DF is None:
        try:
            from etf_scraper import ETFScraper

            _LISTINGS_DF = ETFScraper().listings_df
        except Exception:
            _LISTINGS_DF = pd.DataFrame()
    if _LISTINGS_DF.empty:
        return None, None
    hit = _LISTINGS_DF[_LISTINGS_DF["ticker"].astype(str).str.upper() == ticker.upper()]
    if hit.empty:
        return None, None
    row = hit.iloc[0]
    return str(row.get("provider", "")), str(row.get("product_url", "") or "")


def _from_yfinance(etf: str) -> pd.DataFrame:
    import yfinance as yf

    try:
        tk = yf.Ticker(etf)
        fd = getattr(tk, "funds_data", None)
        if fd is None:
            return pd.DataFrame(columns=["ticker", "weight"])
        top = fd.top_holdings  # DataFrame with index=ticker, columns include 'Holding Percent'
        if top is None or top.empty:
            return pd.DataFrame(columns=["ticker", "weight"])
        col = next((c for c in top.columns if "percent" in c.lower() or "weight" in c.lower()), None)
        if col is None:
            return pd.DataFrame(columns=["ticker", "weight"])
        out = top.reset_index().rename(columns={top.index.name or "Symbol": "ticker", col: "weight"})
        out["ticker"] = out["ticker"].map(_normalize_ticker)
        # yfinance reports weight as a fraction usually (0.07 = 7%).
        return out[["ticker", "weight"]].dropna(subset=["ticker"])
    except Exception as e:  # noqa: BLE001
        log.debug("yfinance funds_data failed for %s: %s", etf, e)
        return pd.DataFrame(columns=["ticker", "weight"])


def fetch_etf_holdings(etfs: Iterable[str] | None = None, refresh: bool = False) -> pd.DataFrame:
    """Return a long DataFrame [etf, ticker, weight, theme]. Cached as parquet."""
    etfs = list(etfs) if etfs is not None else list(THEMATIC_ETFS.keys())
    if ETF_HOLDINGS_PARQUET.exists() and not refresh:
        cached = pd.read_parquet(ETF_HOLDINGS_PARQUET)
        if set(etfs).issubset(set(cached["etf"].unique())):
            return cached[cached["etf"].isin(etfs)].reset_index(drop=True)

    frames: list[pd.DataFrame] = []
    for etf in tqdm(etfs, desc="etf-holdings"):
        provider, product_url = _listings_lookup(etf)
        holdings = pd.DataFrame(columns=["ticker", "weight"])

        # 1. Direct iShares CSV.
        if provider and provider.lower() == "ishares" and product_url:
            try:
                holdings = _from_ishares(etf, product_url)
            except Exception as e:  # noqa: BLE001
                log.debug("iShares direct fetch failed for %s: %s", etf, e)

        # 2. Direct SSGA XLSX.
        if holdings.empty and provider and provider.lower() == "ssga":
            try:
                holdings = _from_ssga(etf)
            except Exception as e:  # noqa: BLE001
                log.debug("SSGA direct fetch failed for %s: %s", etf, e)

        # 3. etf-scraper for Invesco / Vanguard (best effort — buggy in 0.1.2).
        if holdings.empty:
            try:
                holdings = _from_etf_scraper(etf)
            except Exception as e:  # noqa: BLE001
                log.debug("etf-scraper failed for %s: %s", etf, e)

        # 4. yfinance top-10 fallback.
        if holdings.empty:
            holdings = _from_yfinance(etf)

        if holdings.empty:
            log.warning("No holdings retrieved for %s (skipping)", etf)
            continue
        holdings = holdings.copy()
        holdings["etf"] = etf
        holdings["theme"] = THEMATIC_ETFS.get(etf, "")
        frames.append(holdings[["etf", "ticker", "weight", "theme"]])

    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["etf", "ticker", "weight", "theme"]
    )
    if not out.empty:
        out.to_parquet(ETF_HOLDINGS_PARQUET, index=False)
    return out


def derive_etf_tags(holdings: pd.DataFrame, min_weight: float = ETF_MIN_WEIGHT) -> pd.DataFrame:
    """Long DataFrame [ticker, tag, source='etf'] from filtered ETF holdings."""
    if holdings.empty:
        return pd.DataFrame(columns=["ticker", "tag", "source"])
    df = holdings[(holdings["weight"].fillna(0) >= min_weight) & (holdings["theme"].astype(bool))]
    out = df[["ticker", "theme"]].rename(columns={"theme": "tag"}).copy()
    out["tag"] = "theme:" + out["tag"]
    out["source"] = "etf"
    out = out.drop_duplicates(subset=["ticker", "tag"]).reset_index(drop=True)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    h = fetch_etf_holdings(["SOXX", "PHO"], refresh=True)
    print(h.head())
    print(derive_etf_tags(h).head())
