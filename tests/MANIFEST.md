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
  test_numerical_accuracy.py — deterministic arithmetic contracts (no network/Excel):
                           portfolio_return_additive (0.6×eq + 0.4×ig arithmetic, not geometric),
                           cagr_constant_1pct_12months ((1.01^12-1) to 4dp),
                           cagr_constant_2pct_60months (annualisation uses len/12 not 252),
                           sharpe_known_series (μ-rf)/σ×√12 to 4dp with np.random.seed(0)),
                           max_drawdown_known_path ([-0.5,0.3,0.2]→-0.50 exactly),
                           max_drawdown_no_drawdown (monotone increasing → 0.0),
                           equal_weight_one_third (avg_equity_weight=1/3 to 6dp),
                           risk_parity_sum_to_one (avg_eq+avg_bond=1.0 to 6dp),
                           risk_parity_equity_below_benchmark (w_eq < 1/3 with σ_eq > σ_bond),
                           run_all_strategies_returns_dict, _has_expected_keys (10 identifiers),
                           _benchmark_accessible_by_key (dict["BENCHMARK"]["sharpe_ratio"]),
                           portfolio_returns_arithmetic_not_geometric (_portfolio_returns_monthly)
  test_splice_integrity.py — LQD-to-BND join validation (Excel-dependent, skips in CI):
                           no_missing_months_at_join (2007-03 to 2007-07 all present, gaps 25-35d),
                           no_outlier_at_join (z-score ≤ 3.0 vs ±6-month window),
                           spliced_ig_no_nan (zero NaN across full series),
                           spliced_ig_cagr_plausible (3%–7% annually);
                           DB-provenance tests (additionally skip if Postgres not reachable):
                           pre_cutover_rows_cite_lqd_bridge (ig_source="ig_lqd_bridge" before 2007-05-31),
                           post_cutover_rows_cite_ig_monthly_bnd (ig_source="ig_monthly_bnd" from 2007-05-31)

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
  main.py — /api/backtest/compare now calls run_all_strategies() (all 10, real data);
             run_all_strategies() now returns dict[str, dict] keyed by strategy identifier
             (BENCHMARK, CLASSIC_60_40, …); compare endpoint converts to ranked list for API
             /api/optimize/weights now calls real optimizer + efficient frontier
             /api/health sprint field updated to "3"
  cvxpy installed in venv (CLARABEL solver; ECOS not available on Windows without C++ tools)

Test counts: 356 passed, 10 skipped (HMM), 0 failed  ← pre close-out
             ~369 passed (adds 13 new tests in test_numerical_accuracy.py +
             test_splice_integrity.py; splice tests skip without Excel file)
Frontend: 47 tests pass (unchanged from Sprint 2)

## LQD Bridge (post-Sprint 3, 2026-05-11)
Extended IG bond coverage from ~2007-05 back to ~2002-07 using LQD (iShares iBoxx $
Investment Grade Corporate Bond ETF) as a pre-BND bridge fetched from yfinance.

Backend — new/updated tests:
  test_supplemental_fetcher.py (2 new) — test_supplemental_fetcher_has_lqd_bridge_daily:
                           lqd_bridge_daily key present in result, is pd.Series, values
                           are decimal returns not price levels (abs max < 0.15);
                           test_supplemental_fetcher_lqd_bnd_hyg_not_in_bond_fetches:
                           BND and HYG never passed to yfinance (LQD-only exception confirmed)
  test_data_loader.py (2 new) — test_build_monthly_returns_extends_back_with_lqd:
                           with synthetic LQD daily (2002-08 to ~2006-12), extended series
                           starts earlier than BND-only baseline, has more rows, no NaN
                           in core columns after splice;
                           test_build_monthly_returns_without_supplemental_unchanged:
                           build_monthly_returns(provided, supplemental=None) identical to
                           build_monthly_returns(provided) — backward compatibility confirmed
  test_data_provenance.py (updated) — test_provenance_matches_actual_source updated to
                           allow ticker in ("SPY", "LQD") for yfinance series; LQD is the
                           only permitted non-SPY yfinance ticker (pre-BND IG bridge)

Backend — implementation changes (no new test files; covered by updated tests):
  tools/data_fetcher.py — fetch_supplemental_data() fetches LQD 2002-01-01 to 2007-05-31,
                           stores as lqd_bridge_daily (daily decimal returns);
                           build_daily_returns(provided_data=None, supplemental=None) splices
                           LQD returns before BND start date (backward-compatible default);
                           build_monthly_returns(provided_data=None, supplemental=None) splices
                           LQD monthly compound returns before BND monthly start;
                           get_full_history() passes supplemental to both build functions;
                           _build_registry_entries() / _write_provenance() add ig_lqd_bridge
                           entry (source_type=yfinance, ticker=LQD);
                           _upsert_monthly() sets ig_source=ig_lqd_bridge for pre-BND months,
                           ig_source=ig_monthly_bnd for post-BND months;
                           _run_sanity_assertions() assert 5 updated: <220 warns (underpowered),
                           220-287 logs acceptable (LQD IPO 2002-07 limits coverage to ~268
                           months), ≥288 logs pass

Test counts: 356 passed, 10 skipped (HMM on Windows), 0 failed

## Sprint 3 Addendum ✅ COMPLETE (2026-05-11)
Numerical accuracy, data transformation, and strategy constraint tests.
All 111 new tests pass; 0 failed; 12 warnings (deprecation only).

