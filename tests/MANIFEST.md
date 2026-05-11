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

## Sprint 2 ✅ COMPLETE
Backend:
  test_data_fetcher.py   — yfinance fetch (mocked), cache hit skips API call,
                           FRED series fetch (mocked), risk-free rate fallback
                           when FRED unavailable, ValidationResult, NaN gap
                           detection, negative price flagging
  test_risk_metrics.py   — annualized_return (known formula), annualized_volatility
                           uses sqrt(252), sharpe_ratio (time-varying rf, scalar rf),
                           sortino_ratio, max_drawdown (no-loss case, known loss),
                           compute_var / compute_cvar ordering, calmar_ratio,
                           ANNUALIZATION_FACTOR = 252 contract test
  test_backtester.py     — verify_no_lookahead passes for t-1 signals, raises for
                           same-day and future signals, run_benchmark structure and
                           field types (mocked data), BENCHMARK is not significant,
                           no transaction costs for buy-and-hold, weight validation
                           (sum != 1, short positions both raise AssertionError)
  test_statistical_tests.py — paired_ttest structure and p-value range, identical
                              returns → high p-value, large alpha → p < 0.005,
                              tier1/tier2/directional threshold dispatch, normality
                              test (normal vs heavy-tailed), autocorrelation test
                              (IID vs AR(1)), FDR correction increases p-values,
                              power_check tier assignment, alpha_significance_test
Frontend:
  Dashboard.test.tsx     — existing mock-data test continues to pass (API contract
                           unchanged; Sprint 2 real data served by backend, frontend
                           tests use mocked axios responses)

## Sprint 3 ⏳ PENDING
Backend:
  test_optimizer.py      — 6 optimization methods, weight constraints
  test_cross_validation.py — walk-forward, expanding window, purged k-fold, CPCV
  test_regime_detector.py  — threshold classification (VIX, yield curve, equity trend),
                             HMM states, regime agreement flag
  All 10 strategy backtests with real results

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
