"""
Market data fetcher — yfinance (equities/bonds) + FRED (macro).
Parquet caching with 24-hour expiry.
All prices are TOTAL RETURN (adjusted close, auto_adjust=True).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    CACHE_DIR,
    CACHE_EXPIRY_HOURS,
    RISK_FREE_RATE_FALLBACK,
    FRED_SERIES,
    BENCHMARK,
    EQUITIES,
    SECTORS,
    FIXED_INCOME,
    ALTERNATIVES,
)
from logger import get_logger

log = get_logger(__name__)

_CACHE_PATH = Path(CACHE_DIR)
_CACHE_PATH.mkdir(parents=True, exist_ok=True)


# ── ValidationResult ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid: bool
    issues: list[str] = field(default_factory=list)
    n_assets: int = 0
    date_range: tuple[str, str] = ("", "")
    n_rows: int = 0


# ── Cache helpers ─────────────────────────────────────────────────────────────

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


# ── yfinance fetch ────────────────────────────────────────────────────────────

def _yfinance_fetch(tickers: list[str], start: str, end: str) -> pd.DataFrame:
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

    # Normalise to a simple DataFrame with ticker columns
    if isinstance(raw.columns, pd.MultiIndex):
        df = raw["Close"]
    else:
        # Single ticker — yfinance returns flat columns
        df = raw[["Close"]].copy()
        df.columns = [tickers[0]]

    df = df.dropna(how="all")
    df.attrs["adjusted"] = True
    log.info(
        "yfinance_fetch_complete",
        tickers=tickers,
        rows=len(df),
        start=start,
        end=end,
    )
    return df


# ── Public fetch functions ────────────────────────────────────────────────────

def fetch_equity_data(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Total-return equity prices (adjusted close). Cached."""
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


def fetch_bond_data(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """Total-return bond ETF prices (adjusted close). Cached."""
    return fetch_equity_data(tickers, start, end)


def _fred_fetch(series_id: str, start: str, end: str) -> pd.DataFrame:
    """Internal FRED fetch — wraps pandas_datareader so tests can patch it."""
    import pandas_datareader.data as web

    log.info("fred_fetch", series_id=series_id, start=start, end=end)
    df = web.DataReader(series_id, "fred", start, end)
    if df.empty:
        raise ValueError(f"FRED returned no data for {series_id}")
    log.info("fred_fetch_complete", series_id=series_id, rows=len(df))
    return df


def fetch_fred_series(series_id: str, start: str, end: str) -> pd.Series:
    """Fetch a FRED data series via pandas_datareader. Cached."""
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
    Converts to daily decimal. Falls back to RISK_FREE_RATE_FALLBACK / 252
    if FRED is unavailable.
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


def get_market_data(tickers: list[str], start: str, end: str) -> dict:
    """
    Orchestrate equity and bond fetches.
    Returns {"prices": DataFrame, "returns": DataFrame}.
    """
    all_equity = set(EQUITIES + SECTORS + [BENCHMARK] + ALTERNATIVES)
    all_bond = set(FIXED_INCOME)

    eq_tickers = [t for t in tickers if t in all_equity]
    bond_tickers = [t for t in tickers if t in all_bond]
    other_tickers = [t for t in tickers if t not in all_equity and t not in all_bond]

    frames: list[pd.DataFrame] = []
    if eq_tickers:
        frames.append(fetch_equity_data(eq_tickers, start, end))
    if bond_tickers:
        frames.append(fetch_bond_data(bond_tickers, start, end))
    if other_tickers:
        frames.append(fetch_equity_data(other_tickers, start, end))

    if not frames:
        return {"prices": pd.DataFrame(), "returns": pd.DataFrame()}

    prices = pd.concat(frames, axis=1).sort_index()
    prices = prices.ffill(limit=5)
    returns = prices.pct_change().dropna(how="all")
    prices.attrs["adjusted"] = True

    return {"prices": prices, "returns": returns}


# ── Validation ────────────────────────────────────────────────────────────────

def validate_data(df: pd.DataFrame) -> ValidationResult:
    """
    Validate a prices DataFrame.
    Checks: no NaN gaps > 5 days, positive prices, returns within ±50%.
    """
    issues: list[str] = []

    for col in df.columns:
        series = df[col]

        # NaN gap check
        mask = series.isna()
        if mask.any():
            # Count maximum run of consecutive NaNs
            runs = (mask != mask.shift()).cumsum()
            max_gap = int(mask.groupby(runs).sum().max())
            if max_gap > 5:
                issues.append(f"{col}: NaN gap of {max_gap} consecutive days")

        clean = series.dropna()

        # Positive prices
        if len(clean) > 0 and (clean <= 0).any():
            issues.append(f"{col}: non-positive prices detected")

        # Return outliers
        rets = clean.pct_change().dropna()
        outliers = int((rets.abs() > 0.5).sum())
        if outliers > 0:
            issues.append(f"{col}: {outliers} daily returns exceed ±50% (outlier flag)")

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