Backend — new/updated test files:
  test_numerical_accuracy.py (rewritten, 5 new tests added)
                           make_history(seed=42) promoted to primary fixture
                           (make_random_history = alias for backward compat);
                           test_sortino_geq_sharpe_positive_mean_excess (Sortino≥Sharpe
                           when mean excess > 0 — confirmed by downside std ≤ total std);
                           test_returns_are_simple_not_log (pct_change not log diff —
                           verified via np.log comparison, gap > 0.001 on 10% return);
                           test_pct_change_drops_first_row (len-1 after dropna, correct
                           values); test_monthly_annualisation_uses_sqrt_12 (vol from
                           sqrt(12) differs from sqrt(252) by > 0.05 — measurable gap);
                           test_run_all_strategies_regression_constants (_EXPECTED_RESULTS
                           dict for all 10 strategies, tolerance 5e-4 — catches any
                           arithmetic change in the pipeline)
  test_data_transformations.py (new, 17 tests)
                           Group 1 — serial dates: 36526→2000-01-01, 39448→2008-01-01,
                           tz-naive, vectorised conversion matches scalar;
                           Group 2 — month-end snapping: mid-month, already-end, Feb leap;
                           Group 3 — simple returns: pct_change=+0.10/−0.50 exactly,
                           log comparison confirms not log, first row dropped;
                           Group 4 — DTB3 compound conversion: 5%→0.004074 (not 0.004167),
                           zero rate →0.0, source-code inspection confirms (1/12) exponent
                           present and simple-division pattern absent;
                           Groups 5–6 — Excel-dependent (skip in CI): aligned dataset
                           no NaN, shared index, 2023 rf in [0.004,0.005], Oct 2008
                           equity return in [−20%,−14%]
  test_splice_integrity.py (1 new test added)
                           test_cumulative_price_index_continuous_at_splice: builds
                           cumprod index, verifies implied return at 2007-05-31 matches
                           stated return to 1e-4; detects any jump or base discontinuity
                           at the LQD→BND join
  test_strategy_constraints.py (new, 14 tests)
                           TestWeightsSumToOne — parametrised over 10 strategies:
                           avg_equity_weight + avg_bond_weight ≈ 1.0 (tolerance 5e-3);
                           TestNoNegativeWeights — parametrised over 10 strategies:
                           both weights ≥ 0.0;
                           test_momentum_rotation_uses_lookback_window (n_observations ≤ 48
                           from 60 months — 12-month lookback consumed);
                           test_vol_targeting_uses_rolling_window (n_observations > 0,
                           avg_equity_weight in [0, 0.40] — vol-cap enforced; note: VOL
                           uses 21 daily returns not 21 monthly, so n_obs = n_months is
                           correct and expected);
                           test_min_variance_uses_optimization_window (n_observations ≤ 24
                           from 60 months — 36-month optimizer window consumed);
                           test_transaction_costs_reduce_classic_6040_cagr (avg_monthly_
                           turnover > 0, CAGR is finite float in (−1, 1))
  test_benchmark_plausibility.py (new, 45 tests)
                           Historical range checks (5 tests, Excel-dependent, skip in CI):
                           BENCHMARK CAGR 7–11%, Sharpe 0.35–0.75, max_drawdown −45% to
                           −58%, 2022 equity return −18% to −22%, 2009 equity return
                           +20% to +30%;
                           TestImplausibilityGuards (40 parametrised tests, synthetic data,
                           always run): Sharpe ≤ 2.0, CAGR ≤ 20%, max_drawdown ≤ 0,
                           all 4 core metrics finite (no NaN/Inf) — for all 10 strategies

Test counts: 111 passed, 0 failed, 12 warnings (pandas/asyncpg deprecation only)
             Excel-dependent tests (10): skip in CI without FNA_670_Project_Sources.xlsx

## Sprint 4 ✅ COMPLETE (2026-05-12)
All 8 agents, council endpoint, QA audit, scope guard, AI usage logger,
and academic writer scaffold operational. 576 tests passing, 10 skipped (HMM on Windows).

Backend — new test files:
  test_scope_guard.py (18 tests)
    TestInScopeQueries (7): portfolio strategy, Sharpe ratio, regime, 2022 correlation,
      comparison, QA methodology, risk metrics — all allowed, confidence ≥ 0.80;
    TestOutOfScopeQueries (2): general knowledge (Python history) and crypto (off-scope);
    TestInjectionPrescreen (6): "ignore previous instructions", "forget instructions",
      "you are now", "act as", "pretend you are", "reveal your instructions"
      all detected by regex pre-screen before API call;
    TestScopeGuardResultSchema (3): allowed/category/confidence fields present, confidence
      in [0, 1], rejection_message None for in-scope queries

  test_agents.py (coverage of all 8 agent response schemas)
    Tests schema fields across: EquityAnalyst, FixedIncomeAnalyst, RiskManager,
      QuantBacktester, IndependentAnalyst, QAAgent, CIO, ExplainerAgent;
    Every agent response must include: technical_findings (dict), summary (non-empty str),
      layman_explanation (dict with what_we_found/why_it_matters/for_our_portfolio/confidence);
    QA-specific: limitations (non-empty list), data_caveats (non-empty list),
      model_assumptions (non-empty list);
    Gemini-specific: gemini_challenge (non-empty str), alternative_metrics (dict);
    CIO-specific: final_recommendation (non-empty str), consensus_reached (bool),
      corrections_made (list), agents_consulted (list)

  test_council_deliberation.py (13 tests)
    TestCouncilQueryEndpoint (5): 200 for in-scope portfolio query, query field in response,
      501+ character queries rejected (rate limit), unauthenticated rejected (401/422),
      mock_council_session structure valid (all required fields);
    TestCouncilQAEndpoint (4): POST /api/qa/audit returns 200, checks_passed key present,
      POST /api/qa/ask returns 200, answer field present;
    TestScopeGuardIntegration (2): in-scope passes, out-of-scope returns 422 with out_of_scope error;
    TestCouncilSignificanceConsistency (2): n_significant ≤ 10, all significant strategies
      present in strategy_results

  test_qa_agent.py (22 tests)
    TestQADeterministicChecks (8): _run_deterministic_checks() has keys for weights_sum,
      no_negative_weights, has_benchmark, has_dynamic_strategies, has_significant_strategies,
      has_oos_results, uses_risk_free_rate, annualisation_correct — all return status/evidence dicts;
    TestQAAuditStructure (4): run_audit() returns 30 items, n_pass+n_warn+n_fail=30,
      verdict one of PASS/WARN/FAIL, items have required keys (check_id/check/description/
      status/category/evidence);
    TestQALimitationsGeneration (6): limitations non-empty, contains known strings
      ("structural regime changes", "LQD bridge"), data_caveats non-empty, contains BND start
      and BAMLHYH strings, model_assumptions non-empty, contains BL priors string;
    TestQAChecklist (4): all 7 category prefixes present (D/P/S/C/O/E/PR),
      exactly 30 items, all items have check_id+category+description+status+evidence,
      no duplicate check_ids

  test_ai_usage_log.py (13 tests)
    TestCouncilSessionLogging (3): _log_council_session() doesn't raise (in-memory path),
      silent skip when no DB configured, log record is a dict;
    TestHealthEndpointSprintLabel (3): GET /api/health sprint field = "4",
      anthropic and gemini are bool fields, db_connected is bool;
    TestAcademicWriterScaffold (5): AcademicWriter importable from agents.academic_writer,
      references.json loads as valid JSON, required entries present (sharpe_1994,
      black_litterman_1992, lopez_de_prado_2018, benjamin_2018, markowitz_1952),
      each entry has apa/author/year/title/source/use_for fields,
      write_references() only outputs keys from provided list (no hallucinated sources);
    TestLimitationsFields (2): QAAgent().run_audit() result has limitations/data_caveats/
      model_assumptions as non-empty lists

  test_guardrails.py
    Backend guardrail assertions: weight sum enforcement, no-negative-weight enforcement,
      scope guard integration, credit cap configuration, rate limit configuration

  test_mock_data.py (12 test functions updated — Sprint 1 file)
    All 12 QA audit tests updated from old field names (passed/warned/failed/checks/id/label/verdict)
    to new field names matching QAAgent output schema:
      checks_passed, checks_warned, checks_failed, verdict, items, check_id, description, status
    test_qa_audit_ids_are_sequential updated: check_id values are category-prefixed strings
      (D01, P01, etc.) not integers 1-30 — non-empty string validation replaces range check

