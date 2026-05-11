# Forest Capital — Test Manifest

## Sprint 1 ✅ COMPLETE
Backend:
  test_config.py         — all constants correct types and values
  test_auth.py           — magic link generate, expire, single-use, email enumeration protection
  test_mock_data.py      — 10 strategies present, valid schemas, required fields
  test_health.py         — /api/health returns 200, all fields present and typed,
                           protected endpoints require auth, master key grants access
Frontend:
  LoginPage.test.tsx     — renders, email validation, send-link flow, confirmation state
  BrandContext.test.tsx  — MCCOLL default, toggle to FOREST_CAPITAL and back
  Dashboard.test.tsx     — renders with mock data, 10 strategies, regime indicator
  StrategyCard.test.tsx  — metrics display, significance badge, expand/collapse
E2E:
  login.spec.ts          — full login flow, confirmation message
  navigation.spec.ts     — all tabs navigate without error

## Sprint 2 Remediation ✅ COMPLETE (2026-05-11)
Rebuilt data layer to match full CLAUDE.md spec.

Backend — new/updated:
  test_data_loader.py    — load_provided_data() returns internal normalized keys,
                           all sheets load as DataFrames, dates are datetime,
                           HY Total Return has >5000 rows, S&P500 contains price
                           levels not returns, build_monthly_returns() columns,
                           no NaN after alignment, index is month-end,
                           serial date conversion assertions (45839→2025-07,
                           36529→2000-01-04 documented as correct serial)
  test_supplemental_fetcher.py — fetch_supplemental_data() returns dict with
                           spy_daily/vix_daily/dgs2_daily/ff_factors keys,
                           spy_daily is returns not prices, yfinance only called
                           for SPY (BND/HYG never fetched from yfinance),
                           FF factors are in decimal form (divided by 100)
  test_cross_validation.py — CrossValidationResult dataclass, cross_validate_equity()
                           returns CrossValidationResult with matched data,
                           status in valid set, no red months when data matches,
                           DataValidationError importable
  test_data_provenance.py — MANDATORY CLAUDE.md test: excel_provided series have
                           correct file/sheet, yfinance only for SPY ticker,
                           VIXCLS+DGS2 in fred_api, DGS10 NOT in fred_api;
                           provenance.json structure tests; API endpoint tests
  test_provenance_api.py — GET /api/v1/provenance returns 200 with/without file,
                           returns series list, no auth required, required fields
  test_database_writes.py — data_series_registry ≥13 rows; BND/BAMLHYH are
                           excel_provided, SPY is yfinance, VIX/DGS2 are fred_api,
                           DGS10 NOT in fred_api; market_data_monthly ≥100 rows
                           with source columns populated; market_data_daily ≥1000
                           rows with correct source columns; data_validation_log
                           has cross_validate_equity entry; idempotency confirmed
                           (skips automatically when Postgres is not reachable)
Backend — existing (unchanged):
  test_data_fetcher.py   — all 15 tests passing (mocked yfinance/FRED wrappers)
  test_risk_metrics.py   — all 21 tests passing
  test_backtester.py     — all 10 tests passing
  test_statistical_tests.py — all 32 tests passing
  test_config.py, test_auth.py, test_mock_data.py, test_health.py — unchanged
Frontend — new:
  provenanceStore.ts created (Zustand store for /api/v1/provenance)
  provenance.ts types created (TypeScript interfaces, CHART_PROVENANCE_REGISTRY)
  47 existing frontend tests continue to pass
