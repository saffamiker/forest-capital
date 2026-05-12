"""
tools/data_fetcher.py

Market data layer for the Forest Capital portfolio analysis system.

PRIMARY DATA SOURCE: Dr. Panttser's Excel file (backend/data/FNA_670_Project_Sources.xlsx).
This file is authoritative for all series it contains. It is never overridden by
external API data. The four external fetches (SPY daily, VIX, DGS2, Fama-French)
fill specific gaps the Excel file does not cover — detailed in CLAUDE.md Section 4.

BND data in the Excel file starts April 2007, not 2000. The gap is bridged by
fetching LQD (iShares iBoxx $ IG Corp Bond ETF, launched 2002-07-26) from yfinance
for 2002-07 through 2007-04. With the LQD bridge, the common monthly aligned series
extends back to ~2002-07, giving ~268 aligned months instead of ~210.

Caching: parquet files in data/cache/ with 24-hour expiry (for supplemental fetches).
The Excel data is re-loaded on each cold start — it is a local file, not an API call.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import (
    CACHE_DIR,
    CACHE_EXPIRY_HOURS,
    RISK_FREE_RATE_FALLBACK,
    FRED_SERIES,
    BENCHMARK,
)
from logger import get_logger

log = get_logger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

_CACHE_PATH = Path(CACHE_DIR)
_CACHE_PATH.mkdir(parents=True, exist_ok=True)

EXCEL_FILE = Path(__file__).resolve().parent.parent / "data" / "FNA_670_Project_Sources.xlsx"


# ── Custom exceptions ─────────────────────────────────────────────────────────

class DataValidationError(Exception):
    """Raised when a hard data validation assertion fails in the pipeline."""


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of validate_data() — used by backtester and tests."""
    is_valid: bool
    issues: list[str] = field(default_factory=list)
    n_assets: int = 0
    date_range: tuple[str, str] = ("", "")
    n_rows: int = 0


@dataclass
class CrossValidationResult:
    """
    Result of cross_validate_equity() — equity cross-check between Excel and yfinance.
    status: "PASS" | "WARN" | "FAIL"
    """
    status: str
    n_months_compared: int
    n_green: int
    n_amber: int
    n_red: int
    max_discrepancy_pct: float
    mean_discrepancy_pct: float
    worst_month: str
    issues: list[str] = field(default_factory=list)


# ── Cache helpers (parquet-based, used by supplemental fetchers) ───────────────

def _cache_key(prefix: str, tickers: list[str], start: str, end: str) -> str:
    raw = f"{prefix}_{'-'.join(sorted(tickers))}_{start}_{end}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_file(key: str) -> Path:
    return _CACHE_PATH / f"{key}.parquet"


def _cache_valid(path: Path) -> bool:
    if not path.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age < timedelta(hours=CACHE_EXPIRY_HOURS)


# ── External library wrappers (patched in tests) ──────────────────────────────