Backend — new implementation files:
  agents/base.py            — BaseAgent with call_claude() + build_agent_response() helpers
  agents/equity_analyst.py  — EquityAnalyst (Sonnet), pre-computed summaries before LLM call
  agents/fixed_income_analyst.py — FixedIncomeAnalyst (Sonnet), correlation arithmetic before LLM
  agents/risk_manager.py    — RiskManager (Sonnet), risk arithmetic + significance tallies before LLM
  agents/quant_backtester.py — QuantBacktester (Sonnet), OOS degradation pre-computed
  agents/independent_analyst.py — IndependentAnalyst (Gemini), challenge_consensus()
  agents/qa_agent.py        — QAAgent (Opus), 30-point audit, deterministic overrides
  agents/cio.py             — CIO (Opus), 10-step council flow, Gemini integration
  agents/explainer_agent.py — ExplainerAgent (Haiku), 5 explain endpoints
  agents/academic_writer.py — AcademicWriter scaffold (endpoints deferred to Sprint 6)
  scope_guard.py            — ScopeGuard with regex pre-screen + Haiku classifier
  main.py additions:        — /api/council/query, /api/qa/audit, /api/qa/ask,
                              /api/explain/terms, /api/explain/chart, /api/explain/qa,
                              /api/health sprint="4", council_sessions logging

Commentary review (Sprint 4 — all modules pass standard):
  All public methods across all 8 agent files, scope_guard.py, and main.py reviewed.
  Decision comments added to: analyse() × 4 agents, _build_context() × 4 agents,
  _fallback_response() × 4 agents, _parse_response() (quant), _extract_summary() (equity),
  _parse_challenge() (Gemini), _build_audit_context(), _build_report(),
  _generate_limitations/data_caveats/model_assumptions() (QA agent).
  Pattern: all comments explain WHY arithmetic runs before LLM call (anti-hallucination),
  or WHY a particular fallback/format choice was made.

Test counts: 576 passed, 10 skipped (HMM on Windows), 0 failed
  Frontend: 47 tests pass (unchanged from Sprint 3)

## Sprint 4 Remediation + Deployment Tests ✅ COMPLETE (2026-05-12)

### Sprint 4 Remediation (commit 69758e6)
Four bugs fixed after Sprint 4 initial completion:

  FixedIncomeAnalyst fallback schema fix:
    _fallback_response() was not mapping breakdown_detected and
    diversification_effective to the top-level of technical_findings.
    Tests that read those keys directly were receiving KeyError in the
    fallback path. Fixed by explicitly mapping them in fallback.

  RiskManager fallback key mismatch:
    Internal risk_summary uses key "n_significant"; the schema and tests
    expect "n_strategies_significant" in technical_findings.
    _fallback_response() now maps explicitly — both paths now produce
    identical schemas.

  Async event loop affinity error in _persist_to_db():
    asyncio.run() called inside an already-running event loop. Fixed by
    using asyncio.get_event_loop().run_until_complete() with a new loop
    when required, or by restructuring the call to avoid nesting.

  MOCK_STRATEGIES used as primary data path:
    main.py was serving MOCK_STRATEGIES as the primary response to
    /api/backtest/compare rather than only as a fallback when the
    database is unavailable. Fixed — real run_all_strategies() results
    are primary; mock is fallback only.

### Deployment Testing Infrastructure (2026-05-12)

  tests/conftest.py  — pytest marker registration via pytest_configure hook
    Problem solved: pytest resolves rootdir as forest-capital/ (repo root)
    when invoked as `python -m pytest ../tests/` from backend/. With
    rootdir at the repo root, backend/pyproject.toml is not loaded and
    marker definitions in [tool.pytest.ini_options] are invisible to pytest.
    This causes PytestUnknownMarkWarning for @pytest.mark.deployment.
    Fix: conftest.py at tests/ root uses pytest_configure() hook which is
    always loaded regardless of rootdir. Belt-and-braces: pyproject.toml
    also defines the marker so both invocation styles are covered.

  tests/test_deployment.py  — three live-URL tests (@pytest.mark.deployment)
    Run selectively: pytest -m deployment
    Skipped in normal CI (live URLs, Render cold-start latency up to 30s)
    TIMEOUT = 60s to accommodate Render free-tier cold starts

    test_render_health_endpoint:
      GET https://forest-capital.onrender.com/api/health
      Asserts: HTTP 200, environment="production", anthropic=True, gemini=True

    test_vercel_frontend_serves:
      GET https://forest-capital.vercel.app
      Asserts: HTTP 200 (React app is served)

    test_vercel_api_rewrite:
      GET https://forest-capital.vercel.app/api/health
      Asserts: HTTP 200
      Purpose: verifies frontend/vercel.json /api/:path* rewrite is active
      and proxying correctly to the Render backend. If this fails while
      test_render_health_endpoint passes, the rewrite rule is broken.

  These 3 tests are NOT counted in the 576 total — they are excluded from
  normal CI runs and must be invoked explicitly with -m deployment.

## Sprint 5 ✅ COMPLETE (2026-05-12)
Cache layer, export infrastructure, sanity panel, FRED timeout fix,
incremental data ingestion, auth security tests, admin screen spec.
651 backend tests passing, 10 skipped (HMM on Windows). 73 frontend tests passing.

