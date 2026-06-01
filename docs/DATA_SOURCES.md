# Data sources — provenance + traps

A short, authoritative reference for where each return series in the
analytics pipeline comes from, and the gotchas a future contributor
must know before touching a fetcher.

The full per-series registry is in PostgreSQL (`data_series_registry`)
and surfaced at `GET /api/v1/provenance`. This document covers the
**non-obvious** sourcing decisions and the warnings a contributor
needs to read before adding or replacing a data source.

## HY total return (BAMLHYH0A0HYM2TRIV) — DO NOT replace from FRED

HY total return (BAMLHYH0A0HYM2TRIV) is sourced from the Excel file,
which holds the full ICE BofA history to **August 1986**. The FRED
public series was re-baselined to **2023-05-30** and must NOT be used
as a replacement or update source.

What this means in practice:

- `backend/tools/data_fetcher.py` reads the HY series from
  `FNA_670_Project_Sources.xlsx` sheet `hy_total_return`. **Never
  re-fetch from FRED** — FRED's currently-published version of the
  series begins 2023-05-30 and would silently truncate ~37 years of
  history if substituted.
- The HY auto-extension path (post-2025 monthly increments) uses HYG
  via yfinance — a tradeable proxy with documented tracking error
  against the index, NOT a FRED refresh. See
  `data_fetcher.extend_market_data` and CLAUDE.md's SOURCE CHANGE
  note for the HYG splice.
- The FRED metadata API returns `observation_start=2023-05-30` for
  every ICE BofA total-return series (HY, IG sub-indices) as of
  May 2026. We do not know whether this is a vendor-distribution
  shift or a re-baselining — only that the data we already hold is
  authoritatively the deeper version.

If a future contributor sees the FRED series and thinks "we should
sync from there for freshness" — STOP. Read this section first.

## IG total return — the binding constraint at 2002-07-26

The three-asset analytical window (Equity / IG / HY) starts
**2002-07-26**, anchored by the LQD ETF launch date. There is no
IG total-return source in the currently-fetchable universe with
comparable depth to the Excel HY data (which goes back to 1986).
Vanguard Total Bond (BND) in the Excel file starts 2007-04-10;
LQD bridges 2002-07-26 → 2007-04 via the yfinance `lqd_bridge`
splice.

The bootstrap-CI overlap finding (PR #247) addresses the resulting
sample-size limitation directly in the analytical narrative — see
the limitation copy on `analytics.bootstrap_ci_table` and finding
`BOOTSTRAP CI OVERLAP` in `analytical_findings.py`.

## Post-July optional extensions

Items closed as out-of-scope for the July 1 submission but worth
considering for a follow-up:

- **HY-only robustness appendix (1986-2002).** Extend the HY +
  equity (two-asset) analysis to 1986-2002 as a robustness check
  in the brief's limitations section. ~190 months of additional
  HY data. Requires a parallel two-asset specification for every
  IG-dependent strategy (RISK_PARITY, MIN_VARIANCE, BLACK_LITTERMAN,
  MAX_SHARPE_ROLLING, EQUAL_WEIGHT) OR their exclusion from the
  pre-2002 stub. Investigated June 1 2026; the conclusion was that
  the methodological surface area outweighs the sample-size gain
  before July 1. Reopen post-deadline if a longer-window
  robustness narrative is wanted in a follow-up paper.