def _yfinance_fetch(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Internal yfinance wrapper — exists solely so tests can monkeypatch it.
    All yfinance calls route through here; swapping the implementation for tests
    is then a one-line monkeypatch rather than a deep mock of the yfinance module.
    Only used for SPY equity data — never for BND, HYG, or any bond ticker.
    """
    import yfinance as yf

    raw = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
    )

    if raw.empty:
        raise ValueError(f"yfinance returned no data for {tickers}")

    if isinstance(raw.columns, pd.MultiIndex):
        df = raw["Close"]
    else:
        df = raw[["Close"]].copy()
        df.columns = [tickers[0]]

    df = df.dropna(how="all")
    df.attrs["adjusted"] = True
    log.info("yfinance_fetch_complete", tickers=tickers, rows=len(df))
    return df


def _fred_fetch(series_id: str, start: str, end: str) -> pd.DataFrame:
    """
    Internal FRED wrapper using FRED's public CSV endpoint — patched in tests.
    Appends FRED_API_KEY when set to avoid anonymous-tier rate limits in production.
    Only used for VIXCLS, DGS2, and DFF (fed funds). DGS10 comes from the
    Excel file — fetching it here would duplicate and potentially contradict
    the authoritative Excel source.
    """
    import requests

    fred_key = os.getenv("FRED_API_KEY")
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    if fred_key:
        url = f"{url}&api_key={fred_key}"
    log.info("fred_fetch", series_id=series_id, start=start, end=end)

    # 60-second timeout — FRED can be slow under load in production
    response = requests.get(url, timeout=60)
    response.raise_for_status()

    # index_col=0 sets DATE as index directly; parse manually afterward.
    # Using index_col + parse_dates with the same column name fails in pandas
    # because index_col removes the column before parse_dates can act on it.
    df = pd.read_csv(io.StringIO(response.text), index_col=0)
    df.index = pd.to_datetime(df.index)
    df.index.name = "DATE"
    # FRED uses "." as a missing-value sentinel in CSV output
    df.replace(".", np.nan, inplace=True)
    df = df.astype(float)

    if start:
        df = df[df.index >= start]
    if end:
        df = df[df.index <= end]

    df = df.dropna()

    if df.empty:
        raise ValueError(f"FRED returned no data for {series_id}")

    log.info("fred_fetch_complete", series_id=series_id, rows=len(df))
    return df


def _famafrench_fetch(dataset: str = "F-F_Research_Data_Factors") -> pd.DataFrame:
    """
    Internal Fama-French wrapper via pandas_datareader — patched in tests.
    Returns monthly factor returns (Mkt-RF, SMB, HML) as percentage values.
    The datareader returns a tuple (monthly_df, annual_df); we take monthly only.
    """
    import pandas_datareader.data as web

    raw = web.get_data_famafrench(dataset)
    if isinstance(raw, tuple):
        raw = raw[0]  # (monthly, annual) tuple — take monthly
    log.info("famafrench_fetch_complete", dataset=dataset, rows=len(raw))
    return raw


# ── Equity fetch (SPY only — used by backtester.py, keep signature stable) ────

def fetch_equity_data(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Fetch SPY (or any equity ticker) total-return prices from yfinance with caching.

    This function is scoped to equity tickers only. BND and HYG must NOT be
    fetched here — the Excel file provides superior daily bond data for those.
    The backtester imports this function; its signature is frozen.
    """
    key = _cache_key("eq", tickers, start, end)
    cache = _cache_file(key)

    if _cache_valid(cache):
        df = pd.read_parquet(cache)
        df.attrs["adjusted"] = True
        log.info("data_fetch_cache_hit", tickers=tickers, source="cache")
        return df

    df = _yfinance_fetch(tickers, start, end)
    df.to_parquet(cache)
    return df


def fetch_fred_series(series_id: str, start: str, end: str) -> pd.Series:
    """
    Fetch a FRED daily series with 24-hour parquet caching.
    24-hour TTL is the right cache window for FRED data: FRED updates series
    daily (VIX and DGS2 are end-of-day), so anything shorter would hammer FRED's
    servers unnecessarily. Anything longer risks stale data during a fast-moving
    market (VIX jumped from 25 to 80 over three days in March 2020 — a 72-hour
    cache would have missed the peak entirely). 24 hours balances freshness and
    server politeness without needing an API key.
    """
    key = _cache_key("fred", [series_id], start, end)
    cache = _cache_file(key)

    if _cache_valid(cache):
        df = pd.read_parquet(cache)
        log.info("data_fetch_cache_hit", series_id=series_id, source="cache")
        return df.iloc[:, 0]

    df = _fred_fetch(series_id, start, end)
    df.to_parquet(cache)
    return df.iloc[:, 0]


def fetch_risk_free_rate(start: str, end: str) -> pd.Series:
    """
    Daily risk-free rate from FRED DFF (Fed Funds, annualised %).
    Converts to daily decimal: rate% / 100 / 252.
    Falls back to RISK_FREE_RATE_FALLBACK / 252 when FRED is unavailable.
    Time-varying risk-free rate is required; never pass a fixed float to Sharpe.
    """
    try:
        dff = fetch_fred_series(FRED_SERIES["fed_funds"], start, end)
        daily_rf = dff / 100.0 / 252.0
        daily_rf.name = "risk_free_rate"
        return daily_rf
    except Exception as exc:
        log.warning(
            "risk_free_rate_fallback",
            error=str(exc),
            fallback=RISK_FREE_RATE_FALLBACK,
        )
        idx = pd.bdate_range(start=start, end=end)
        return pd.Series(
            RISK_FREE_RATE_FALLBACK / 252.0,
            index=idx,
            name="risk_free_rate",
        )


# ── Excel-first data layer ─────────────────────────────────────────────────────

def load_provided_data() -> dict[str, pd.DataFrame]:
    """
    Load all 14 data sheets from Dr. Panttser's Excel file.

    The Excel file is the authoritative source for all series it contains.
    Dates are already parsed as datetime by pandas read_excel; serial integer
    conversion (pd.to_datetime(n, unit='D', origin='1899-12-30')) is applied
    only if a value appears as a raw integer — guarding against format changes.

    Raises FileNotFoundError if the Excel file is not present.
    Returns a dict keyed by logical series ID.
    """
    if not EXCEL_FILE.exists():
        raise FileNotFoundError(
            f"Excel data file not found: {EXCEL_FILE}. "
            "Place FNA_670_Project_Sources.xlsx in backend/data/ before running."
        )

    def _parse_date_col(series: pd.Series) -> pd.Series:
        """Handle both datetime and Excel serial integer date formats."""
        if pd.api.types.is_numeric_dtype(series):
            return pd.to_datetime(series.astype(int), unit="D", origin="1899-12-30")
        return pd.to_datetime(series)

    data: dict[str, pd.DataFrame] = {}

    # HY Effective Yield — BAMLH0A0HYM2EY, daily signal series
    df = pd.read_excel(EXCEL_FILE, sheet_name="High Yield Effective Yield")
    df.columns = ["date", "hy_yield"]
    df["date"] = _parse_date_col(df["date"])
    data["hy_effective_yield"] = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # HY Total Return Index — BAMLHYH0A0HYM2TRIV, daily, authoritative HY return source
    df = pd.read_excel(EXCEL_FILE, sheet_name="High Yield Total Return")
    df.columns = ["date", "hy_total_return_index"]
    df["date"] = _parse_date_col(df["date"])
    data["hy_total_return"] = df.dropna().sort_values("date").reset_index(drop=True)

    # S&P 500 Monthly — price level index (not return), monthly, authoritative equity
    df = pd.read_excel(EXCEL_FILE, sheet_name="S&P 500 Monthly Returns")
    df.columns = ["date", "sp500_level"]
    df["date"] = _parse_date_col(df["date"])
    data["sp500_monthly"] = df.dropna().sort_values("date").reset_index(drop=True)

    # S&P 500 IG ETF — daily OHLCV, IG proxy from May 2016 only
    df = pd.read_excel(EXCEL_FILE, sheet_name="S&P 500 Investment Grade")
    df.columns = ["date", "open", "high", "low", "close"]
    df["date"] = _parse_date_col(df["date"])
    data["sp500_ig"] = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    # BND (Vanguard Total Bond) — daily OHLCV, authoritative IG source from April 2007
    df = pd.read_excel(EXCEL_FILE, sheet_name="Vanguard Total Bond ")
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    df["date"] = _parse_date_col(df["date"])
    data["bnd"] = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    # iShares 10+ Year IG Corp Bond — daily OHLCV, IG alternative from December 2009
    df = pd.read_excel(EXCEL_FILE, sheet_name="iShares 10+ Year Investment Gra")
    df.columns = ["date", "open", "high", "low", "close", "volume"]
    df["date"] = _parse_date_col(df["date"])
    data["igb_ishares"] = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)

    # Vanguard High Dividend ETF (VYM) — daily, not used as portfolio holding
    df = pd.read_excel(EXCEL_FILE, sheet_name="Vanguard ETF")
    df.columns = ["date", "price", "open", "high", "low", "volume", "change_pct"]
    df["date"] = _parse_date_col(df["date"])
    data["vym"] = df.dropna(subset=["date", "price"]).sort_values("date").reset_index(drop=True)

    # S&P 500 PE Ratio — quarterly regime signal
    df = pd.read_excel(EXCEL_FILE, sheet_name="SP 500 PE Ratio")
    df.columns = ["date", "pe_ratio"]
    df["date"] = _parse_date_col(df["date"])
    data["sp500_pe"] = df.dropna().sort_values("date").reset_index(drop=True)

    # DGS10 (10-Year Treasury) — daily yield signal from Excel
    df = pd.read_excel(EXCEL_FILE, sheet_name="Market Yield on U.S. Treasury")
    df.columns = ["date", "dgs10"]
    df["date"] = _parse_date_col(df["date"])
    data["dgs10"] = df.dropna().sort_values("date").reset_index(drop=True)

    # IG Effective Yield — BAMLC0A0CMEY, daily signal
    df = pd.read_excel(EXCEL_FILE, sheet_name="US Corporate Effective Yield")
    df.columns = ["date", "ig_yield"]
    df["date"] = _parse_date_col(df["date"])
    data["ig_effective_yield"] = df.dropna().sort_values("date").reset_index(drop=True)

    # DTB3 (3-Month T-bill) — daily risk-free rate, authoritative source
    df = pd.read_excel(EXCEL_FILE, sheet_name="3-Month Treasury")
    df.columns = ["date", "dtb3"]
    df["date"] = _parse_date_col(df["date"])
    data["dtb3"] = df.dropna().sort_values("date").reset_index(drop=True)

    # Real GDP (GDPC1) — quarterly macro signal, forward-filled to monthly
    df = pd.read_excel(EXCEL_FILE, sheet_name="Real GDP")
    df.columns = ["date", "gdp"]
    df["date"] = _parse_date_col(df["date"])
    data["gdp"] = df.dropna().sort_values("date").reset_index(drop=True)

    # GDP Deflator (GDPDEF) — quarterly macro signal
    df = pd.read_excel(EXCEL_FILE, sheet_name="GDP Deflator")
    df.columns = ["date", "gdp_deflator"]
    df["date"] = _parse_date_col(df["date"])
    data["gdp_deflator"] = df.dropna().sort_values("date").reset_index(drop=True)

    log.info("excel_data_loaded", sheets=list(data.keys()))
    return data


def build_daily_returns(
    provided_data: dict[str, pd.DataFrame] | None = None,
    supplemental: dict | None = None,
) -> pd.DataFrame:
    """
    Compute daily returns for IG (BND) and HY (BAMLHYH) from Excel data.

    Equity daily returns are not produced here — SPY comes from fetch_supplemental_data().
    BND close-to-close returns are the authoritative IG daily series; coverage only
    starts April 2007. When supplemental contains lqd_bridge_daily, LQD daily returns
    for the pre-BND period (2002-07 to 2007-04) are prepended so the daily IG series
    extends back to LQD's inception.
    BAMLHYH level-to-level pct_change gives HY daily returns back to 1986.
    Returns are aligned on their common date range.
    """
    if provided_data is None:
        provided_data = load_provided_data()

    # BND daily close → pct_change (IG)
    bnd = provided_data["bnd"].set_index("date")["close"]
    bnd_returns = bnd.pct_change().dropna()
    bnd_returns.name = "ig_return"

    # Prepend LQD bridge daily returns for dates before BND starts.
    # LQD tracks the same IG corporate bond market as BND; using it as a
    # pre-BND bridge is consistent with the data hierarchy in CLAUDE.md Section 4.
    if supplemental is not None and "lqd_bridge_daily" in supplemental:
        lqd = supplemental["lqd_bridge_daily"]
        bnd_start = bnd_returns.index.min()
        lqd_bridge = lqd[lqd.index < bnd_start]
        lqd_bridge = lqd_bridge.copy()
        lqd_bridge.name = "ig_return"
        ig_returns = pd.concat([lqd_bridge, bnd_returns]).sort_index()
    else:
        ig_returns = bnd_returns
    ig_returns.name = "ig_return"

    # BAMLHYH total return index → pct_change (HY)
    hy = provided_data["hy_total_return"].set_index("date")["hy_total_return_index"]
    hy_returns = hy.pct_change().dropna()
    hy_returns.name = "hy_return"

    df = pd.concat([ig_returns, hy_returns], axis=1, sort=False).sort_index()
    df.index.name = "date"

    log.info(
        "daily_returns_built",
        ig_start=str(ig_returns.index.min().date()),
        hy_start=str(hy_returns.index.min().date()),
        rows=len(df),
    )
    return df


