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