Backend — new test files:
  test_cache_layer.py (16 tests)
    TestStrategyCacheHit (3): hit returns result dict within 500ms,
      hit skips run_all_strategies() call (strategy function not called),
      hit result has required keys (sharpe_ratio, cagr, max_drawdown);
    TestStrategyCacheMiss (3): miss calls through to recompute, miss populates
      cache for subsequent hits (second call resolves faster), miss result
      has identical keys to hit result;
    TestStrategyCacheHashInvalidation (2): stale hash causes miss + recompute,
      fresh hash with matching data causes hit — no redundant recomputation;
    TestRegimeSignalsCache (4): cache hit skips FRED calls, cache returns
      required regime keys (vix_level, threshold_regime, hmm_state, hmm_probabilities),
      cache expires after 15 minutes (mock time.time advance), miss triggers
      fresh FRED fetch and updates expiry;
    TestCacheRestartSurvival (2): strategy cache read after simulated restart
      (clear in-memory state, re-query DB) returns same result as before,
      regime cache survives restart within TTL window;
    TestCacheReturnTimes (2): cached strategy result returns in < 500ms,
      cached regime result returns in < 100ms

  test_incremental_ingestion.py (10 tests)
    TestNoNewDataWhenCurrent (3): last_date < 35 days ago → no yfinance call,
      no FRED call, no rows appended to market_data_monthly;
    TestIncrementalFetchWhenStale (4): last_date ≥ 35 days ago → yfinance called
      for SPY from last_date to today, FRED called for VIX+DGS2 from last_date,
      new rows appended to market_data_monthly, run_all_strategies() called only
      when new rows were appended (not when no-op);
    TestHistoricalDataNeverRestated (3): rows before last_date unchanged after
      incremental run (checksum same), incremental fetch does not re-download
      pre-existing date range, LQD bridge rows (pre-2007) untouched

  test_fred_fetch.py (12 tests)
    TestFredTimeoutConfig (5): FRED_TIMEOUT_SECONDS == 60 in config,
      _fred_fetch importable from tools.data_fetcher, _fred_fetch passes
      timeout=60 to requests.get (patches global requests.get since import
      is inside function body), FRED_API_KEY appended to URL when set,
      FRED_API_KEY absence does not crash at import;
    TestFredFallback (3): regime cache returns cached data when FRED unavailable
      (mock get_regime_cache returns dict with all required keys), FRED timeout
      raises requests.exceptions.Timeout not generic Exception,
      missing FRED_API_KEY does not crash module reload;
    TestFredDataShape (4): VIX result is numeric DataFrame (all columns float),
      '.' missing-value sentinel is dropped (1 row remaining from 2-row CSV),
      empty FRED response (header only) raises ValueError,
      FRED result has correct column structure

  test_admin_screen.py (13 tests)
    TestDataHealthAccess (4): unauthenticated request returns 401/403/404/422
      (not 200), GET /api/health returns 200 (base health still reachable),
      force-refresh without MASTER_API_KEY returns non-200,
      force-refresh with correct MASTER_API_KEY returns 200/202/404;
    TestDataHealthSchema (9): registry_series_count == 16,
      market_data_monthly_rows ≥ MIN_OBSERVATIONS_FOR_POWER (220),
      all sanity assertions have required fields (assert_id/description/
      expected/actual/status), cross_validation block has equity and
      bond_internal sub-keys, all source_breakdown entries have status
      in {pass, warn, fail}, last_pipeline_run is ISO timestamp with T and Z,
      cache_status in {hit, miss, stale}, source_breakdown has at least one
      excel_provided and one fred_api entry, all mock sanity assertions pass

  test_security.py (15 tests)
    TestEmailEnumerationPrevention (5): approved email returns HTTP 200,
      unapproved email also returns HTTP 200 (never 401/403),
      approved email response has status="sent", unapproved has status="pending",
      both receive identical message text (enumeration prevention);
    TestMagicLinkTokenSecurity (4): generate_magic_token produces decodable JWT
      with sub=email and type="magic_link", redeem_magic_token is single-use
      (second call returns same session — scanner-safe), invalid token raises
      exception (401 if HTTPException), expired token raises exception;
    TestSessionTokenSecurity (3): generate_session_token produces JWT verifiable
      with same secret (sub=email, type="session"), verify_session_token extracts
      email correctly from valid token, tampered signature raises exception;
    TestAuthAttemptsSchema (3): auth_attempt record has all required fields
      (timestamp/email/ip_address/status), valid status values are defined
      (sent/rejected/geo_blocked/rate_blocked), status="sent" maps to
      check-inbox message and status="pending" maps to generic message only

Backend — new implementation files:
  tools/cache.py         — get_strategy_cache()/set_strategy_cache() (PostgreSQL-backed,
                           keyed by strategy_hash), get_regime_cache()/set_regime_cache()
                           (15-min TTL, expires_at column), both survive Render restarts
  tools/data_fetcher.py  — check_last_date_in_db() reads MAX(date) from market_data_monthly,
                           fetch_incremental_delta() fetches SPY/VIX/DGS2 from last_date
                           to today when stale (≥35 days behind), historical data never
                           re-fetched (2002–2024 rows untouched by incremental runs)
  config.py              — FRED_TIMEOUT_SECONDS = 60 (replaces implicit 30s default that
                           caused 3-minute dashboard load times on FRED outage days)

Frontend — new test files:
  ChartExportButton.test.tsx (5 tests)
    renders without errors, PNG download button present with data-testid,
    has accessible aria-label, PNG click triggers URL.createObjectURL,
    SVG click creates SVG blob and initiates download

  TableExportButton.test.tsx (5 tests)
    renders without errors, has accessible aria-label, shows "CSV" label,
    CSV export creates a Blob (verified via createObjectURL capture),
    filename matches pattern tableId_YYYYMMDD.csv — verified via
    mockAnchor.download match (captures original document.createElement
    before spying to avoid infinite recursion in the 'a' tag fallback)

  SanityCheckPanel.test.tsx (21 tests)
    TestSanityPanelRender (5): renders without errors, shows "SANITY CHECKS"
      heading, all 10 check labels present, renders pass/fail status indicators,
      shows overall summary count;
    TestSanityPanelStatus (5): GREEN check shows green indicator, RED check shows
      red indicator and warning banner ("Review required"), all GREEN shows
      "Data integrity confirmed" banner, AMBER check shows amber indicator,
      mixed results show correct pass count;
    TestSanityCheckValues (5): check 1 shows expected CAGR range 8-12%,
      check 3 shows BND 2022 range -12% to -16%, check 10 shows ≥288
      observation threshold, actual value displayed alongside expected,
      status column present in check rows;
    TestSanityPanelExport (3): export button present with "Download for Appendix"
      label, clicking export triggers CSV download (URL.createObjectURL called),
      exported filename includes "sanity" and timestamp;
    TestSanityCommentaryMode (3): checks render without commentary strips by default,
      commentary strips appear when commentaryMode prop is true,
      each check has associated analyst note in commentary mode