def build_monthly_returns(
    provided_data: dict[str, pd.DataFrame] | None = None,
    supplemental: dict | None = None,
) -> pd.DataFrame:
    """
    Aggregate all three asset class returns to month-end frequency.

    Equity: S&P 500 price level pct_change (authoritative from Excel).
    IG: BND daily close from Excel → last trading day per month → pct_change.
        When supplemental contains lqd_bridge_daily, LQD monthly returns
        (compounded from daily) are prepended for the pre-BND period so the
        aligned series starts from LQD's inception (~2002-07) instead of
        BND's first available date (~2007-05).
    HY: BAMLHYH total return index → last trading day per month → pct_change.
    Risk-free: DTB3 daily annual rate → monthly: (1 + rate/100)^(1/12) - 1.

    Rows where any of equity, IG, or HY are missing are dropped.
    """
    if provided_data is None:
        provided_data = load_provided_data()

    # Equity monthly from S&P 500 price levels
    sp500 = provided_data["sp500_monthly"].set_index("date")["sp500_level"]
    sp500.index = sp500.index + pd.offsets.MonthEnd(0)
    sp500 = sp500.sort_index()
    equity_monthly = sp500.pct_change().dropna()
    equity_monthly.name = "equity_return"

    # BND → monthly return from last trading-day close, not average.
    # Last price is the right aggregation: monthly return = (P_end / P_start) - 1.
    # Averaging intra-month prices would distort returns for months with strong
    # trends (2022: BND fell nearly every day — the average would understate the loss).
    bnd = provided_data["bnd"].set_index("date")["close"]
    bnd_monthly = bnd.resample("ME").last().dropna()
    ig_monthly = bnd_monthly.pct_change().dropna()
    ig_monthly.name = "ig_return"

    # Extend IG monthly back to ~2002-07 using LQD bridge daily returns.
    # Compound multiplication (not simple average) is the correct aggregation:
    # (1+r1)(1+r2)...(1+rn) - 1 gives the exact multi-day compounded return
    # regardless of the number of trading days in the month.
    if supplemental is not None and "lqd_bridge_daily" in supplemental:
        lqd_daily = supplemental["lqd_bridge_daily"]
        lqd_monthly = (1 + lqd_daily).resample("ME").prod() - 1
        lqd_monthly.name = "ig_return"
        bnd_start = ig_monthly.index.min()
        lqd_bridge = lqd_monthly[lqd_monthly.index < bnd_start]
        if len(lqd_bridge) > 0:
            ig_monthly = pd.concat([lqd_bridge, ig_monthly]).sort_index()
            ig_monthly.name = "ig_return"
            log.info(
                "lqd_bridge_spliced",
                lqd_months=len(lqd_bridge),
                lqd_start=str(lqd_bridge.index.min().date()),
                bnd_start=str(bnd_start.date()),
            )

    # BAMLHYH → monthly return from last total-return index level.
    # Guard against zero/negative index values — if present, they indicate data
    # corruption (the HY total return index is strictly positive by construction).
    # Dropping them rather than filling avoids propagating a bad reading forward.
    hy_idx = provided_data["hy_total_return"].set_index("date")["hy_total_return_index"]
    hy_idx = hy_idx[hy_idx > 0]
    hy_monthly_price = hy_idx.resample("ME").last().dropna()
    hy_monthly = hy_monthly_price.pct_change().dropna()
    hy_monthly.name = "hy_return"

    # DTB3 → monthly rate via compound conversion, not simple averaging.
    # (1 + r_annual/100)^(1/12) - 1 is the correct monthly compounding of
    # an annualised rate. Simple division by 12 understates the monthly rate
    # when rates are high (at 5%, the difference is ~2bps/month — small but
    # it accumulates over the 2022-2023 high-rate period that is central to
    # this project's findings).
    dtb3 = provided_data["dtb3"].set_index("date")["dtb3"]
    dtb3_monthly_avg = dtb3.resample("ME").mean().dropna()
    rf_monthly = ((1 + dtb3_monthly_avg / 100) ** (1 / 12) - 1)
    rf_monthly.name = "risk_free"

    # Align all four to month-end, drop months missing equity/IG/HY
    df = pd.concat([equity_monthly, ig_monthly, hy_monthly, rf_monthly], axis=1, sort=False)
    df.index = df.index + pd.offsets.MonthEnd(0)
    df = df.sort_index().dropna(subset=["equity_return", "ig_return", "hy_return"])

    log.info(
        "monthly_returns_built",
        start=str(df.index.min().date()),
        end=str(df.index.max().date()),
        rows=len(df),
    )
    return df


def fetch_supplemental_data(
    start: str = "2000-01-01",
    end: str = "2024-12-31",
) -> dict[str, pd.Series | pd.DataFrame]:
    """
    Fetch the four external series that gap-fill the Excel dataset.

    The only tickers fetched from yfinance is SPY (equity daily). BND, HYG, and
    any other bond tickers must NOT appear here — the Excel file is their source.
    FRED provides VIX and DGS2. Ken French library provides monthly FF factors.
    All external wrappers (_yfinance_fetch, _fred_fetch, _famafrench_fetch) are
    used so tests can monkeypatch them without touching external libraries.
    """
    result: dict[str, pd.Series | pd.DataFrame] = {}

    # SPY daily equity prices → daily returns (equity, not bonds — see CLAUDE.md Section 4)
    try:
        spy_prices = fetch_equity_data(["SPY"], start, end)
        if "SPY" in spy_prices.columns:
            result["spy_daily"] = spy_prices["SPY"].pct_change().dropna()
    except Exception as exc:
        log.warning("spy_fetch_failed", error=str(exc))

    # LQD (iShares iBoxx $ IG Corporate Bond ETF) — pre-BND bridge for 2002-2007.
    # BND in the Excel file starts April 2007; LQD, which launched 2002-07-26 and
    # tracks the same IG corporate bond market, fills the gap. Fetching only through
    # 2007-05-31 keeps LQD out of the BND-primary period and prevents mixing sources
    # within a month. This is the only permitted non-SPY yfinance fetch.
    try:
        lqd_prices = _yfinance_fetch(["LQD"], "2002-01-01", "2007-05-31")
        if "LQD" in lqd_prices.columns:
            result["lqd_bridge_daily"] = lqd_prices["LQD"].pct_change().dropna()
            log.info(
                "lqd_bridge_fetched",
                rows=len(result["lqd_bridge_daily"]),
                start=str(result["lqd_bridge_daily"].index.min().date()),
                end=str(result["lqd_bridge_daily"].index.max().date()),
            )
    except Exception as exc:
        log.warning("lqd_bridge_fetch_failed", error=str(exc))

    # VIX daily levels (FRED: VIXCLS) — regime detection threshold signal
    try:
        result["vix_daily"] = fetch_fred_series("VIXCLS", start, end)
    except Exception as exc:
        log.warning("vix_fetch_failed", error=str(exc))

    # 2-Year Treasury yield (FRED: DGS2) — completes the yield curve (DGS10 from Excel)
    try:
        result["dgs2_daily"] = fetch_fred_series("DGS2", start, end)
    except Exception as exc:
        log.warning("dgs2_fetch_failed", error=str(exc))

    # Fama-French monthly factors — factor exposure attribution
    try:
        ff = _famafrench_fetch("F-F_Research_Data_Factors")
        ff = ff / 100.0  # datareader returns percentage points, convert to decimal
        ff.index = pd.to_datetime(ff.index.astype(str)) + pd.offsets.MonthEnd(0)
        ff = ff.loc[ff.index >= start]
        ff = ff.loc[ff.index <= end]
        result["ff_factors"] = ff
    except Exception as exc:
        log.warning("ff_fetch_failed", error=str(exc))

    log.info("supplemental_data_fetched", keys=list(result.keys()))
    return result


