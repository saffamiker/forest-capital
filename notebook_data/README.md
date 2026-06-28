# Notebook Data Freeze — Forest Capital Analytical Appendix

This directory contains the immutable data snapshot the
companion Jupyter notebook (`../analytical_appendix.ipynb`)
reads from. The freeze is permanent — graders run the notebook
against these files, not against the live system. No database,
no API, no live data fetcher: the notebook stands alone.

## Freeze identity

| Field | Value |
|---|---|
| `strategy_hash` (canonical) | **`f2e87dec7dcabe71`** |
| `n_rows` | 287 |
| `last_date` | 2026-05-31 |
| `n_strategies` | 10 |
| `study_period` | 2002-07-31 → 2026-05-31 (287 monthly observations) |
| Freeze commit date | 2026-06-21 |

The strategy hash is computed by the system as
`sha256(f"{n_rows}:{last_date}:{n_strategies}").hexdigest()[:16]`
and must match between this freeze, the Executive Brief, and
the DOCX Analytical Appendix.

The notebook's manifest cell (Cell 3) asserts this hash equals
`f2e87dec7dcabe71` and halts with a clear error if the freeze
has been edited.

## Files

### `monthly_returns.csv` — 287 × 4

Raw monthly returns for the three asset classes the universe is
built from. Decimals (not percentages).

| Column | Type | Notes |
|---|---|---|
| `date` | ISO date (month-end) | 2002-07-31 → 2026-05-31 |
| `equity_return` | float | S&P 500 total return |
| `ig_return` | float | Investment-grade bonds (Vanguard BND from Sep 2007; AGG-equivalent splice prior) |
| `hy_return` | float | High yield (BAMLHYH0A0HYM2TRIV ICE BofA HY index; HYG splice for the early window) |

### `ff_factors.csv` — 1,198 × 5

Fama-French 3-factor monthly series from the Kenneth French
data library, plus the risk-free rate. **In percent units**
(divide by 100 before joining with the decimal returns in
`monthly_returns.csv`).

| Column | Type | Notes |
|---|---|---|
| `yyyymm` | int (YYYYMM) | 192607 → 202604 |
| `mkt_rf` | float (%) | Market excess return |
| `smb` | float (%) | Small-minus-big |
| `hml` | float (%) | High-minus-low |
| `rf` | float (%) | 1-month T-bill |

**Coverage limitation:** the freeze ends 2026-04 while the
returns file ends 2026-05 — 286 overlap months, not 287. The
factor regression cell drops the last month of returns. The
freeze does **not** carry a momentum (UMD/MOM) factor; the
notebook runs a 3-factor regression rather than the Carhart
4-factor model, and documents this constraint at cell 5.

### `rebalance_events.csv` — 9 × 7

Nine council rebalance events from 2023-03 → 2025-04, each
triggered by a regime detection or news-driven posterior
shift. Used by the AI Usage section to compare the council's
verdict against the realised 30/60/90-day outcome.

| Column | Type | Notes |
|---|---|---|
| `event_date` | ISO date | Month-end of the rebalance |
| `trigger` | text | One-sentence description of what fired the council |
| `regime` | enum | BULL / BEAR / TRANSITION |
| `posterior` | JSON str | `{"bear": p, "bull": p, "transition": p}` posterior |
| `blend_weights` | JSON str | Per-strategy weights immediately after the rebalance |
| `performance` | JSON str | Realised d30/d60/d90 returns for blend / benchmark / classic_6040 |
| `verdict` | text | Generated 90-day value-add narrative ("council added value" or "council did not add value") |

### `strategy_results.json` — 10 strategies × 32-59 fields each

Full backtester output for every strategy in the universe.
Each strategy carries its own `monthly_returns` list
(`[[date_str, return_float], ...]`), a complete metrics block
(sharpe, sortino, calmar, cagr, max_drawdown, volatility,
alpha, beta, etc.), all eight Tier-1 significance test outputs
(t-test p, FDR-corrected p, deflated Sharpe p, OOS p, CV
stability, SPA p, probabilistic Sharpe, bootstrap CI), and a
`weight_schedule` showing the rebalancing trajectory.

The brief's headline figures map to the JSON as:

| Brief claim | Source |
|---|---|
| OOS Sharpe 0.63 (blend) | `REGIME_SWITCHING.sharpe_ratio = 0.6291` |
| OOS Sharpe 0.54 (benchmark) | `BENCHMARK.sharpe_ratio = 0.537` |
| Max drawdown -29.7% (blend) | `REGIME_SWITCHING.max_drawdown = -0.2974` |
| Max drawdown -52.6% (benchmark) | `BENCHMARK.max_drawdown = -0.5256` |
| Recovery 32 months (blend) | `REGIME_SWITCHING.drawdown_recovery_days = 671 / 21 = 31.95` |
| Recovery 71 months (benchmark) | `BENCHMARK.drawdown_recovery_days = 1492 / 21 = 71.05` |

**Recovery-months convention:** the brief expresses recovery in
trading-day-months (`days / 21`), not calendar months. The
notebook's recovery cell computes both and shows the
reconciliation explicitly.

## Reproducing the notebook

The notebook runs on a standard Python scientific stack — no
proprietary libraries, no network access required.

```bash
# 1. Create a clean environment
python -m venv venv
source venv/bin/activate          # Linux/Mac
# venv\Scripts\activate            # Windows

# 2. Install dependencies (versions pinned in the notebook's
#    first code cell; this is the minimum set)
pip install pandas numpy scipy matplotlib jupyter

# 3. Run the notebook end-to-end
jupyter nbconvert --execute --to notebook --inplace \
  ../analytical_appendix.ipynb
```

If any cell raises (other than a clearly-labelled scope-
constraint warning), the freeze and the notebook have drifted
apart — check that `monthly_returns.csv` still has 287 rows
ending 2026-05-31 and the strategy hash assertion in Cell 3
still passes.

## Provenance and integrity

The freeze was generated from the production strategy cache
at commit `5a49169` on 2026-06-21 and corresponds to the
data state used to generate the Executive Brief and DOCX
Analytical Appendix submitted alongside this deliverable.
Edits to any file in this directory invalidate the freeze
and the notebook's manifest assertion will fail.