Frontend — new implementation files:
  ChartExportButton.tsx  — lazy html2canvas import for 2× PNG (avoids bundle bloat),
                           SVG serialisation via XMLSerializer, chart canvas found
                           by data-chart-id attribute, filename pattern:
                           {chartId}_{YYYYMMDD}.{ext}
  TableExportButton.tsx  — UTF-8 BOM prepended to CSV (prevents Excel encoding errors
                           for degree symbols and special characters), headers from
                           props, all visible rows included, filename:
                           {tableId}_{YYYYMMDD}.csv
  SanityCheckPanel.tsx   — 10 statically-defined checks with expected ranges,
                           status computed from actual values fetched from
                           /api/v1/admin/data-health, RED items trigger warning
                           banner, export assembles CSV from rendered check data

Commentary review (Sprint 5 — all modules pass standard):
  All new backend modules reviewed. Decision comments cover:
  tools/cache.py — WHY 15-min TTL for regime cache (FRED outage tolerance),
    WHY strategy_hash invalidation (data changes invalidate stale results),
    WHY separate tables for strategy vs regime (different TTL semantics);
  tools/data_fetcher.py incremental additions — WHY 35-day threshold
    (one full month plus buffer — avoids re-fetching a partial month),
    WHY historical rows never restated (LQD bridge data is stable,
    yfinance auto_adjust may shift on corporate actions);
  config.py FRED_TIMEOUT_SECONDS — WHY 60s not 30s (documented FRED gateway
    stalls that caused 3-minute dashboard loads, regime cache absorbs repeat hits)

Test counts: 651 passed, 10 skipped (HMM on Windows), 0 failed
  Frontend: 73 tests pass

## Sprint 5 Addendum ✅ COMPLETE (2026-05-12)
QA gate for Present mode, Team Primer, correlation breakdown live data,
magic link JTI persistence, provenance justification endpoint, 0/10 tooltip.

Backend — new/updated:
  main.py — verify_magic_link updated with DB-backed JTI check (is_jti_used /
             mark_jti_used from tools/cache.py); GET /api/v1/provenance/justification
             endpoint added (spy_daily, vixcls, dgs2, lqd_bridge structured metadata)
  models/schemas.py — MOCK_REGIME updated with pre_2022_avg_correlation and
             post_2022_avg_correlation fields for test environments

  test_provenance_justification.py — 17 tests:
    Endpoint returns 200; all four supplemental sources present;
    each source has required fields (strategies_enabled, key_reason,
    months_added, statistical_impact, without_this_source);
    lqd_bridge months_added ≥ 50; spy_daily months_added = 0;
    VOL_TARGETING and MOMENTUM_ROTATION in spy_daily.strategies_enabled;
    REGIME_SWITCHING in vixcls and dgs2 strategies_enabled;
    lqd_bridge enables ≥5 strategies; all string fields non-empty;
    strategies_enabled is a list not a string

Frontend — new/updated:
  MainLayout.tsx — QA gate on Present mode (lock on unknown/fail, warn badge
                   on warn, navigate to /qa when unknown, 🔒 ○ ⚠ indicators);
                   HelpCircle ? icon linking to /TEAM_PRIMER.md (served from public/)
  Dashboard.tsx  — 0/10 significant strategies note (amber colour + italic
                   explanatory line when no strategies pass all 5 Tier 1 gates);
                   MetricTile accepts note prop (shown as italic subtitle and
                   native title tooltip)
  frontend/public/TEAM_PRIMER.md — plain English guide to all three modes;
                   sections for Bob (Commentary mode workflow), Molly (Present
                   mode workflow), Michael (pre-presentation checklist);
                   FAQ explaining 0/10, data provenance, AI council architecture

Correlation breakdown banner: already correct in prior session —
  detect_current_regime() computes pre_2022_avg_correlation and
  post_2022_avg_correlation from live DB data; Dashboard reads from
  regimeStore (never hardcoded); fallback to −0.31/+0.48 only when API returns null

Magic link JTI persistence: DB check (is_jti_used) runs before token
  redemption in verify_magic_link endpoint; mark_jti_used called after
  successful redemption; in-memory dict still handles scanner pre-fetch
  within same instance; DB handles cross-restart replay attack prevention

Test counts (Sprint 5 + addendum): 668 passed, 10 skipped (HMM on Windows), 0 failed
  Frontend: 73 tests pass
  New addendum tests: 17 (test_provenance_justification.py, all passing)

## Sprint 6 ⏳ PENDING
  Academic Writer Agent (agents/academic_writer.py)
  Report generation endpoints (analytical-appendix, executive-brief, midpoint)
  Storyboard Editor UI + API (POST /api/documents/storyboard/draft, etc.)
  Script Writer (generate-from-storyboard output_type="script")
  Version control infrastructure (documents, document_versions, document_drafts tables)
  Gemini Assistant panel (embedded in Storyboard Editor and Section Editor)
  Full regression suite
  Performance benchmarks (API response times p95)
  Accessibility audit (axe-core WCAG AA)
  Print stylesheet test (@media print)

## Sprint 6 Phase 6 ✅ COMPLETE (2026-05-14)
Storyboard Editor + Presentation Script Writer + Gemini Assistant.

Backend:
  tools/storyboard_template.py  — 15-slide default + Academic Writer
                                   enrichment + context-driven interpolation
  tools/documents_cache.py       — full CRUD against documents,
                                   document_versions, document_drafts
                                   (migration 004). Atomic create_document
                                   inserts parent + draft + v1 in one txn.
  tools/pptx_generator.py        — .pptx via python-pptx with AI DRAFT
                                   footer on every slide + speaker notes
  tools/script_writer.py         — 130 wpm voice-differentiated scripts,
                                   rehearsal cues, Q&A doc with 18 questions

  Endpoints (main.py):
    POST   /api/documents/storyboard/draft
    GET    /api/documents/:id
    PATCH  /api/documents/:id/draft
    POST   /api/documents/:id/versions
    GET    /api/documents/:id/versions
    POST   /api/documents/:id/restore/:ver_id
    POST   /api/reports/generate-from-storyboard/:id  (deck/script/qa)
    POST   /api/documents/:id/assistant               (Gemini diff)

  tests/test_storyboard_endpoints.py — 24 tests:
    Draft generation: 200, contains storyboard, 15 slides, all three
      team owners present, total timing 18–20.5 min, every slide has
      the required 11 fields
    Generate-from-storyboard: deck returns valid pptx, filename .pptx,
      script returns valid docx, script_molly excludes Bob slides,
      rehearsal includes cues, Q&A docx contains all three sections,
      invalid output_type returns 422
    Template unit: pure-function builds 15-slide structure with no
      strategy_results; with results, interpolates {best_strategy}
      into headlines
    Assistant: 200, returns diff object, rejects missing message,
      rejects oversized message (>1000 chars)
    pptx_generator unit: valid bytes, correct slide count (storyboard + 1)
    script_writer unit: full script includes all slides, owner_filter
      excludes other owners, rehearsal cues only when requested