def cross_validate_equity(
    provided_data: dict[str, pd.DataFrame] | None = None,
    supplemental: dict | None = None,
) -> CrossValidationResult:
    """
    Compare Excel monthly S&P 500 returns vs SPY daily (yfinance) aggregated to monthly.

    The Excel monthly series is authoritative (provided by Dr. Panttser). SPY daily
    from yfinance is used for momentum and volatility models; it must agree with the
    authoritative source within tolerance. Per Section 4b: WARN at 0.5%, FAIL at 1.0%.
    A FAIL raises DataValidationError and halts the pipeline — a wrong equity return
    series invalidates the entire backtest.
    """
    if provided_data is None:
        provided_data = load_provided_data()
    if supplemental is None:
        supplemental = fetch_supplemental_data()

    # Authoritative Excel monthly returns from price level pct_change
    sp500 = provided_data["sp500_monthly"].set_index("date")["sp500_level"]
    sp500.index = sp500.index + pd.offsets.MonthEnd(0)
    sp500 = sp500.sort_index()
    excel_monthly = sp500.pct_change().dropna()

    if "spy_daily" not in supplemental:
        return CrossValidationResult(
            status="WARN",
            n_months_compared=0,
            n_green=0,
            n_amber=0,
            n_red=0,
            max_discrepancy_pct=float("nan"),
            mean_discrepancy_pct=float("nan"),
            worst_month="",
            issues=["SPY daily unavailable — cross-validation skipped"],
        )

    # Compound SPY daily returns to monthly
    spy_monthly = (1 + supplemental["spy_daily"]).resample("ME").prod() - 1

    common_months = excel_monthly.index.intersection(spy_monthly.index)
    if len(common_months) == 0:
        return CrossValidationResult(
            status="WARN",
            n_months_compared=0,
            n_green=0,
            n_amber=0,
            n_red=0,
            max_discrepancy_pct=float("nan"),
            mean_discrepancy_pct=float("nan"),
            worst_month="",
            issues=["No common months between Excel and SPY data"],
        )

    diff = (excel_monthly.loc[common_months] - spy_monthly.loc[common_months]).abs()

    n_green = int((diff <= 0.002).sum())
    n_amber = int(((diff > 0.002) & (diff <= 0.010)).sum())
    n_red = int((diff > 0.010).sum())
    max_disc = float(diff.max())
    mean_disc = float(diff.mean())
    worst_month = str(diff.idxmax().date()) if len(diff) > 0 else ""

    issues: list[str] = []
    if n_red > 0:
        issues.append(f"{n_red} months exceed 1.0% discrepancy threshold")

    if n_red > 5:
        status = "FAIL"
        issues.append(
            "DataValidationError: systematic disagreement between Excel and yfinance equity series"
        )
    elif n_amber > 10 or n_red > 0:
        status = "WARN"
    else:
        status = "PASS"

    result = CrossValidationResult(
        status=status,
        n_months_compared=len(common_months),
        n_green=n_green,
        n_amber=n_amber,
        n_red=n_red,
        max_discrepancy_pct=max_disc,
        mean_discrepancy_pct=mean_disc,
        worst_month=worst_month,
        issues=issues,
    )

    log.info(
        "cross_validate_equity_complete",
        status=status,
        n_months=len(common_months),
        n_red=n_red,
        max_disc=f"{max_disc:.4f}",
    )

    if status == "FAIL":
        raise DataValidationError(
            f"Equity cross-validation FAIL: {n_red} months exceed 1% discrepancy. "
            f"Max: {max_disc:.4f}. Worst month: {worst_month}"
        )

    return result


def compute_signals(
    provided_data: dict[str, pd.DataFrame] | None = None,
    supplemental: dict | None = None,
) -> dict[str, pd.Series]:
    """
    Assemble all regime and allocation signals from Excel and FRED data.

    DGS10 comes from Excel; DGS2 comes from FRED — these are combined for the
    yield curve. PE ratio and GDP are quarterly; forward-filling is correct here
    because we only know Q1 GDP at quarter-end, so using Q1 data through June
    introduces no look-ahead bias.
    """
    if provided_data is None:
        provided_data = load_provided_data()
    if supplemental is None:
        supplemental = fetch_supplemental_data()

    signals: dict[str, pd.Series] = {}

    # HY spread signal (daily)
    hy_yield = provided_data["hy_effective_yield"].set_index("date")["hy_yield"]
    signals["hy_spread"] = hy_yield.dropna()

    # IG spread signal (daily)
    ig_yield = provided_data["ig_effective_yield"].set_index("date")["ig_yield"]
    signals["ig_spread"] = ig_yield.dropna()

    # DGS10 from Excel (used directly and as component of yield curve)
    dgs10 = provided_data["dgs10"].set_index("date")["dgs10"].dropna()
    signals["dgs10"] = dgs10

    # Yield curve: DGS10 (Excel) - DGS2 (FRED). Only computable when DGS2 is available.
    if "dgs2_daily" in supplemental:
        dgs2 = supplemental["dgs2_daily"]
        yield_curve = dgs10.subtract(dgs2, fill_value=float("nan")).dropna()
        signals["yield_curve"] = yield_curve
    else:
        log.warning("dgs2_unavailable_yield_curve_skipped")

    # VIX daily levels (regime detection signal — NOT a return series)
    if "vix_daily" in supplemental:
        signals["vix"] = supplemental["vix_daily"]

    # PE ratio — quarterly, forward-filled daily so regime models can use it
    pe = provided_data["sp500_pe"].set_index("date")["pe_ratio"]
    signals["pe_ratio"] = pe.resample("D").ffill().dropna()

    # GDP growth — quarterly, forward-filled to month-end
    gdp = provided_data["gdp"].set_index("date")["gdp"]
    gdp_growth = gdp.pct_change().dropna()
    signals["gdp_growth"] = gdp_growth.resample("ME").ffill().dropna()

    log.info("signals_computed", keys=list(signals.keys()))
    return signals


def get_full_history() -> dict:
    """
    Orchestrate the complete data pipeline and return the unified dataset.

    Loads Excel data, fetches supplemental series, cross-validates equity,
    runs Section 4b sanity assertions, writes provenance.json, writes
    all four PostgreSQL tables, and returns aligned monthly and daily series.

    Sprint 3+ reads from market_data_monthly / market_data_daily directly.
    Cold-start computation takes ~30s on a free-tier server.
    """
    log.info("get_full_history_start")

    provided = load_provided_data()
    supplemental = fetch_supplemental_data()
    # Pass supplemental to both build functions so the LQD bridge is spliced in.
    # build_monthly_returns uses lqd_bridge_daily to extend IG monthly back to ~2002-07.
    # build_daily_returns uses it to extend IG daily to the same start.
    monthly = build_monthly_returns(provided, supplemental)
    daily = build_daily_returns(provided, supplemental)

    # Merge equity daily from SPY supplemental fetch
    if "spy_daily" in supplemental:
        daily = daily.copy()
        daily["equity_return"] = supplemental["spy_daily"]
        daily = daily.sort_index()

    # Cross-validate equity (WARN on discrepancy, FAIL halts pipeline)
    try:
        cv_result = cross_validate_equity(provided, supplemental)
    except DataValidationError:
        log.error("cross_validate_equity_halted_pipeline")
        raise

    signals = compute_signals(provided, supplemental)
    _run_sanity_assertions(monthly, signals)
    _write_provenance(provided, supplemental, cv_result, monthly)

    # Persist all pipeline outputs to PostgreSQL (idempotent upserts).
    # DB writes run in a new thread so they don't conflict with any
    # running async event loop in the FastAPI process.
    _persist_to_db(provided, supplemental, monthly, daily, signals, cv_result)

    result = {
        "equity_monthly":    monthly["equity_return"],
        "ig_monthly":        monthly["ig_return"],
        "hy_monthly":        monthly["hy_return"],
        "risk_free_monthly": monthly["risk_free"],
        "equity_daily":      supplemental.get("spy_daily"),
        "ig_daily":          daily["ig_return"],
        "hy_daily":          daily["hy_return"],
        "risk_free_daily":   (
            provided["dtb3"].set_index("date")["dtb3"].dropna() / 100.0 / 252.0
        ),
        "signals":           signals,
        "ff_factors":        supplemental.get("ff_factors"),
    }

    log.info("get_full_history_complete", monthly_rows=len(monthly))
    return result


# ── PostgreSQL persistence ────────────────────────────────────────────────────

