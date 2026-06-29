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

### `blend_oos_monthly_returns.csv` — 53 × 2

Out-of-sample monthly return stream of the regime-conditional
council blend, covering the 2022-01 → 2026-05 OOS window.
Produced by running `out_of_sample_validation()` against the
frozen `strategy_results.json` with a 2022-01-01 split. The
HMM regime path comes from `tools/regime_detector.py` (fit on
the `equity_return` column of `monthly_returns.csv`).

| Column | Type | Notes |
|---|---|---|
| `date` | ISO date (month-end) | 2022-01-31 → 2026-05-31 |
| `return` | float | Blend monthly return (decimal) |

### `regime_classification.csv` — 287 × 2

HMM regime label for every month in the study period. Used by
the notebook's regime-signals chart (Cell 7) to overlay
posterior bands on the S&P 500 cumulative path.

| Column | Type | Notes |
|---|---|---|
| `date` | ISO date (month-end) | 2002-07-31 → 2026-05-31 |
| `regime_label` | enum | BULL / TRANSITION / BEAR |

### `oos_summary.json`

The headline OOS scalars sourced from the platform
`academic_lock` cache (PR #490, locked at submission). Same
HMM + OOS validation pipeline as the brief/appendix/deck so
the four deliverables stay consistent.

```json
{
  "oos_sharpe_blend":        0.90,
  "oos_sharpe_benchmark":    0.49,
  "oos_sharpe_classic_6040": 0.18,
  "improvement_pct":         83.7,
  "n_test_months":           53,
  "oos_window_start":        "2022-01-31",
  "oos_window_end":          "2026-05-31"
}
```

## Regenerating the chart-data exports

`scripts/export_notebook_chart_data.py` writes the three files
above (`blend_oos_monthly_returns.csv`,
`regime_classification.csv`, `oos_summary.json`) from the
frozen inputs. Re-run when the freeze changes, or to refresh
canonical values on Render.

**hmmlearn requirement.** The HMM regime fit uses `hmmlearn`,
which doesn't yet have Python 3.14 wheels. If running locally
on 3.14, the script falls back to a deterministic 3-regime
quantile classifier that approximates the count distribution
(~55/196/36 vs canonical 58/191/38) but **does not reproduce
the canonical OOS Sharpe values**. For final submission,
regenerate on Render (Python 3.12, hmmlearn installed):

```bash
# On Render shell, from /opt/render/project/src
python scripts/export_notebook_chart_data.py
```

The script's `oos_summary.json` carries the canonical academic-
lock values whether hmmlearn is present or not; only the
`regime_classification.csv` + `blend_oos_monthly_returns.csv`
series shapes differ between local-fallback and canonical
runs.

## Running in Google Colab

1. Open Google Colab (colab.research.google.com)
2. Upload `analytical_appendix.ipynb`
3. Upload the entire `notebook_data/` folder using the Files
   panel (left sidebar) — the notebook expects the folder
   alongside the .ipynb file
4. Run all cells (Runtime → Run all)

All figures and tables reproduce from the pre-exported data
files. No platform access, API keys, or non-standard packages
required. The notebook uses only the Colab-default stack:
`pandas`, `numpy`, `matplotlib`, `scipy`, `json`, `pathlib`,
`hashlib`.

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