Infrastructure:
  database.py — async SQLAlchemy engine, conditional on DATABASE_URL
  migrations/versions/001_create_data_tables.py — 4 tables in dependency order
  GET /api/v1/provenance endpoint added to main.py
  GET /api/backtest/compare fixed to use real BENCHMARK result
  .github/workflows/test.yml — E2E job timeout increased to 15min,
    wait-on timeout doubled to 120s, log capture on failure added
    (continue-on-error: true remains until issue #1 is resolved)

## Sprint 3 ✅ COMPLETE (2026-05-11)

Backend — new/updated:
  test_optimizer.py      — optimize_weights dispatcher (6 methods), parametrised weight
                           constraint checks (sum=1±1e-4, within [MIN, MAX], non-negative),
                           method-specific properties (MV concentration, RP equal risk,
                           MV vol < equal-weight, BL equilibrium prior, max-Sharpe fallback,
                           min-drawdown valid), efficient frontier (list, keys, vol ordering)
  test_cross_validation.py (Sprint 3 additions) — walk_forward_cv dict structure, fold keys
                           (oos_sharpe, n_test_obs), expanding_window_cv divergence metric,
                           purged_kfold_cv (embargo gap, fold structure), cpcv paths
                           (C(6,2)=15 paths, CI tuple, pct_positive in [0,1]),
                           permutation_test (fast path, passed key, known-edge strategy,
                           no-edge series with p>0.05), regime_stratified_cv (4 regimes),
                           compute_cv_summary (stability score in [0,1], all required keys)
  test_statistical_tests.py (Sprint 3 additions) — deflated_sharpe_ratio (required keys,
                           high Sharpe passes, low Sharpe fails at sr_star≈0.096,
                           more trials raises sr_star, non-normal affects result, p in [0,1]),
                           probabilistic_sharpe_ratio (required keys, psr in [0,1],
                           above benchmark gives high PSR, equal gives ≈0.5, CI width,
                           larger n tighter CI), spa_test (required keys, p in [0,1],
                           identifies best strategy, reproducible with seed, random strategy
                           p>0.05, 10 skipped when hmmlearn absent — expected)
  test_regime_detector.py — _classify_threshold (bull/bear/transition/all-None/partial),
                           _check_agreement (same agrees, BULL vs BEAR disagrees, TRANSITION
                           neutral with both, None HMM trivially agrees), classify_hmm_regime
                           (dict, required keys, valid regime label, probs sum to 1, labels
                           length, insufficient data error), fit_hmm_historical (dict,
                           transition matrix rows sum to 1, VIX branch runs) — 8 tests run,
                           10 HMM tests SKIP on Windows (hmmlearn requires C++ build tools;
                           run in CI on Linux where hmmlearn installs via pre-built wheel)

Backend — new implementation files (no new tests required, covered by above):
  tools/optimizer.py     — 6 methods: MEAN_VARIANCE (cvxpy CLARABEL QP), RISK_PARITY
                           (scipy SLSQP), MIN_VARIANCE (cvxpy), BLACK_LITTERMAN
                           (He & Litterman 1999 closed-form), MAX_SHARPE (SLSQP with box
                           constraints — CLARABEL doesn't enforce bounds after change-of-vars),
                           MIN_DRAWDOWN (CVaR LP via cvxpy); efficient_frontier 100-point sweep
  tools/cross_validation.py — 7 CV methods: walk_forward, expanding_window, purged_kfold
                           (Lopez de Prado 252-day embargo), CPCV C(6,2)=15 paths,
                           monte_carlo_permutation (block bootstrap, seed=42),
                           regime_stratified, compute_cv_summary (5-component stability score)
  tools/statistical_tests.py (Sprint 3 additions) — deflated_sharpe_ratio (Lopez de Prado,
                           expected-max of K Sharpes), probabilistic_sharpe_ratio (95% CI
                           via delta method), spa_test (Hansen SPA, block bootstrap)
  tools/regime_detector.py (Sprint 3 additions) — classify_hmm_regime (GaussianHMM
                           3-state, state labelling by mean return), fit_hmm_historical
                           (transition matrix with VIX feature support)

Infrastructure:
  main.py — /api/backtest/compare now calls run_all_strategies() (all 10, real data)
             /api/optimize/weights now calls real optimizer + efficient frontier
             /api/health sprint field updated to "3"
  cvxpy installed in venv (CLARABEL solver; ECOS not available on Windows without C++ tools)

Test counts: 352 passed, 10 skipped (HMM), 0 failed
Frontend: 47 tests pass (unchanged from Sprint 2)

## Sprint 4 ⏳ PENDING
Backend:
  test_scope_guard.py    — in-scope pass, out-of-scope reject, injection pre-screening
  test_agents.py         — agent response schema validation (updated stubs → real tests)
  test_qa_agent.py       — 30-point checklist execution

## Sprint 5 ⏳ PENDING
Backend:
  test_rate_limiting.py  — rate limit enforcement per endpoint
  test_credit_cap.py     — daily spend cap blocks further requests
  test_explainer.py      — Explainer Agent response schema
Frontend:
  ChartCommentStrip.test.tsx — strip renders, expands, collapses, Commentary Mode
  ExplainableText.test.tsx   — hover and click behaviour, glossaryStore integration

## Sprint 6 ⏳ PENDING
  Full regression suite
  Performance benchmarks (API response times p95)
  Accessibility audit (axe-core WCAG AA)
  Print stylesheet test