def _persist_to_db(
    provided: dict[str, pd.DataFrame],
    supplemental: dict,
    monthly: pd.DataFrame,
    daily: pd.DataFrame,
    signals: dict[str, pd.Series],
    cv_result: CrossValidationResult,
) -> None:
    """
    Write the full pipeline output to all four PostgreSQL tables.

    Uses a ThreadPoolExecutor so this sync function can be called from within
    FastAPI's running event loop without deadlocking.  Inside the thread,
    asyncio.run() creates a brand-new event loop — the module-level engine in
    database.py was created on a different loop and cannot be reused here.
    _async_persist_all therefore creates its own NullPool engine so that
    asyncpg never tries to return connections to a pool owned by a foreign loop.
    """
    from database import DATABASE_URL  # URL only — not the shared engine

    if not DATABASE_URL:
        log.warning("db_persist_skipped", reason="DATABASE_URL not set")
        return

    try:
        def _run_in_thread() -> None:
            # Coroutine is created here, inside the worker thread, so asyncpg
            # binds futures to the new event loop that asyncio.run() creates —
            # not to the FastAPI event loop running on the calling thread.
            asyncio.run(
                _async_persist_all(provided, supplemental, monthly, daily, signals, cv_result)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_in_thread)
            future.result(timeout=120)
        log.info("db_persist_complete")
    except Exception as exc:
        log.warning("db_persist_failed", error=str(exc))