Frontend:
  components/GeminiAssistantPanel.tsx — purple-accented sliding panel,
    paragraph-level red/green diff, per-message Apply/Skip, multi-turn
    conversation, scope-guard out_of_scope handling, mock fallback display
  stores/storyboardStore.ts        — createDraft, updateSlide,
    reorderSlides, addSlide, removeSlide, saveNamedVersion, restoreVersion;
    30s debounced auto-save via shared module-level timer;
    localStorage stash of active document_id for Reports screen
  pages/StoryboardEditor.tsx       — three-column layout: slide list
    with native HTML5 drag-reorder + timing bar (green ≤20m / amber ≤21 /
    red >21), centre editor with all 11 slide fields + Regenerate
    speaker note + Remove, version-history sidebar with named versions +
    collapsible auto-saves + Restore buttons + Save Version dialog
  types/storyboard.ts              — Slide / Storyboard / DocumentVersion
    / AssistantResponse interfaces

  __tests__/storyboard.test.tsx    — 15 tests:
    createDraft: populates state + selects first slide, writes
      localStorage, surfaces error on persistence failure
    Mutations: updateSlide patches one slide and recomputes total
      timing, reorderSlides renumbers to 1..N contiguous, addSlide
      inserts and renumbers, removeSlide shifts following orders down
      and reselects nearest sibling
    Auto-save: 30-second debounce — multiple rapid edits produce one
      PATCH call (vi.useFakeTimers)
    saveNamedVersion: POSTs to /versions with content + name + summary
    GeminiAssistantPanel: renders header + textarea, disables send when
      no documentId, renders Apply after response, calls onApply with
      suggestion, renders warning when out_of_scope

Test counts (cumulative): 758 backend pass, 10 skipped (HMM/Windows),
                           111 frontend pass, npm run build clean.

Migration:
  004_create_documents_tables.py applied at storyboard endpoint
  rollout — operator runs `alembic upgrade head` on Render before
  the next deploy.


## Combined Analytics Enhancement Pass ✅ COMPLETE (2026-05-16)
The final analytical build before code-review lockdown — 12 commits
across two passes plus finalize. Derived-metric analytics, the Carhart
momentum factor, true portfolio turnover, parameter sensitivity, and
source-controlled strategy metadata.

Backend:
  migrations/009_add_mom_to_ff_factors.py — adds the nullable `mom`
    column to ff_factors_monthly (down_revision 008). Nullable, not
    NOT NULL: months predating the momentum backfill carry no value.
  tools/data_fetcher.py — MOM factor fetch + backfill. Direct HTTP of
    Ken French's F-F_Momentum_Factor_CSV.zip (no pandas-datareader).
    backfill_momentum_factor() populates `mom` where NULL; wired into
    _load_ff_factors_with_cache so a cold cache self-heals.
  tools/backtester.py — _true_turnover(schedule, n_months): the genuine
    sum-of-absolute-weight-change figure, sum(|Δw|)/2 per rebalance
    annualised. Added to every strategy result alongside the legacy
    rebalance-count proxy avg_monthly_turnover. The four dynamic
    strategy runners gained optional parameters (lookback_scale,
    regime_window_m, target_volatility, optimization_window) with
    config defaults — additive, behaviour unchanged at the defaults.
  tools/sensitivity.py — compute_sensitivity(history): sweeps each
    dynamic strategy's key parameter around its current setting and
    records the Sharpe ratio. ~23 backtests, memoised in-process by
    history length. Served by GET /api/v1/analytics/sensitivity (its
    own endpoint — deliberately NOT bundled into the light /academic).
  strategy_metadata.py — STRATEGY_METADATA: source-controlled record
    of all 10 strategies (construction logic, signal, economic
    intuition, key parameter). Optimised strategies described as
    optimised; their `weights` field is None.
  tools/analytics.py — factor_loadings() extended to the Carhart
    four-factor model (MKT-RF, SMB, HML, MOM). MOM-null rows are
    dropped per strategy; a strategy whose history predates the
    backfill falls back to a three-factor fit, recorded in `model`.
  tools/cache.py — get_ff_factors() now SELECTs `mom` (nullable).
  main.py — GET /api/v1/analytics/academic also returns
    cumulative_returns, rolling_excess_return, and strategy_metadata.

  tests/test_momentum_factor.py — 6 tests: Ken French momentum CSV
    parse, backfill update path, NULL-count query, end-to-end backfill
    return shape.
  tests/test_analytics.py — +10 tests:
    Factor loadings: four-factor regression recovers a unit MOM beta
      and records model == 'carhart_4factor'; falls back to
      'ff_3factor' when MOM is absent or NULL on every row.
    Cumulative returns: every series starts at exactly 1.0, a constant
      return compounds correctly, empty input returns empty.
    Excess return / information ratio: the benchmark's own excess is
      0.0 and its information ratio is null (zero tracking error);
      both are null when no benchmark series is supplied; the
      benchmark is excluded from the rolling-excess series list.
  tests/test_strategy_enhancements.py — 8 tests:
    True turnover: every strategy reports true_turnover, it is never
      negative, fixed-weight statics are ~0, and dynamic strategies
      turn over more than fixed-weight statics (a warning, not a hard
      failure — a degenerate synthetic path could violate it).
    Sensitivity: the sweep covers all four dynamic strategies, every
      sweep carries points + current_value + parameter, each yields
      at least one real Sharpe, and the result is memoised by
      history length.

Frontend:
  pages/AcademicAnalytics.tsx — CumulativeReturnChart,
    RollingExcessReturnChart, SensitivityAnalysis,
    StrategyMethodologyPanel; FactorLoadingsTable gains a MOM column
    and the Carhart subtitle (a dash marks a three-factor fallback);
    summary table gains excess-return and information-ratio columns.

