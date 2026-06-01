# Analytics Invariant Framework

A permanent quality gate that runs at the end of every analytics warm.
Module: `backend/tools/invariant_checks.py`. Wired into
`tools/cache.set_strategy_cache` so a hard-tier failure preserves the
previous cache row rather than overwriting it with bad data.

## Architecture constraint — deterministic detection only

Every assertion is a PURE MATHEMATICAL COMPARISON. No LLM is
involved in any detection path. The seven rules a contributor MUST
honor before adding a new check:

1. Pure comparison only — `abs(x) <= threshold`, `lo <= x <= hi`,
   `abs(computed - displayed) < tolerance`. No interpretation.
2. Expected ranges live as hardcoded module-level constants
   reviewed by a human (`_BENCHMARK_CRISIS_PLAUSIBILITY`,
   `_MACRO_PLAUSIBILITY` in `invariant_checks.py`). Never generated
   at runtime.
3. Layer 4 fixture expected values are computed inline in the test
   file with basic arithmetic (`(1+r1)*(1+r2)-1`). Never call a
   platform helper for the expected side.
4. Hard failure = deterministic boolean. No probabilities or LLM
   verdicts.
5. Logging uses static f-string templates with values substituted.
6. Soft warnings use the same deterministic comparison; only
   `severity` differs.
7. Any randomness in fixtures uses a fixed seed.

The 30-second readability test: a junior analyst should be able to
verify each check's logic by reading 2-3 lines. The validation
layer is the thing we trust when everything else is wrong.

## Motivating example — F3

The F3 incident (May 30 2026) surfaced a 6× annualisation amplification
on a 2-month crisis window: a benchmark cumulative loss of -19.87%
was displayed as -73.53% CAGR. The bug slipped past every prior check:
Layer 1 deterministic-Python sanity, Layer 2 LLM recomputation, the QA
methodology audit. Each was busy verifying that the computation was
correct in isolation; none of them caught that the displayed FIELD
was wrong.

The invariant framework catches this class of bug at warm time —
specifically at assertions **1a**, **1h**, and **2a**. Each assertion
documents the F3 example so a future reader sees what it was built to
catch.

## Severity tiers

- **HARD** — abort the warm; preserve the previous cache; log
  `invariant_hard_failure` per assertion plus the
  `invariant_check_summary` line.
- **SOFT** — log `invariant_soft_warning`; do not block the warm.

## Categories

### Category 1 — Mathematical Impossibilities (HARD)

| Code | What it checks | Why it matters | Catches |
|------|---------------|----------------|---------|
| 1a | Crisis-window cumulative loss ≤ full-period max DD (per strategy) | A loss inside the period cannot exceed the worst-ever loss across the full period | F3: COVID Crash -73.53% > full-period -52.56% |
| 1b | Stored Sharpe ≈ `mean(excess) / std(excess, ddof=1) * sqrt(12)` within 0.02, using the actual DTB3 rf series the backtester used | The headline metric must be reconstructible via the backtester's exact monthly-arithmetic formula | Component drift / wrong rf application. SKIPPED when no rf series is supplied to the runner (rf=0 fallback produced false positives that blocked every cache write on May 31 2026). |
| 1c | Max DD ≤ worst single monthly return | DD contains at least the worst month | Bad DD computation or stale field |
| 1d | CVaR99 ≤ CVaR95 | The 1% tail is a subset of the 5% tail | CVaR swap / wrong quantile direction |
| 1e | Every weight schedule sums to 1 ± 0.001 | Fully-invested constraint | Bad rebalance, unnormalised weights |
| 1f | Correlation matrix is PSD (min eigenvalue ≥ -0.001) | A genuine correlation matrix must be PSD | Misaligned series, NaN-induced bad matrix |
| 1g | Every monthly return in (-100%, +200%] | No unintended leverage or data error | Bad scaling / units |
| 1h | Full-period max DD ≤ any crisis cumulative loss | Crisis is a subset of full period; full-period DD must be at least as bad | F3 (subset framing) |

### Category 2 — Time Basis Consistency (HARD)

| Code | What it checks | Why it matters | Catches |
|------|---------------|----------------|---------|
| 2a | Crisis-window `cumulative_return` matches a fresh recompute from the monthly series within 0.5% | The crisis table headline must be the cumulative basis | F3 directly |
| 2b | Stored Sharpe matches an annualised (sqrt(12)) recompute, not a monthly Sharpe | Full-period Sharpes must be annualised | Missed sqrt(12) factor |
| 2c | Factor-loadings rows use ≤ 4 distinct estimation windows | Comparisons across rows must be apples-to-apples | Mixed-window factor table |
| 2d | Stored CAGR matches a fresh recompute from monthly returns within 0.5% | A metric appearing in storage and in a derived view must reconcile | Cross-table basis drift |

### Category 3 — External Reference Checks (SOFT)

- **Benchmark crisis returns** must fall within published S&P 500 price-return ranges:
  - GFC 2008-2009: [-50%, -38%]
  - EU Debt 2011: [-10%, -2%]
  - COVID Crash: [-25%, -15%]
  - COVID Recovery: [+70%, +95%]
  - Rate Shock 2022: [-22%, -16%]
- **Macro series** stay within plausibility bands:
  - DGS10 (10Y yield): 0.5% – 8.0%
  - VIX: 9 – 85
  - HY OAS (BAMLH0A0HYM2): 200 – 2500 bps
  - DTB3 (T-bill): 0% – 6%

### Category 4 — Directional Logic (SOFT)

| Code | What it checks |
|------|---------------|
| 4a | VOL_TARGETING beats BENCHMARK in every loss window |
| 4b | Higher Sharpe correlates with less-negative CVaR across strategies |
| 4c | Tangency portfolio Sharpe ≥ best individual-strategy Sharpe |
| 4d | Bootstrap 95% CI brackets the point estimate |
| 4e | Defensive strategies (MIN_VARIANCE, RISK_PARITY, VOL_TARGETING) outperform benchmark on post-2022 Sharpe |

### Category 5 — Temporal Integrity (HARD on gaps / SOFT on ordering)

| Code | Severity | What it checks |
|------|----------|----------------|
| 5a | HARD | No gaps in any strategy's monthly series between its first and last observation |
| 5b | HARD | Lookback-windowed strategies produce no results before their initialisation completes |
| 5d | SOFT | Every crisis window's [start, end] sits within the BENCHMARK data range |
| 5e | SOFT | OOS split sits ≥ 36 months after data start |

(5c — "no future-data leakage" — is enforced by the backtester
construction itself; the framework does not duplicate that check.)

## How a failure surfaces

- **Per assertion**: a `invariant_hard_failure` or
  `invariant_soft_warning` log line carries `code`, `severity`,
  `category`, `entity`, `metric`, `expected`, `actual`, `detail`.
- **Aggregate**: an `invariant_check_summary` log line carries
  `hard_failures`, `soft_warnings`, `checks_passed`, `total_checks`.
- **Admin surface**: `GET /api/v1/admin/invariants` returns the last
  run's full result, including the full violation list. Settings →
  Data and Study Period renders the summary card.

## Adding a new assertion

1. Add a `check_*` function in `tools/invariant_checks.py` with the
   same `(violations, n_checks_run)` signature as the existing
   functions.
2. Append it to the `suites` list in `run_all_invariants`.
3. Add a row to this document under the right category.
4. Add a positive-and-negative test case in
   `tests/test_invariant_checks.py`.