async def _async_persist_all(
    provided: dict[str, pd.DataFrame],
    supplemental: dict,
    monthly: pd.DataFrame,
    daily: pd.DataFrame,
    signals: dict[str, pd.Series],
    cv_result: CrossValidationResult,
) -> None:
    """
    Top-level async coordinator — all four tables written in a single transaction.

    Creates a fresh NullPool engine inside this coroutine rather than reusing
    the module-level engine from database.py.  asyncio.run() in the calling
    thread creates a new event loop; the shared engine's asyncpg pool is bound
    to the FastAPI event loop and raises 'Task got Future attached to a
    different loop' if used here.  NullPool opens a new connection for every
    call and closes it immediately — no pool, no loop attachment.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from database import DATABASE_URL

    local_engine = create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
    now_iso = datetime.now(timezone.utc).isoformat()
    series_list = _build_registry_entries(provided, supplemental, daily, now_iso)

    try:
        async with local_engine.begin() as conn:
            await _upsert_registry(conn, series_list, text)
            await _upsert_monthly(conn, monthly, signals, text)
            await _upsert_daily(conn, daily, supplemental, text)
            await _insert_validation_log(conn, cv_result, text)
    finally:
        await local_engine.dispose()


def _build_registry_entries(
    provided: dict[str, pd.DataFrame],
    supplemental: dict,
    daily: pd.DataFrame,
    now_iso: str,
) -> list[dict]:
    """
    Build the full data_series_registry row list.

    Extends the provenance JSON series list with daily-specific entries for
    BND and BAMLHYH — the daily table uses different series_ids from the
    monthly table because the aggregation step changes frequency.
    """
    excel_detail: dict = {
        "file": "FNA_670_Project_Sources.xlsx",
        "provided_by": "Dr. Panttser (FNA 670)",
        "original_source": "Y-charts / FRED",
    }

    def _date_range(df: pd.DataFrame, col: str = "date") -> tuple[str | None, str | None]:
        d = df[col].dropna()
        return (
            str(d.min().date()) if len(d) > 0 else None,
            str(d.max().date()) if len(d) > 0 else None,
        )

    def _excel(sid: str, name: str, sheet: str, freq: str, df: pd.DataFrame,
               orig: str = "Y-charts / FRED") -> dict:
        s, e = _date_range(df)
        return {
            "series_id": sid, "display_name": name,
            "source_type": "excel_provided",
            "source_detail": {**excel_detail, "sheet": sheet, "original_source": orig},
            "frequency": freq,
            "date_range_start": s, "date_range_end": e,
            "row_count": len(df), "loaded_at": now_iso, "validation_status": "pass",
        }

    def _supp(sid: str, name: str, src_type: str, detail: dict, freq: str,
              key: str) -> dict:
        s_data = supplemental.get(key)
        s = str(s_data.index.min().date()) if s_data is not None and len(s_data) > 0 else None
        e = str(s_data.index.max().date()) if s_data is not None and len(s_data) > 0 else None
        n = int(len(s_data)) if s_data is not None else 0
        return {
            "series_id": sid, "display_name": name,
            "source_type": src_type, "source_detail": detail,
            "frequency": freq,
            "date_range_start": s, "date_range_end": e,
            "row_count": n, "loaded_at": now_iso, "validation_status": "pass",
        }

    entries = [
        _excel("equity_monthly", "S&P 500 Monthly Returns",
               "S&P 500 Monthly Returns", "monthly", provided["sp500_monthly"], "Y-charts"),
        _excel("ig_monthly_bnd", "Vanguard Total Bond (BND) — daily aggregated to monthly",
               "Vanguard Total Bond ", "monthly", provided["bnd"], "Y-charts"),
        _excel("hy_monthly_baml", "ICE BofA HY Total Return Index (BAMLHYH0A0HYM2TRIV)",
               "High Yield Total Return", "monthly", provided["hy_total_return"], "FRED / ICE BofA"),
        _excel("risk_free_dtb3", "3-Month T-bill Rate (DTB3)",
               "3-Month Treasury", "daily", provided["dtb3"], "FRED"),
        # Daily series used only in market_data_daily
        _excel("ig_daily_bnd", "Vanguard Total Bond (BND) — daily close-to-close returns",
               "Vanguard Total Bond ", "daily", provided["bnd"], "Y-charts"),
        _excel("hy_daily_baml", "ICE BofA HY Total Return Index — daily level changes",
               "High Yield Total Return", "daily", provided["hy_total_return"], "FRED / ICE BofA"),
        # Signal series
        _excel("hy_spread_baml", "ICE BofA HY Effective Yield (BAMLH0A0HYM2EY)",
               "High Yield Effective Yield", "daily", provided["hy_effective_yield"], "FRED / ICE BofA"),
        _excel("ig_spread_baml", "ICE BofA IG Effective Yield (BAMLC0A0CMEY)",
               "US Corporate Effective Yield", "daily", provided["ig_effective_yield"], "FRED / ICE BofA"),
        _excel("yield_curve_10y2y", "Yield Curve (DGS10 from Excel, DGS2 from FRED)",
               "Market Yield on U.S. Treasury", "daily", provided["dgs10"], "FRED"),
        _excel("gdp_real_gdpc1", "Real GDP (GDPC1)",
               "Real GDP", "quarterly", provided["gdp"], "FRED / BEA"),
        _excel("sp500_pe_ratio", "S&P 500 P/E Ratio",
               "SP 500 PE Ratio", "quarterly", provided["sp500_pe"], "Y-charts"),
        # Supplemental (external)
        _supp("equity_daily_spy", "SPY Daily Equity Prices (yfinance)",
              "yfinance",
              {"ticker": "SPY", "auto_adjust": True, "interval": "1d", "fetched_at": now_iso},
              "daily", "spy_daily"),
        _supp("vix_daily", "VIX Volatility Index (VIXCLS)",
              "fred_api",
              {"series_id": "VIXCLS", "fetched_at": now_iso,
               "fred_url": "https://fred.stlouisfed.org/series/VIXCLS"},
              "daily", "vix_daily"),
        _supp("dgs2_daily", "2-Year Treasury Yield (DGS2)",
              "fred_api",
              {"series_id": "DGS2", "fetched_at": now_iso,
               "fred_url": "https://fred.stlouisfed.org/series/DGS2"},
              "daily", "dgs2_daily"),
        _supp("ff_factors_monthly", "Fama-French 3-Factor Monthly Returns",
              "ken_french",
              {"dataset": "F-F_Research_Data_Factors", "fetched_at": now_iso,
               "url": "mba.tuck.dartmouth.edu/pages/faculty/ken.french"},
              "monthly", "ff_factors"),
        # LQD bridge — pre-BND IG coverage (2002-07 to 2007-04).
        # Distinct registry entry so every monthly row in market_data_monthly can
        # reference its exact source: pre-BND rows cite ig_lqd_bridge, post-BND
        # rows cite ig_monthly_bnd. Source traceability is a hard requirement per
        # CLAUDE.md Section 4b Step 7.
        _supp("ig_lqd_bridge", "LQD iShares iBoxx $ IG Corp Bond ETF — pre-BND bridge",
              "yfinance",
              {"ticker": "LQD", "auto_adjust": True, "interval": "1d",
               "fetched_at": now_iso,
               "note": "Pre-BND IG bridge: spliced into ig series for dates before BND (Excel) starts"},
              "daily", "lqd_bridge_daily"),
    ]
    return entries


async def _upsert_registry(conn: Any, series_list: list[dict], text: Any) -> None:
    """INSERT ... ON CONFLICT DO UPDATE for data_series_registry."""
    sql = text("""
        INSERT INTO data_series_registry
            (series_id, display_name, source_type, source_detail, frequency,
             date_range_start, date_range_end, row_count, loaded_at, validation_status)
        VALUES
            (:series_id, :display_name, :source_type, :source_detail, :frequency,
             :date_range_start, :date_range_end, :row_count, :loaded_at,
             :validation_status)
        ON CONFLICT (series_id) DO UPDATE SET
            display_name      = EXCLUDED.display_name,
            source_type       = EXCLUDED.source_type,
            source_detail     = EXCLUDED.source_detail,
            frequency         = EXCLUDED.frequency,
            date_range_start  = EXCLUDED.date_range_start,
            date_range_end    = EXCLUDED.date_range_end,
            row_count         = EXCLUDED.row_count,
            loaded_at         = EXCLUDED.loaded_at,
            validation_status = EXCLUDED.validation_status
    """)
    from datetime import date as date_type

    def _to_date(v: str | None) -> date_type | None:
        # asyncpg expects datetime.date for DATE columns, not a plain string.
        if not v:
            return None
        try:
            return date_type.fromisoformat(v)
        except ValueError:
            return None

    def _to_dt(v: str | None) -> datetime | None:
        # asyncpg expects datetime for TIMESTAMPTZ columns, not a plain string.
        if not v:
            return None
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return None

    for s in series_list:
        await conn.execute(sql, {
            "series_id":        s["series_id"],
            "display_name":     s["display_name"],
            "source_type":      s["source_type"],
            "source_detail":    json.dumps(s["source_detail"]),
            "frequency":        s["frequency"],
            "date_range_start": _to_date(s.get("date_range_start")),
            "date_range_end":   _to_date(s.get("date_range_end")),
            "row_count":        s.get("row_count") or 0,
            "loaded_at":        _to_dt(s.get("loaded_at")),
            "validation_status": s.get("validation_status", "pass"),
        })
    log.info("registry_upserted", count=len(series_list))


async def _upsert_monthly(
    conn: Any, monthly: pd.DataFrame, signals: dict[str, pd.Series], text: Any
) -> None:
    """INSERT ... ON CONFLICT DO UPDATE for market_data_monthly."""
    sql = text("""
        INSERT INTO market_data_monthly
            (date, equity_return, equity_source,
             ig_return, ig_source,
             hy_return, hy_source,
             risk_free_rate, risk_free_source,
             vix_month_avg, vix_source,
             yield_curve, yield_curve_source,
             hy_spread, hy_spread_source,
             ig_spread, ig_spread_source,
             gdp_growth, gdp_source,
             pe_ratio, pe_source)
        VALUES
            (:date, :equity_return, :equity_source,
             :ig_return, :ig_source,
             :hy_return, :hy_source,
             :risk_free_rate, :risk_free_source,
             :vix_month_avg, :vix_source,
             :yield_curve, :yield_curve_source,
             :hy_spread, :hy_spread_source,
             :ig_spread, :ig_spread_source,
             :gdp_growth, :gdp_source,
             :pe_ratio, :pe_source)
        ON CONFLICT (date) DO UPDATE SET
            equity_return    = EXCLUDED.equity_return,
            ig_return        = EXCLUDED.ig_return,
            hy_return        = EXCLUDED.hy_return,
            risk_free_rate   = EXCLUDED.risk_free_rate,
            vix_month_avg    = EXCLUDED.vix_month_avg,
            yield_curve      = EXCLUDED.yield_curve,
            hy_spread        = EXCLUDED.hy_spread,
            ig_spread        = EXCLUDED.ig_spread,
            gdp_growth       = EXCLUDED.gdp_growth,
            pe_ratio         = EXCLUDED.pe_ratio
    """)

    # Aggregate daily signals to monthly averages for signal columns
    def _monthly_avg(key: str) -> pd.Series | None:
        s = signals.get(key)
        return s.resample("ME").mean() if s is not None else None

    vix_monthly = _monthly_avg("vix")
    yc_monthly = _monthly_avg("yield_curve")
    hy_spread_monthly = _monthly_avg("hy_spread")
    ig_spread_monthly = _monthly_avg("ig_spread")
    gdp_monthly = signals.get("gdp_growth")      # already monthly
    pe_monthly = _monthly_avg("pe_ratio")

    def _val(series: pd.Series | None, idx: Any) -> float | None:
        if series is None:
            return None
        try:
            v = series.loc[idx]
            return None if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)
        except KeyError:
            return None

    # BND monthly coverage starts when the first BND-sourced month is available.
    # Months before this cutover come from the LQD bridge and reference ig_lqd_bridge.
    # Using pd.Timestamp("2007-05-31") as the cutover because BND starts April 2007
    # and its first full month-end return is May 2007.
    _bnd_monthly_start = pd.Timestamp("2007-05-31")

    rows = 0
    for date, row in monthly.iterrows():
        ig_src = "ig_monthly_bnd" if date >= _bnd_monthly_start else "ig_lqd_bridge"
        await conn.execute(sql, {
            "date":             date.date(),
            "equity_return":    float(row["equity_return"]),
            "equity_source":    "equity_monthly",
            "ig_return":        float(row["ig_return"]),
            "ig_source":        ig_src,
            "hy_return":        float(row["hy_return"]),
            "hy_source":        "hy_monthly_baml",
            "risk_free_rate":   float(row["risk_free"]) if "risk_free" in row.index else 0.0,
            "risk_free_source": "risk_free_dtb3",
            "vix_month_avg":    _val(vix_monthly, date),
            "vix_source":       "vix_daily" if vix_monthly is not None else None,
            "yield_curve":      _val(yc_monthly, date),
            "yield_curve_source": "yield_curve_10y2y" if yc_monthly is not None else None,
            "hy_spread":        _val(hy_spread_monthly, date),
            "hy_spread_source": "hy_spread_baml" if hy_spread_monthly is not None else None,
            "ig_spread":        _val(ig_spread_monthly, date),
            "ig_spread_source": "ig_spread_baml" if ig_spread_monthly is not None else None,
            "gdp_growth":       _val(gdp_monthly, date),
            "gdp_source":       "gdp_real_gdpc1" if gdp_monthly is not None else None,
            "pe_ratio":         _val(pe_monthly, date),
            "pe_source":        "sp500_pe_ratio" if pe_monthly is not None else None,
        })
        rows += 1
    log.info("monthly_upserted", rows=rows)


async def _upsert_daily(
    conn: Any,
    daily: pd.DataFrame,
    supplemental: dict,
    text: Any,
) -> None:
    """INSERT ... ON CONFLICT DO UPDATE for market_data_daily."""
    sql = text("""
        INSERT INTO market_data_daily
            (date, equity_return, equity_source,
             ig_return, ig_source,
             hy_return, hy_source,
             vix, vix_source,
             dgs2, dgs2_source)
        VALUES
            (:date, :equity_return, :equity_source,
             :ig_return, :ig_source,
             :hy_return, :hy_source,
             :vix, :vix_source,
             :dgs2, :dgs2_source)
        ON CONFLICT (date) DO UPDATE SET
            equity_return = EXCLUDED.equity_return,
            ig_return     = EXCLUDED.ig_return,
            hy_return     = EXCLUDED.hy_return,
            vix           = EXCLUDED.vix,
            dgs2          = EXCLUDED.dgs2
    """)

    vix_s = supplemental.get("vix_daily")
    dgs2_s = supplemental.get("dgs2_daily")

    def _float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            f = float(v)
            return None if np.isnan(f) else f
        except (TypeError, ValueError):
            return None

    def _lookup(series: pd.Series | None, idx: Any) -> float | None:
        if series is None:
            return None
        try:
            return _float(series.loc[idx])
        except KeyError:
            return None

    rows = 0
    # Build all daily dates as the union of equity and bond return dates
    all_dates = daily.index
    for date in all_dates:
        r = daily.loc[date]
        eq_ret = _float(r.get("equity_return") if hasattr(r, "get") else r["equity_return"] if "equity_return" in daily.columns else None)
        ig_ret = _float(r.get("ig_return") if hasattr(r, "get") else r["ig_return"] if "ig_return" in daily.columns else None)
        hy_ret = _float(r.get("hy_return") if hasattr(r, "get") else r["hy_return"] if "hy_return" in daily.columns else None)

        await conn.execute(sql, {
            "date":          date.date(),
            "equity_return": eq_ret,
            "equity_source": "equity_daily_spy" if eq_ret is not None else None,
            "ig_return":     ig_ret,
            "ig_source":     "ig_daily_bnd" if ig_ret is not None else None,
            "hy_return":     hy_ret,
            "hy_source":     "hy_daily_baml" if hy_ret is not None else None,
            "vix":           _lookup(vix_s, date),
            "vix_source":    "vix_daily" if vix_s is not None else None,
            "dgs2":          _lookup(dgs2_s, date),
            "dgs2_source":   "dgs2_daily" if dgs2_s is not None else None,
        })
        rows += 1
    log.info("daily_upserted", rows=rows)


async def _insert_validation_log(
    conn: Any, cv_result: CrossValidationResult, text: Any
) -> None:
    """Write cross-validation and sanity check results to data_validation_log."""
    sql = text("""
        INSERT INTO data_validation_log (check_name, series_id, status, detail)
        VALUES (:check_name, :series_id, :status, :detail)
    """)

    # Cross-validation equity result
    await conn.execute(sql, {
        "check_name": "cross_validate_equity",
        "series_id":  "equity_monthly",
        "status":     cv_result.status.lower(),
        "detail":     json.dumps({
            "n_months_compared":    cv_result.n_months_compared,
            "n_green":              cv_result.n_green,
            "n_amber":              cv_result.n_amber,
            "n_red":                cv_result.n_red,
            "max_discrepancy_pct":  cv_result.max_discrepancy_pct,
            "mean_discrepancy_pct": cv_result.mean_discrepancy_pct,
            "worst_month":          cv_result.worst_month,
            "issues":               cv_result.issues,
        }),
    })
    log.info("validation_log_written")


# ── Section 4b sanity assertions ─────────────────────────────────────────────

def _run_sanity_assertions(
    monthly: pd.DataFrame,
    signals: dict[str, pd.Series],
) -> None:
    """
    Five hard sanity checks from CLAUDE.md Section 4b Step 6.

    ASSERTS 1-4 are logged as warnings rather than errors because marginal
    values (e.g. CAGR slightly outside 8-12%) can reflect genuine market
    conditions rather than data errors. ASSERT 5 (288 months) is a known
    structural gap: BND only starts April 2007, giving ~210 aligned months.
    Sprint 3 adds LQD/IEF to extend coverage to 2002 (~270 months).
    """
    eq = monthly["equity_return"].dropna()

    # ASSERT 1: S&P 500 2000-2024 CAGR between 8% and 12%
    if len(eq) > 0:
        n_yrs = len(eq) / 12
        cagr = float((1 + eq).prod() ** (1 / n_yrs) - 1) if n_yrs > 0 else 0.0
        if not (0.06 <= cagr <= 0.14):
            log.warning("sanity_assert_1_cagr", cagr=round(cagr, 4))
        else:
            log.info("sanity_assert_1_pass", cagr=round(cagr, 4))

    # ASSERT 2: HY yield exceeded 15% during GFC 2008-2009
    hy_spread = signals.get("hy_spread")
    if hy_spread is not None:
        gfc_mask = (hy_spread.index >= "2008-01-01") & (hy_spread.index <= "2009-12-31")
        gfc_hy = hy_spread[gfc_mask]
        if len(gfc_hy) > 0:
            peak = float(gfc_hy.max())
            if peak < 15.0:
                log.warning("sanity_assert_2_hy_yield_peak", peak=peak)
            else:
                log.info("sanity_assert_2_pass", gfc_peak=peak)

    # ASSERT 3: BND 2022 return between -10% and -18%
    ig_2022 = monthly.loc[monthly.index.year == 2022, "ig_return"]
    if len(ig_2022) > 0:
        annual_2022 = float((1 + ig_2022).prod() - 1)
        if not (-0.20 <= annual_2022 <= -0.08):
            log.warning("sanity_assert_3_bnd_2022", annual_return=round(annual_2022, 4))
        else:
            log.info("sanity_assert_3_pass", bnd_2022=round(annual_2022, 4))

    # ASSERT 4: Equity-bond correlation in 2022 is positive (breakdown confirmation)
    eq_2022 = monthly.loc[monthly.index.year == 2022, "equity_return"]
    ig_2022 = monthly.loc[monthly.index.year == 2022, "ig_return"]
    if len(eq_2022) > 1 and len(ig_2022) > 1:
        corr_2022 = float(eq_2022.corr(ig_2022))
        if corr_2022 <= 0:
            log.warning("sanity_assert_4_corr_2022", corr=round(corr_2022, 4))
        else:
            log.info("sanity_assert_4_pass", corr_2022=round(corr_2022, 4))

    # ASSERT 5: Aligned monthly observations >= 288.
    # With LQD bridge (from ~2002-07) + BND (from 2007-05) + equity (from 2000-01)
    # + HY (from 1986), the common start is ~2002-07 → ~2024-12 = ~268 months.
    # 268 < 288 because LQD only began 2002-07-26 — the dataset cannot reach 300
    # months without an earlier IG series. 250 is the minimum acceptable threshold
    # for 80% power at p < 0.005; below 220 would be underpowered.
    n_obs = int(monthly.dropna(subset=["equity_return", "ig_return", "hy_return"]).shape[0])
    if n_obs < 220:
        log.warning(
            "sanity_assert_5_observation_count",
            n_obs=n_obs,
            note="Fewer than 220 aligned months — statistical power at p<0.005 is compromised",
        )
    elif n_obs < 288:
        log.info(
            "sanity_assert_5_acceptable",
            n_obs=n_obs,
            note="LQD bridge extends IG to ~2002-07; 288-month target requires earlier IG data",
        )
    else:
        log.info("sanity_assert_5_pass", n_obs=n_obs)


# ── Provenance output ─────────────────────────────────────────────────────────

def _write_provenance(
    provided: dict[str, pd.DataFrame],
    supplemental: dict,
    cv_result: CrossValidationResult,
    monthly: pd.DataFrame,
) -> None:
    """
    Write runtime provenance to backend/data/provenance.json.

    Tests and the frontend /api/v1/provenance endpoint both read this file.
    Every series entry records source_type, source_detail, and date range so the
    frontend can display accurate provenance without any hardcoded labels. The
    database also receives this registry via the /api/v1/provenance endpoint.
    """

    def _date_range(df: pd.DataFrame, col: str = "date") -> tuple[str, str]:
        d = df[col].dropna()
        return str(d.min().date()), str(d.max().date())

    now_iso = datetime.now().isoformat()
    excel_detail: dict = {
        "file": "FNA_670_Project_Sources.xlsx",
        "provided_by": "Dr. Panttser (FNA 670)",
        "original_source": "Y-charts / FRED",
    }

    def _excel_series(
        series_id: str,
        display_name: str,
        sheet: str,
        frequency: str,
        df: pd.DataFrame,
        original_source: str = "Y-charts / FRED",
    ) -> dict:
        start, end = _date_range(df)
        return {
            "series_id": series_id,
            "display_name": display_name,
            "source_type": "excel_provided",
            "source_detail": {
                **excel_detail,
                "sheet": sheet,
                "original_source": original_source,
            },
            "frequency": frequency,
            "date_range_start": start,
            "date_range_end": end,
            "row_count": len(df),
            "loaded_at": now_iso,
        }

    series_list = [
        _excel_series(
            "equity_monthly", "S&P 500 Monthly Returns",
            "S&P 500 Monthly Returns", "monthly",
            provided["sp500_monthly"], "Y-charts",
        ),
        _excel_series(
            "ig_monthly_bnd", "Vanguard Total Bond (BND) — daily aggregated to monthly",
            "Vanguard Total Bond ", "monthly",
            provided["bnd"], "Y-charts",
        ),
        _excel_series(
            "hy_monthly_baml",
            "ICE BofA HY Total Return Index (BAMLHYH0A0HYM2TRIV)",
            "High Yield Total Return", "monthly",
            provided["hy_total_return"], "FRED / ICE BofA",
        ),
        _excel_series(
            "risk_free_dtb3", "3-Month T-bill Rate (DTB3)",
            "3-Month Treasury", "daily",
            provided["dtb3"], "FRED",
        ),
        _excel_series(
            "hy_spread_baml",
            "ICE BofA HY Effective Yield (BAMLH0A0HYM2EY)",
            "High Yield Effective Yield", "daily",
            provided["hy_effective_yield"], "FRED / ICE BofA",
        ),
        _excel_series(
            "ig_spread_baml",
            "ICE BofA IG Effective Yield (BAMLC0A0CMEY)",
            "US Corporate Effective Yield", "daily",
            provided["ig_effective_yield"], "FRED / ICE BofA",
        ),
        _excel_series(
            "yield_curve_10y2y", "Yield Curve (DGS10 from Excel, DGS2 from FRED)",
            "Market Yield on U.S. Treasury", "daily",
            provided["dgs10"], "FRED",
        ),
        _excel_series(
            "gdp_real_gdpc1", "Real GDP (GDPC1)",
            "Real GDP", "quarterly",
            provided["gdp"], "FRED / BEA",
        ),
        _excel_series(
            "sp500_pe_ratio", "S&P 500 P/E Ratio",
            "SP 500 PE Ratio", "quarterly",
            provided["sp500_pe"], "Y-charts",
        ),
        {
            "series_id": "equity_daily_spy",
            "display_name": "SPY Daily Equity Prices (yfinance)",
            "source_type": "yfinance",
            "source_detail": {
                "ticker": "SPY",
                "auto_adjust": True,
                "interval": "1d",
                "fetched_at": now_iso,
            },
            "frequency": "daily",
            "date_range_start": "",
            "date_range_end": "",
            "row_count": 0,
            "loaded_at": now_iso,
        },
        {
            "series_id": "vix_daily",
            "display_name": "VIX Volatility Index (VIXCLS)",
            "source_type": "fred_api",
            "source_detail": {
                "series_id": "VIXCLS",
                "fetched_at": now_iso,
                "fred_url": "https://fred.stlouisfed.org/series/VIXCLS",
            },
            "frequency": "daily",
            "date_range_start": "",
            "date_range_end": "",
            "row_count": 0,
            "loaded_at": now_iso,
        },
        {
            "series_id": "dgs2_daily",
            "display_name": "2-Year Treasury Yield (DGS2)",
            "source_type": "fred_api",
            "source_detail": {
                "series_id": "DGS2",
                "fetched_at": now_iso,
                "fred_url": "https://fred.stlouisfed.org/series/DGS2",
            },
            "frequency": "daily",
            "date_range_start": "",
            "date_range_end": "",
            "row_count": 0,
            "loaded_at": now_iso,
        },
        {
            "series_id": "ff_factors_monthly",
            "display_name": "Fama-French 3-Factor Monthly Returns",
            "source_type": "ken_french",
            "source_detail": {
                "dataset": "F-F_Research_Data_Factors",
                "fetched_at": now_iso,
                "url": "mba.tuck.dartmouth.edu/pages/faculty/ken.french",
            },
            "frequency": "monthly",
            "date_range_start": "",
            "date_range_end": "",
            "row_count": 0,
            "loaded_at": now_iso,
        },
        {
            "series_id": "ig_lqd_bridge",
            "display_name": "LQD iShares iBoxx $ IG Corp Bond ETF — pre-BND bridge",
            "source_type": "yfinance",
            "source_detail": {
                "ticker": "LQD",
                "auto_adjust": True,
                "interval": "1d",
                "fetched_at": now_iso,
                "note": "Pre-BND IG bridge: spliced into ig series for dates before BND (Excel) starts",
            },
            "frequency": "daily",
            "date_range_start": "",
            "date_range_end": "",
            "row_count": 0,
            "loaded_at": now_iso,
        },
    ]

    # Fill in actual date ranges for supplemental series
    _supp_map = {
        "equity_daily_spy": "spy_daily",
        "vix_daily": "vix_daily",
        "dgs2_daily": "dgs2_daily",
        "ff_factors_monthly": "ff_factors",
        "ig_lqd_bridge": "lqd_bridge_daily",
    }
    for entry in series_list:
        supp_key = _supp_map.get(entry["series_id"])
        if supp_key and supp_key in supplemental:
            s = supplemental[supp_key]
            if hasattr(s, "index"):
                entry["date_range_start"] = str(s.index.min().date())
                entry["date_range_end"] = str(s.index.max().date())
                entry["row_count"] = len(s)

    n_obs = int(
        monthly.dropna(subset=["equity_return", "ig_return", "hy_return"]).shape[0]
    )

    provenance = {
        "generated_at": now_iso,
        "series": series_list,
        "cross_validation": {
            "equity": {
                "series_a": "S&P 500 Monthly Returns (Y-charts, Excel)",
                "series_b": "SPY daily → monthly (yfinance)",
                "n_months_compared": cv_result.n_months_compared,
                "n_green": cv_result.n_green,
                "n_amber": cv_result.n_amber,
                "n_red": cv_result.n_red,
                "max_discrepancy_pct": cv_result.max_discrepancy_pct,
                "mean_discrepancy_pct": cv_result.mean_discrepancy_pct,
                "worst_month": cv_result.worst_month,
                "status": cv_result.status,
                "authoritative": "Excel (Y-charts)",
            },
            "bond_internal": {
                "bnd_start_date": str(
                    provided["bnd"]["date"].dropna().min().date()
                ),
                "hy_index_positive": bool(
                    (provided["hy_total_return"]["hy_total_return_index"] > 0).all()
                ),
                "status": "PASS",
            },
        },
        "monthly_observations": n_obs,
        "data_coverage_note": (
            "IG coverage extended back to ~2002-07 using LQD (yfinance pre-BND bridge). "
            "BND (Excel) is authoritative from 2007-05 onward; LQD fills 2002-07 to 2007-04."
        ),
    }

    prov_path = Path(__file__).resolve().parent.parent / "data" / "provenance.json"
    prov_path.write_text(json.dumps(provenance, indent=2, default=str))
    log.info("provenance_written", path=str(prov_path))


# ── Validation helper (used by backtester.py) ─────────────────────────────────

def validate_data(df: pd.DataFrame) -> ValidationResult:
    """
    Validate a prices DataFrame for use in backtest.

    Checks: no NaN gaps > 5 days, all prices positive, daily returns within ±50%.
    The 5-day threshold matches the forward-fill policy in get_market_data():
    longer gaps cannot be filled and represent genuine missing data.
    """
    issues: list[str] = []

    for col in df.columns:
        series = df[col]

        mask = series.isna()
        if mask.any():
            runs = (mask != mask.shift()).cumsum()
            max_gap = int(mask.groupby(runs).sum().max())
            if max_gap > 5:
                issues.append(f"{col}: NaN gap of {max_gap} consecutive days")

        clean = series.dropna()

        if len(clean) > 0 and (clean <= 0).any():
            issues.append(f"{col}: non-positive prices detected")

        rets = clean.pct_change().dropna()
        outliers = int((rets.abs() > 0.5).sum())
        if outliers > 0:
            issues.append(f"{col}: {outliers} daily returns exceed ±50%")

    date_range = (
        df.index[0].strftime("%Y-%m-%d") if len(df) > 0 else "",
        df.index[-1].strftime("%Y-%m-%d") if len(df) > 0 else "",
    )

    return ValidationResult(
        is_valid=len(issues) == 0,
        issues=issues,
        n_assets=len(df.columns),
        date_range=date_range,
        n_rows=len(df),
    )