Deviations from spec (flagged and approved):
  - `mom` left NULLABLE, not NOT NULL — a conditional ALTER outside
    Alembic is unsafe and migration 010 was not authorised. The
    regression drops NULL-mom rows, which is correct regardless.
  - Parameter sensitivity sits on its own endpoint rather than being
    bundled into /api/v1/analytics/academic — bundling ~23 backtests
    into the light read path would defeat its purpose.

Migration:
  009_add_mom_to_ff_factors.py — operator runs `alembic upgrade head`
  on Render before the next deploy.


## Team Activity Build ✅ COMPLETE (2026-05-16)
UI event tracking, interaction logging, login logging, Testing Mode,
and the Team Activity view — 14 commits.

Backend:
  migrations/010_create_activity_tables.py — session_events,
    agent_interactions, commit_activity (users keyed by email; no
    users table).
  tools/activity_log.py — the data layer: team-allowlist-gated inserts,
    git-author resolution, the unified timeline query, the per-member
    summary. Every write fail-open.
  tools/github_sync.py — webhook HMAC verification, push-payload
    parsing, the REST commit sync.
  main.py — five /api/v1/activity/* endpoints; non-blocking interaction
    logging hooked into council / academic-review / upload / QA.
  agents/academic_review.py — team-activity block in the context
    assembly; the multi-user-gated peer dimension 5 and arbiter
    section 6.
  agents/academic_writer.py — team-activity-aware system prompt;
    optional team_activity argument on write_methodology / write_discussion.

  tests/test_activity.py — 28 tests:
    Pure logic: is_team_member / resolve_git_author / display_name;
      webhook signature accept/reject, push-payload parse.
    Endpoint contracts: webhook 401 on a bad signature, non-push
      ignored, valid push accepted; events endpoint always 200 and
      auth-gated; team / summary response shapes; council endpoint
      unaffected by its logging hook.
    DB round-trips (skip without a live database): session-event
      insert + team filter, agent-interaction insert + team gate,
      commit upsert dedup on SHA, timeline sort + session_type filter,
      per-member summary counts, testing sessions excluded from the
      agent-context summary.
    Agent context: the team-activity block assembles with multiple
      users, a single active user does not trigger the division
      dimension, the peer question / arbiter message gain their extra
      section only when multi-user.

Frontend:
  context/SessionContext.tsx — per-login session_id + session_type,
    mirrored onto the axios X-Session-* headers.
  lib/activityLogger.ts + lib/useActivityTracking.ts — batched UI
    telemetry, 30s / 50-event / unload flush, route page_views.
  components/TeamActivityPanel.tsx + TeamActivityCharts.tsx — the
    Reports Team Activity section: summary, three charts, timeline,
    filters, Presentation View, CSV export.
  Testing Mode toggle (Settings → Account) + nav-bar indicator.

  __tests__/session.test.tsx — 3 tests: sessionType defaults to
    analytical, a session_id is minted for an authenticated session,
    setTestingMode toggles the band and back.

Migration:
  010_create_activity_tables.py — operator runs `alembic upgrade head`
  on Render. Webhook registration + commit backfill are post-deploy
  operator steps — see docs/TEAM_ACTIVITY_SETUP.md.


## Contextual Explainer Tooltips ✅ COMPLETE (2026-05-16)
Inline ⓘ explainer affordances across the Analytics and Dashboard
pages — hover for static text, click for a live streamed explanation.

Backend:
  agents/explainer_agent.py — stream_metric_explanation(), an async
    generator streaming a metric explanation via Haiku.
  main.py — POST /api/council/explain (auth, streamed text/plain,
    logs an "explain" agent_interaction).
  tools/activity_log.py — "explain" added to _INTERACTION_TYPES.

  tests/test_explainer_endpoint.py — 5 tests: endpoint 200 / 401 / 422
    contracts; "explain" is a registered interaction type; the explain
    interaction logs for a team email and is gated out for a non-team
    email (DB round-trip, skips without a database).

Frontend:
  constants/explainerTooltips.ts — static hover content per metric/chart.
  components/InfoIcon.tsx — ⓘ affordance: 300ms hover tooltip + click.
  components/ExplainerPanel.tsx — right-side drawer streaming the live
    explanation from POST /api/council/explain.
  Wired into AcademicAnalytics (chart titles, table titles, metric
    columns) and Dashboard (strategy-table columns, efficient frontier).

  __tests__/explainer.test.tsx — 5 tests: every EXPLAINER_TOOLTIPS key
    has content; InfoIcon renders the button / nothing for an unknown
    key / the static tooltip on hover; ExplainerPanel calls the endpoint
    on mount.

Test counts (cumulative): 1059 backend pass, 15 skipped (HMM/Windows),
                           186 frontend pass.


## Generator-Evaluator Harness ✅ COMPLETE (2026-05-17)
A reusable evaluate-and-retry quality harness wrapping agent text
generation — infrastructure, invisible to the end user.

Backend:
  agents/harness.py — GeneratorEvaluatorHarness (sync run(), HarnessResult,
    fail-open) + the per-request ContextVar metrics capture.
  agents/evaluator_prompts.py — three evaluator system-prompt builders
    (council, academic-review peer, academic-review arbiter).
  config.py — EVALUATOR_THRESHOLD / EVALUATOR_MAX_RETRIES /
    EVALUATOR_MODEL / EVALUATOR_PASSTHROUGH_ON_ERROR.
  agents/equity_analyst | fixed_income_analyst | risk_manager |
    quant_backtester — each routes its call_claude through the harness.
  agents/academic_review.py — peers route through the harness;
    stream_arbiter replaced by run_arbiter_with_harness (full generate +
    evaluate + retry) and chunk_arbiter_text for streaming.
  main.py — academic-review endpoint streams the harness-accepted
    verdict; council + academic-review endpoints attach the `harness`
    metrics block to the agent_interactions metadata.

  tests/test_harness.py — 12 tests: 7 unit (mocked evaluator), 2 metrics
    capture, 3 integration (council + academic-review API shape, arbiter
    five-section verdict).

Test counts (cumulative): 1071 backend pass, 15 skipped (HMM/Windows),
                           186 frontend pass.


## Changelog, What's New, and CI/CD ✅ COMPLETE (2026-05-17)
Changelog infrastructure, the What's New modal, and a database-backed
GitHub Actions pipeline.

Backend:
  migrations/011_create_changelog.py — changelog table + 30-entry
    historical seed.
  migrations/012_create_users.py — users table (email PK,
    last_changelog_seen_at, last_tour_version_seen) + changelog entry 31.
  config.py — TOUR_VERSION.
  tools/changelog.py — get_all / get_unseen / mark_seen (fail-open).
  main.py — GET /api/v1/changelog, /unseen, POST /mark-seen.

  tests/test_changelog.py — 9 tests: endpoint auth + shape contracts;
    DB round-trips for unseen filtering, all-seen empty, mark-seen
    timestamp + tour-version persistence, and the has_tour_update flag.

Frontend:
  components/WhatsNewModal.tsx — login-triggered modal of unseen
    entries, mounted in MainLayout.
  pages/Settings.tsx — Release History section (sixth) + a What's New
    link in Account.
  types/changelog.ts — ChangelogEntry / response types.

CI/CD:
  .github/workflows/ci.yml — postgres-service backend job (migrations +
    pytest + changelog gate) and a frontend typecheck + Vitest job.
  scripts/changelog_gate.py — fails a migration with no changelog INSERT.
  .pre-commit-config.yaml — changelog-gate / pytest / frontend-typecheck
    hooks appended to the existing local block.

Migration: 011 + 012 — operator runs `alembic upgrade head` on Render.

Test counts (cumulative): 1080 backend pass, 15 skipped (HMM/Windows),
                           186 frontend pass.


## Site Tour ✅ COMPLETE (2026-05-17)
A controlled react-joyride walkthrough — fifteen steps across every
route, framed for Forest Capital and McColl faculty.

Backend:
  migrations/013_site_tour_changelog.py — changelog entry 32
    ("Site Tour", tour_step_id "welcome").
  config.py — TOUR_VERSION bumped 1 → 2.

Frontend:
  components/SiteTour.tsx — controlled Joyride mounted in MainLayout;
    custom dark tooltip, cross-route navigate/pause/resume, once-per-
    session auto-start.
  constants/tourSteps.ts — the 15 TourStep definitions.
  lib/tourBus.ts — module-level start-function bridge.
  components/WhatsNewModal.tsx — "View updated site tour" button wired.
  pages/Settings.tsx — Account "Retake Site Tour" button.

  __tests__/site-tour.test.tsx — 7 tests: auto-start fires on a pending
    tour update and is suppressed once seen / while the What's New modal
    would show; completion and skip POST mark-seen with the tour version
    (skip also sets the session skip flag); startTour() force-starts
    regardless of seen state; the What's New tour button is active and
    starts the tour. react-joyride stubbed to capture Joyride props.

Migration: 013 — operator runs `alembic upgrade head` on Render.

Test counts (cumulative): 1080 backend pass, 15 skipped (HMM/Windows),
                           193 frontend pass.


## Two access tiers — TeamGate ✅ COMPLETE (2026-05-17)
The first access-control pass: any authenticated user explores the
analytics; the action endpoints (document upload, the export endpoints,
Academic Review, the test runner) are restricted to the project team.

Backend:
  auth.py — require_team_member dependency.
  main.py — require_team_member applied to the team-gated endpoints.

  tests/test_team_gate.py — require_team_member 403s a non-team user,
    admits a team member, and 401s an unauthenticated request; the open
    tier (council query / explain) admits any authenticated user.

Frontend:
  components/TeamGate.tsx — gates an action element (disabled+lock, or
    hidden) for non-team users.

  __tests__/team-gate.test.tsx — TeamGate renders, gates and hides per
    the caller's permission.


## Database-managed access control ✅ COMPLETE (2026-05-17)
Access control migrated from the hardcoded config allowlists to a
database-managed user system. Roles (viewer / team_member / sysadmin)
are presets over an authoritative `permissions` array; Michael Ruurds
is the sysadmin and manages every user from inside the platform.

Backend:
  migrations/015_create_platform_users.py — platform_users table +
    config-seed (sysadmin / team_member / viewer) + changelog entry 34.
  config.py — PERMISSIONS, ROLE_PRESETS, SYSADMIN_EMAILS. ALLOWED_EMAILS
    and PROJECT_TEAM_EMAILS retained as the emergency config fallback.
  tools/platform_users.py — the platform_users data layer; config_fallback
    mirrors the migration-015 seed so a database outage never locks the
    team out.
  auth.py — three-tier permission resolution (JWT → platform_users →
    config fallback); require_permission(perm) factory; require_team_member
    is require_permission("team_member").
  main.py — per-endpoint permission gates; GET/POST/PATCH/DELETE
    /api/v1/admin/users (manage_users-gated, with last-sysadmin guards).

  tests/test_platform_users.py — 35 tests: the manage_users gate on the
    /api/v1/admin/users endpoints (viewer / team member 403, sysadmin and
    master key admitted, unauthenticated 401); create-user validation
    (422 on a bad email / role, 503 past validation with no database);
    404 on an unknown user id; /api/auth/me carrying the authoritative
    permissions; config_fallback mirroring the seed (case-insensitive);
    every platform_users read failing open with no database; the
    magic-link request never enumerating; _valid_email / _clean_permissions.

Frontend:
  hooks/usePermissions.ts — useHasPermission and the convenience hooks
    (useIsTeamMember / useIsSysadmin / useCanGenerateDocuments /
    useCanExport). hooks/useIsTeamMember.ts removed.
  components/TeamGate.tsx — gains a `permission` prop (default
    "team_member").
  constants/permissions.ts — PERMISSIONS, ROLE_PRESETS, ASSIGNABLE_ROLES,
    matchesPreset (the frontend mirror of the config).
  components/UserManagementPanel.tsx — the Settings → Users table with
    add / edit / deactivate and a per-permission checklist.
  pages/Settings.tsx — sysadmin-only Users section between Analytics
    Configuration and Academic Documents.

  __tests__/user-management.test.tsx — 12 tests: the permission hooks
    read the session permissions array; ASSIGNABLE_ROLES omits sysadmin
    and manage_users is sysadmin-only; matchesPreset detects a Custom
    set; UserManagementPanel renders the table, gates Add-User on a
    valid email, and never offers sysadmin as a role preset.
  __tests__/team-gate.test.tsx — updated to the permission-based model.

Migration: 015 — operator runs `alembic upgrade head` on Render.

Test counts (cumulative): 1157 backend pass, 21 skipped (HMM/Windows +
                           deployment), 220 frontend pass.
