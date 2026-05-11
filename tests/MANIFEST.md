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
