# Forest Capital Portfolio Intelligence System

MSFA FNA 670 Graduate Practicum — Queens University of Charlotte  
Partner: Forest Capital

## Research Question

Does diversification across equities and fixed income — via static or dynamic asset allocation — improve risk-adjusted performance relative to a 100% equity benchmark?

## Architecture

Six AI agents (Claude Opus CIO, four Claude Sonnet specialists, Google Gemini Pro independent analyst) plus a QA agent that audits all results before presentation. A full cross-validation suite and statistical testing framework enforces p < 0.005 significance throughout.

## Sprint History

| Sprint | Status | What was built |
|--------|--------|---------------|
| 1 | ✅ Complete | Frontend shell (React/TypeScript), skeleton FastAPI backend, magic-link auth (dev mode), mock data for all 10 strategies, GitHub Actions CI/CD (3 jobs) |
| 2 | ✅ Complete | Excel data loader (`load_provided_data`), serial-date conversion, BND/BAMLHYH daily→monthly aggregation, SPY/VIX/DGS2/FF supplemental fetches, equity cross-validation, data provenance (`provenance.json`), PostgreSQL migrations (4 tables), `/api/v1/provenance` endpoint |
| 3 | ✅ Complete | All 10 strategies with real metrics, full 12-test statistical suite (DSR, PSR, SPA), 7 CV methods including CPCV C(6,2)=15 paths, HMM 3-state regime detection, LQD bridge extending IG coverage to July 2002, `run_all_strategies` returning `dict[str, dict]`, numerical accuracy tests, splice integrity tests |
| 4 | ✅ Complete | All 8 AI agents live (Equity, Fixed Income, Risk, Quant, Gemini, CIO, QA, Explainer), council WebSocket streaming, scope guard, academic writer scaffold, production deployment (Render + Vercel), magic-link email via SendGrid |
| 5 | ✅ Complete | PostgreSQL cache layer (strategy + regime signals), `FRED_TIMEOUT_SECONDS=60`, incremental data ingestion, `ChartExportButton` (PNG/SVG), `TableExportButton` (CSV), `SanityCheckPanel` (10-check data integrity), 5 new backend test files (75 new tests), 3 new frontend test files (31 new tests) |
| 6 | ✅ Complete | **Phase 1:** 12 Statistical-Evidence + Regime-Analysis charts with `/api/v1/charts/data` bundle endpoint. **Phase 2:** Council Debate narrative fix, navigation persistence via Zustand stores, chart UX standardisation. **Phase 3:** Tiered QA system (Tier 1 deterministic + Tier 2 Sonnet background + Tier 3 Opus manual) with `qa_results_cache` table, nav-bar status badge, Present-mode gate (≥WARN + <48h + hash match). **Phase 4:** Commentary mode frontend bridge — `glossaryStore`, `ExplainableText` three-level wrapper, `ChartCommentStrip` (always-visible Sources line, mode-conditional narrative), `LearnModeBanner` + `LearnModeToggle`. **Phase 5:** Reports screen + Priority 1 (June 3) midpoint paper generator with `python-docx`, Academic Writer prose, AI DRAFT banner. **Phase 6:** Storyboard Editor + Presentation Script Writer. 15-slide AI draft (`POST /api/documents/storyboard/draft`) with Academic Writer speaker-note enrichment. Full CRUD via `tools/documents_cache.py` against `documents` / `document_versions` / `document_drafts` (migration 004). React `StoryboardEditor` page with drag-to-reorder, slide editor panel, running timing bar (GREEN ≤20m, AMBER ≤21m, RED >21m), Version History sidebar (named + auto-saves), and 30s debounced auto-save. `python-pptx` deck generator; `script_writer.py` produces full-team / Molly / Michael / Bob / rehearsal scripts at 130 wpm; Q&A doc with 18 questions across Forest Capital / MSFA Board / AI usage. Gemini 1.5 Pro assistant panel with paragraph-level red/green diff display, multi-turn conversation, scope guard, and mock fallback. Grok contrarian analyst remains alongside Gemini independent analyst. **Phase 8 (Explainer slice):** stable term IDs on the 7 Dashboard strategy-table headers + 5 Significance Journey Matrix gate headers, regime-badge label, and the 2022 Equity-Bond Correlation Breakdown banner; `QAAuditPanel` now calls `loadQA(audit.items)` on audit load and renders the four-section glossary narrative (what / why / failure-meaning / how-tested) inside each expanded checklist row. **Phase 9 (Academic Advisor — Agent 10):** Sonnet + Anthropic server-side `web_search_20250305` tool with citation integrity enforced at the agent boundary — every URL the model emits is filtered against the tool's actual returned URLs before reaching the frontend (`_filter_to_verified`), so no fabricated citation can survive. Three endpoints (`/api/advisor/analyse`, `/verify-finding`, `/citations`) with grade-aware rubric (Midpoint 10%, Brief 20%, Appendix 35%, Presentation 35%). Gold-accented `AdvisorPanel` mounted globally in `MainLayout` (floating button), hidden in Present mode; Reports screen wires a per-deliverable "Get Advisor Feedback" button using the controlled-open API to pin the panel to the right context. Session-cached `advisorStore` keyed by deliverable+query to avoid re-burning the ~$0.04-0.06 web-search cost on panel re-open. Fixed `test_provenance_json_source_types_are_valid` — added `ken_french_direct` to the valid source-type set for the FF factors direct HTTP fetcher. **Phase 9b (excerpt provenance):** added Anthropic `web_fetch_20250910` to the advisor's tools list and a second integrity gate in `_filter_to_verified` — every citation now carries an `excerpt` field that is set to a 2-3 sentence passage *only* when the URL was actually fetched (URL must appear in the parsed `web_fetch_tool_result` blocks). Failed fetches (paywall, 404, timeout) → excerpt = `null` → frontend shows "Excerpt unavailable — click to verify directly" on hover. `AdvisorPanel.CitationItem` renders a custom hover tooltip with gold accent showing the excerpt or fallback message, alongside a native `title` attribute for a11y / no-JS fallback, with the citation link opening in a new tab (`target="_blank" rel="noopener"`). **Phase 11 (Bob's section editor + remaining Explainer wiring):** `POST /api/reports/analytical-appendix` (HTML, 6 sections with Table 1 strategy comparison + Table 2 provenance registry auto-injected, AI DRAFT banner sticky in screen / page-break-avoiding in print) and `POST /api/reports/executive-brief-template` (.docx, 6 sections incl. 5 captioned chart placeholders) both available in the Reports manifest. `GET /api/agents/personas` surfaces every council agent's verbatim system prompt + model + module path. Bob's three deliverables can now be opened in a new `SectionEditor` page (`/reports/document/:id`) — three new endpoints back this flow: `POST /api/documents/section-doc/draft` creates a section-structured document with `ai_draft` (immutable) and `content` (Bob's edits) per section, `POST /api/documents/:id/sections/:section_id/regenerate` re-runs Academic Writer for one section only, `POST /api/documents/:id/export` compiles Bob's current draft back into .docx or HTML. Section editor UI: left section list with live word counts + total, middle editor surface per section with View AI Draft / Regenerate AI / Revert (with confirmation dialog), right Version History panel (collapsible, named + auto-saves, restore creates a new version row), Save Version dialog. AI DRAFT banner permanent at top, never dismissable. `PersonaModal` opens from a "View system prompt" link on every `AgentCard` in `CouncilDebate` — three tabs: PROMPT (verbatim with copy button), PLAIN ENGLISH (Explainer-generated via `glossaryStore.loadPersona`, cached per agent), THIS SESSION (agent's actual contribution to the current council run, falls back to passed `sessionContent` when Explainer is offline). Escape key + backdrop click + X all close. `StrategyCard` metric labels (Sharpe Ratio, CAGR, Max Drawdown, Volatility, CV Stability Score, Tier 1 Significance Tests, DSR / PSR / SPA p-value) wrapped in `ExplainableText` with stable term IDs matching the Dashboard table headers — same glossary entry hits whether the click is on the dashboard or the strategy card. **Phase 12 (Advisor input validation):** Get Advisor Feedback button now disabled when the query is empty or whitespace-only — prevents firing the ~$0.04-0.06 web-search call against an empty string. Submit handler trims the query before sending, so leading/trailing whitespace doesn't reach the backend. Placeholder text updated to "Ask about your findings, deliverables, or what to focus on..." to nudge the user toward concrete questions. The previous deliverable-specific `DEFAULT_QUERIES` fallback is removed — silent placeholders were producing low-quality LLM responses when users submitted blank inputs. **Phase 13 (Render hotfix — four bugs):** (1) **migration 006** widens `regime_signals_cache.hmm_regime` from INTEGER to VARCHAR(20) — the regime detector emits string labels (`'BULL'`, `'BEAR'`, `'TRANSITION'`) that the previous INTEGER column rejected with `InvalidTextRepresentation`. Upgrade casts existing rows via `postgresql_using="hmm_regime::text"`; downgrade nulls non-numeric rows first. (2) **Explainer Grok 400** — aligned `_call_grok` timeout (30s) and body shape with `agents/contrarian_analyst.py` literally; on 4xx the response body is now logged at `explainer_grok_http_error.body_preview[:500]` so future xAI spec drifts surface in Render logs immediately rather than as a bare "400 Bad Request". (3) **Haiku fallback truncated JSON** — bumped fallback `max_tokens` floor to `HAIKU_FALLBACK_MAX_TOKENS = 2000` (was 800 — `explain_qa` covered 30 items and hit the cap mid-string), and routed all five `explain_*` methods through new `_safe_json_parse` helper that tolerates fences, prose-wrapped JSON, truncated strings, and non-string inputs without raising. (4) **Incremental update KeyError** — `_append_incremental_daily` previously called `vix_series.set_index("date")["value"]`, but `_fred_fetch` returns a DataFrame with DATE as the **index** and the value column named after the series_id (`VIXCLS`, `DGS2`). Fixed to `vix_series.iloc[:, 0]` so the Series keeps its date index. |

**After Sprint 6, development moved to a Kanban board** (the board of record is in `CLAUDE.md`). The post-Sprint-6 feature stream — the academic analytics layer and Analytics page, the `/settings` page, Academic Review, Team Activity, the changelog / What's New modal, the site tour, the generator-evaluator harness, the guided UAT test runner, document upload + generation, database-managed access control, full mobile-responsive support, automated feedback triage, and the statistical audit system — is documented in the dedicated sections below and in `CLAUDE.md`.

## Test Counts (current)

| Layer | Tests | Notes |
|-------|-------|-------|
| Backend (pytest) | 1424 passed, 15 skipped | HMM tests skip on Windows (hmmlearn requires C++ build tools; passes in CI on Linux). +9 tests for the changelog feature (`test_changelog.py`): endpoint auth/shape contracts, and DB round-trips — unseen filters by last-seen timestamp, is empty when all seen, mark-seen updates the timestamp and the tour version, and has_tour_update flips once the tour version is recorded. +12 tests for the generator-evaluator harness (`test_harness.py`): unit tests for accept-on-first-pass, sub-threshold retry with feedback injection, best-not-last selection, evaluator-parse-failure passthrough (8.0), retry-generator-exception fallback, first-attempt re-raise, and evaluator-exception passthrough; metrics capture/aggregation; and integration checks that the council and academic-review API shapes are unchanged and the arbiter verdict still has all five rubric sections. +5 tests for the contextual explainer (`test_explainer_endpoint.py`): `POST /api/council/explain` returns 200 with a valid metric + auth, 401 without auth, 422 with no metric; `explain` is a registered interaction type; the explain interaction logs for a team email and is gated out for a non-team email. +28 tests for the Team Activity feature (`test_activity.py`): identity resolution and the team-email allowlist filter, webhook signature validation and push-payload parsing, the activity endpoints' contracts (events always 200, council unaffected by its logging hook, webhook 401 on a bad signature), DB round-trips for the session-event / agent-interaction inserts and the commit upsert-on-SHA, timeline sort + `session_type` filtering, the per-member summary, and the commit-11b agent-context injection (the team-activity block assembling with multiple users, the multi-user-gated peer dimension and arbiter section, testing sessions excluded from agent context). DB round-trip tests run against a live database and skip cleanly in CI. +24 tests for the combined analytics enhancement pass: `test_momentum_factor.py` (6) covers the Ken French momentum-factor direct-HTTP fetch and the `mom`-column backfill of `ff_factors_monthly`; `test_analytics.py` (+10) covers the Carhart four-factor regression (unit MOM-beta recovery, three-factor fallback when MOM is absent or NULL), cumulative returns starting at 1.0, and the benchmark's 0.0 excess return / null information ratio; `test_strategy_enhancements.py` (8) covers true portfolio turnover (`sum(|Δw|)/2` per rebalance, non-negative, ~0 for fixed-weight statics) and the parameter-sensitivity sweep across all four dynamic strategies. Factor-loading regressions are now Carhart four-factor (MKT-RF / SMB / HML / MOM); the Dashboard turnover column shows genuine weight-change turnover. +6 tests for the Academic Review council endpoint (`POST /api/council/academic-review`): context assembly with documents and with missing types, peer fan-out covering every non-arbiter agent, the arbiter prompt carrying all peer responses, and the SSE stream emitting `peer_responses` before `arbiter_chunk`. The academic-document upload endpoint accepts `.pdf` and `.md` (extension-authoritative — `.md` is read directly as UTF-8, bypassing pypdf; any other extension is a 400). `extract_document_text()` is now PDF-only: its dead non-PDF branch and the three obsolete tests that exercised it were removed (net −2 vs the prior 998). +5 tests for the `/settings` page backend: `GET /api/v1/admin/data-status` (per-table row count, date range, green/amber/red staleness) and `GET /api/v1/analytics/config` (risk-free rate), plus the three new `academic_documents` types (`midpoint_draft`, `presentation_slides`, `presentation_script`). +4 tests in `test_optimizer.py` for the Efficient Frontier rewrite: `efficient_frontier()` now does a target-return sweep (minimise variance s.t. a fixed target return) over the full long-only space `[0,1]` instead of a risk-aversion sweep capped at `MAX_WEIGHT=0.40` — the 0.40 cap collapsed a 3-asset frontier into a near-straight sliver. Tests pin the hyperbola shape, the min-variance→max-return span, long-only weights, and that the tangency point beats every single asset. +14 tests in `test_academic_documents.py` for the document-upload feature: PDF/text extraction (`pypdf`), academic-context formatting and injection, migration 008 (`academic_documents` table), and upload-endpoint validation. Uploaded documents are injected into every agent's system context. +13 tests in `test_analytics.py` for the academic analytics layer (`tools/analytics.py`): summary statistics, 12-month rolling correlation with the 2022 regime break, regime-conditional performance (pre/post-2022 split), drawdown comparison, and Fama-French factor loadings (since extended to the Carhart four-factor model) — all served by `GET /api/v1/analytics/academic` and surfaced on the new Analytics page. The Efficient Frontier computes from `market_data_monthly`'s equity/IG/HY monthly returns — the same 3-asset universe the 10 strategies use — via `cache.get_monthly_returns()`, so the curve and the strategy scatter share one universe and annualization (`efficient_frontier(..., periods_per_year=12)`); no yfinance dependency. +1 test pinning the `periods_per_year` annualization. +8 tests in `test_optimizer.py` for the NaN/Inf guard: `_returns_have_finite_moments()` rejects empty / single-row / all-NaN-column return frames before the cvxpy/scipy solver, so a ticker yfinance fails to fetch falls back to equal weight with one log line instead of a "Problem data contains NaN or Inf" crash on every frontier point. `/api/optimize/weights` also drops all-NaN columns before `dropna()` so one dead ticker no longer wipes every row. Grok model upgraded `grok-3-mini` → `grok-4.3` (both grok-3-mini and grok-4 retired on OpenRouter, 404). Earlier: +6 Efficient Frontier tests — `/api/optimize/weights` returns the structured `{frontier_points, portfolio_points, max_sharpe_point, min_variance_point}` payload, `portfolio_points` read from the latest `strategy_results_cache` row (no `get_full_history()` recompute). +11 tests for the Academic Export Package (`test_export_package.py`): `POST /api/v1/export/package` returns a valid attachment-headed ZIP containing the uploaded charts/tables with bytes preserved, the curated `metadata/` files and `README.txt`, falls back gracefully on absent metadata, still produces a ZIP on an empty upload, and requires auth; a DB round-trip confirms `export` is an accepted interaction type that logs for a team email and is gated out for a non-team email. +10 tests for academic document generation (`test_document_generation.py`): `POST /api/v1/export/{midpoint-paper,executive-brief,presentation-deck}` each returns a valid, parseable `.docx` / `.pptx` (16 slides) with the right attachment header and section headings, degrades to `[DATA PENDING]` markers when the analytics caches are cold and no academic documents are stored, and requires auth; a DB round-trip confirms a document-generation run logs an `export` interaction for a team email and is gated out for a non-team email. +16 tests for the guided UAT test runner (`test_test_runner.py`): contract tests for the `/api/v1/testing/*` endpoints — the fail-open quality gate, team/admin gating, auth, result validation, and screenshot path storage (paths not BLOBs; invalid input degrades gracefully) — and DB round-trip tests for the persistence layer (insert-then-upsert with `overridden`, per-user isolation, summary counts, failure resolution flipping a step back to pending, feedback AI categorisation, free-form feedback). +7 tests for the two access tiers (`test_team_gate.py`): `require_team_member` 403s a non-team authenticated user and admits a team member, an unauthenticated request is 401, the open tier (council/explain, council/query) is never team-gated, and the gated tier (academic-review) 403s non-team. +35 tests for database-managed access control (`test_platform_users.py`): the `manage_users` gate on the `/api/v1/admin/users` endpoints (a viewer and a team member are 403, the sysadmin and the master key admitted, unauthenticated 401), create-user validation (422 on a bad email/role, 503 past validation with no database), 404 on an unknown user id, `/api/auth/me` carrying the authoritative permissions array, `config_fallback` mirroring the migration-015 seed case-insensitively, every `platform_users` read failing open with no database, the magic-link request never enumerating, and the `_valid_email` / `_clean_permissions` helpers. +20 tests for the automated feedback triage system (`test_triage.py`): the sysadmin gate on the three `/api/v1/testing/triage` endpoints; `run_triage` returning early on an empty backlog and skipping a concurrent run; the five-section triage report generation and the high-severity immediate set; the threshold trigger firing at exactly 5 items (and not below), the test-pass trigger firing unconditionally, and both blocked by a concurrent run; the `run_triage` orchestration storing the correct item count and status with the GitHub step failing open; and GitHub issue / label creation failing open with no token. +27 tests for the statistical audit system (`test_audit.py`): the sysadmin gate on the five `/api/v1/audit` endpoints; the assembler (test-env unavailable, the formula specs covering every metric, the documented two-regime annualisation spec, a deterministic payload hash); Layer 1 (clean data passes, a >50% monthly return and a broken weight sum are caught, absent weights skip honestly); Layer 3 (the benchmark-IR null/numeric check, a Sharpe-CI inversion caught via an injected strategy cache); and the audit engine (a concurrent run returns `already_running`, the export report carries every section including COMPUTATION REGIMES, `make_finding` carries every field, and `classify_discrepancy`'s tolerance bands). +12 tests for smart audit caching (`test_audit.py` `TestSmartAuditCaching`): `current_data_hash` consistency / row-count sensitivity / empty-on-unavailable; `is_audit_current` match, no-prior-run and per-layer hash mismatch; `run_full_audit` idempotent skip on current data and proceed on stale; `trigger_audit_async` background spawn; the demo-`reason` alias on `/api/v1/audit/run`; the cache-invalidate auto-trigger; and the latest-run currency fields. The weight-constraint test now feeds the columnar persisted `weight_schedule` shape — the backtester persists per-rebalance weights, so Layer 1's weight check runs in full rather than skipping. +15 tests for AI token cost tracking (`test_token_cost.py`, migration 020): `config.calculate_cost` pricing arithmetic (known/unknown model, dated-string prefix match, None/non-numeric guards, zero-token = zero cost), the `agents/usage.py` ContextVar accumulator (no-op without a capture, total aggregation, multi-model → `"multiple"`, the per-agent breakdown, bad counts dropped not raised), the `GET /api/v1/activity/cost-summary` contract (auth + shape), and a DB round-trip asserting logged token columns surface in `get_cost_summary` by member and by interaction type. Every AI call wrapper now records token usage; the council and academic-review endpoints log a per-interaction cost, the Team Activity view carries a cost panel, and Settings → Users an AI-cost column. +10 tests for the global QA-run concurrency guard (`test_qa_guard.py`): the guard reports the in-progress kind (methodology flag, or a running statistical audit) and fails open on a database error; every run-triggering endpoint (`/api/v1/audit/run`, `/api/qa/audit`, `/api/v1/qa/run`, `/api/v1/qa/full-review`) returns 409 with a clear message while a QA run is in progress; a run proceeds when the platform is free; and the guard lifts cleanly when the active run completes. +10 tests for the in-platform document editor (`test_editor_drafts.py`, migration 021): endpoint auth gating, the `editor_content` builders (midpoint → a TipTap doc with four section headings and two `[[BOB]]` callouts; deck → sixteen slides with empty speaker notes), a full CRUD round-trip (create computes word count, auto-save creates no version, a named version saves and restores, soft delete then 404), create setting `is_current` and unsetting the prior draft, midpoint generation creating a draft and returning `X-Draft-Id`, and Academic Review overlaying the editor draft onto the documents-by-type map. The reusable `clean_platform_users` / `clean_editor_drafts` conftest fixtures snapshot-and-delete rows a test creates so a create test runs repeatedly without a duplicate collision. +21 tests for the canvas presentation editor (`test_charts.py`): the chart render endpoints (`/api/v1/charts/available` + `/render/{key}` — auth, the five-chart list, a PNG response, 404 on an unknown key, the 5-minute cache); `chart_render` units (known-chart set, a PNG from `render_chart_png`, dark→light fallback); migration 022's slide-card↔canvas conversion (`_slide_to_canvas` field mapping, the data-point element omitted when empty, idempotence, `_slide_to_cards` downgrade, a card→canvas→card round-trip); and the canvas PPTX export (the EMU mapping scaling 960×540 onto a 10×5.625in slide, `build_editor_pptx` rendering a canvas draft with a chart PNG embedded, an empty deck). +20 tests for the presentation script writer (`test_script_writer.py`): script generation covers all 16 slides and carries the deck's speaker assignments, the prompt builder includes the executive brief when present, `script_to_tiptap` classifies every node kind, the master DOCX contains every slide while a per-speaker DOCX contains only that speaker's slides (with slide numbers/titles), and the generate/export endpoint contracts (401/403/422/404) plus DB round-trips. |
| Frontend (Vitest) | 358 passed | 12 for the mobile-responsive implementation (`mobile-responsive.test.tsx`): the hamburger nav drawer renders below lg and opens/closes on the hamburger, a nav-item selection and an overlay click; the hamburger is `lg:hidden` and the horizontal nav `hidden lg:flex`; the Dashboard strategy table is wrapped in `overflow-x-auto` with a sticky-left Strategy column; ExplainerPanel carries the mobile bottom-sheet anchoring; the InfoIcon and nav hamburger meet the 44px touch-target minimum. 6 for the access tiers (`team-gate.test.tsx`, rewritten for the permission model): TeamGate renders children for a permitted user, the muted+locked disabled state for a user without the permission, hides the element when `showDisabled` is false, honours a specific `permission` prop, and treats an unauthenticated session as holding no permission. +12 for database-managed access control (`user-management.test.tsx`): the permission hooks (`useHasPermission` / `useIsTeamMember` / `useIsSysadmin`) read the session permissions array, the permissions constants (`ASSIGNABLE_ROLES` omits sysadmin, `manage_users` is sysadmin-only), `matchesPreset` detects a Custom set, and `UserManagementPanel` renders the user table, gates Add-User on a valid email, and never offers sysadmin as a role preset. +8 for the guided UAT test runner (`test-runner.test.tsx`): the four code-versioned test scripts (shape, unique step ids, `scriptForEmail` mapping) and the submission panel's required-field gating. +3 for SessionContext (`session.test.tsx`) and +5 for the explainer tooltips (`explainer.test.tsx`): every `EXPLAINER_TOOLTIPS` key has non-empty content, `InfoIcon` renders the ⓘ button / nothing for an unknown key / the static tooltip on hover, and `ExplainerPanel` calls `POST /api/council/explain` on mount. +7 for the site tour (`site-tour.test.tsx`): auto-start fires when a tour update is pending and is suppressed once the version is seen or while the What's New modal would show, completion and skip both POST mark-seen with the tour version, `startTour()` force-starts regardless of seen state, and the What's New "View updated site tour" button is active and starts the tour. 25 for the in-platform document editor (`editor.test.tsx`): `[[VERIFY]]` / `[[BOB]]` marker detection (`findMarkers` / `countMarkers`), the `transformBobMarkers` / `docToText` projection, `BobCalloutPanel` / `VerifyPopup`, `RichTextEditor` marker rendering, the `canvasSlideStatus` deck-slide completion rule (complete / in-progress / not-started), and `PresentationPreview` rendering the canvas slide shape. +13 for the canvas presentation editor (`canvas-editor.test.tsx`, react-konva mocked for jsdom): the `canvasSlide` helpers (`newTextElement` / `newChartElement` / `konvaFontStyle` / `deckToText`), `CanvasSlideEditor` (toolbar, add a text element, open the chart picker, AI Layout always vs AI Copy only with a text element selected, speaker-notes editing, an AI Layout review-and-apply round-trip), and `ChartPicker` (lists the available charts, selects one, closes). `react-konva` is mocked in the Vitest setup (`setup.ts`) — konva's Node build needs the native `canvas` package, absent under jsdom. +11 for the presentation script writer (`script-writer.test.tsx`): the navigator speaker badge (assigned/unassigned, suggestions) and read-only speaker line, the delivery-time metric line and tone, the MOLLY script callout, the canvas presenter label, and `DocumentEditor` integration — Generate Script disabled/enabled by speaker assignment, the master and per-speaker export buttons, the delivery-time estimate. |
| E2E (Playwright) | Non-blocking | Pointed at live Render + Vercel URLs; `continue-on-error: true` removed once CI green |

Run backend tests:
```bash
cd tests
pytest -v
```

Run frontend tests:
```bash
cd frontend
npm run test
```

## Tech Stack

**Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy (async), asyncpg, Alembic, structlog, slowapi  
**Compute:** pandas, numpy, scipy, cvxpy (CLARABEL solver), hmmlearn, statsmodels, arch  
**Agents:** Anthropic SDK (Claude Opus 4.7 + Sonnet 4.6 + Haiku 4.5 — the project's `OPUS_MODEL` / `SONNET_MODEL` constants are `claude-opus-4-7` / `claude-sonnet-4-6`, not dated strings), Google GenerativeAI SDK (Gemini 1.5 Pro), xAI HTTP API (Grok 4.3 — Contrarian Analyst), Anthropic server-side `web_search_20250305` + `web_fetch_20250910` tools (Academic Advisor — citation integrity with passage-level excerpt provenance)  
**Frontend:** React 18, TypeScript 5 (strict), Vite, TailwindCSS, Recharts, Zustand, React Query  
**Database:** PostgreSQL (asyncpg), 28 tables: `data_series_registry`, `market_data_monthly`, `market_data_daily`, `data_validation_log`, `strategy_results_cache`, `regime_signals_cache`, `auth_attempts`, `used_magic_tokens`, `qa_results_cache`, `documents` / `document_versions` / `document_drafts` (Storyboard Editor), `ff_factors_monthly` (direct Ken French fetch — `mom` column added migration 009 for the Carhart fourth factor), `academic_documents` (migration 008 — agent-context uploads), `session_events` / `agent_interactions` / `commit_activity` (migration 010 — Team Activity), `changelog` (migration 011), `users` (migration 012 — per-user changelog/tour state), `test_results` / `test_feedback` (migration 014 — guided UAT test runner), `platform_users` (migration 015 — database-managed access control), `triage_reports` (migration 016 — automated feedback triage), `audit_runs` / `audit_findings` (migration 017 — statistical audit), `editor_drafts` / `editor_draft_versions` (migration 021 — in-platform document editor). Migration 022 is a data migration (no new table) — it converts every `presentation_deck` draft's `content_json` to the free-form canvas element schema.  
**Auth:** Itsdangerous (signed magic-link tokens), JWT sessions, SendGrid email delivery  
**CI/CD:** GitHub Actions (backend pytest + frontend Vitest + E2E Playwright)

## Data Sources

All data follows a strict hierarchy — Excel is authoritative; external fetches fill gaps only:

| Source | What it provides | Series |
|--------|-----------------|--------|
| Excel (Dr. Panttser, FNA 670) | Equity monthly returns, BND daily OHLCV, BAMLHYH total return index, HY/IG effective yields, DGS10, DTB3, GDP, P/E | Primary return series |
| yfinance | SPY daily (equity signal for momentum/vol models), LQD daily 2002–2007 (IG bridge) | `equity_daily_spy`, `ig_lqd_bridge` |
| FRED API | VIX (VIXCLS), 2Y Treasury (DGS2) | `vix_daily`, `dgs2_daily` |
| Ken French data library | Fama-French factors (Mkt-RF, SMB, HML, RF) | `ff_factors` |
| Constants | Black-Litterman equilibrium prior (60/30/10) | `bl_market_cap_priors` |

**LQD bridge:** BND starts April 2007; LQD (iShares IG Corporate Bond ETF) extends IG coverage back to July 2002, adding ~57 months for a total of ~282 aligned monthly observations (vs ~225 without the bridge).

## Settings Page

`/settings` is a single scrollable page reached from the nav-bar gear icon. Its sections depend on the signed-in user's permissions:

1. **Organisation** — McColl / Forest Capital brand switcher (drives the header branding).
2. **Data and Study Period** — read-only data-table status from `GET /api/v1/admin/data-status`: per-table row count, date range, last-updated timestamp and a green/amber/red staleness pill (green = newest data within 15 days of today, amber 15–30, red > 30), plus a study-period summary line.
3. **Analytics Configuration** — the risk-free rate from `GET /api/v1/analytics/config` (mean monthly FRED DTB3 ×12 — the same value the efficient frontier and analytics layer use), shown read-only.
4. **Users** *(sysadmin only)* — the `UserManagementPanel`: add, edit and deactivate platform users and tune each one's permission checklist. See *Access Control* below.
5. **Academic Documents** — the `AcademicDocumentsPanel` upload UI (moved here from the Reports view). Accepts **PDF and Markdown (.md)**; file type is decided by extension, authoritative over MIME type — PDFs go through pypdf, `.md` files are read directly as UTF-8. Uploaded documents are injected into every AI agent's system context. Deep-linkable via `/settings#academic-documents`.
6. **Account** — signed-in email, the Testing Mode toggle and Start Test Pass control, the Retake Site Tour button, and Sign out.
7. **Release History** — every changelog entry with its academic rationale, newest first; unseen entries carry a "New" badge.
8. **Test Results** *(project team)* — the signed-in tester's guided-UAT attestations per script, with re-test and an attestation CSV export.
9. **Test Administration** *(sysadmin only)* — the all-testers Failure Reports list, the AI-categorised Feedback Backlog, and the **Triage Reports** block (see *Automated Triage* below).

**Staleness pills — expected behaviour:** `market_data_monthly` and `ff_factors_monthly` show **red** pills by design. The dataset is intentionally locked at December 2025, so the newest data is several months behind "today". The pill reports recency-vs-today; a red pill here is the dataset's deliberate end date, not a pipeline failure.

## Navigation

The top-nav order is **Dashboard → Analytics → Statistical Evidence → Regime Analysis → Council → QA Audit → Reports** — Analytics sits second, directly after Dashboard. Settings is the gear icon on the right of the nav ribbon.

## Academic Review

`POST /api/council/academic-review` convenes the council to evaluate the project's academic readiness. No request body — context is assembled server-side (analytics inventory + uploaded academic documents + a team-activity summary). Every council agent except the academic advisor answers a stock review question in parallel (the **peer fan-out**, on `claude-sonnet-4-6`); the **academic advisor then arbitrates** on `claude-opus-4-7`, synthesising a five-section, rubric-mapped verdict (each section rated Strong / Developing / Needs Work). The response is a Server-Sent Events stream — one `peer_responses` frame, then streamed `arbiter_chunk` frames, then `data: [DONE]`. The Council screen's Academic Review button renders the verdict section by section as it streams, with peer reviews in a collapsible accordion.

Model strings are the project constants `claude-sonnet-4-6` / `claude-opus-4-7`, never dated strings — the project moved off `claude-opus-4` because it retires 2026-06-15.

## Team Activity

The Team Activity section on the Reports screen is the objective record of how the team engaged with the platform — the evidence behind the Roles & Division of Labor deliverable and the AI-use narrative for the July 1 presentation. It interleaves three sources into one timeline: **commits** (from the GitHub push webhook + manual sync), **agent interactions** (council runs, academic reviews, QA audits, document uploads — logged non-blocking by the agent endpoints), and **UI telemetry** (page views, exports, logins — batched from the frontend every 30 seconds). A summary panel and three charts (weekly activity, contribution split, agent engagement) sit above the timeline; a Presentation View shows just the charts full-width for the demo.

Only the three project-team accounts (`PROJECT_TEAM_EMAILS`) are logged — any other authenticated user produces no rows, so the view is naturally team-only. Commits are attributed by git author; Michael's personal git identity is merged onto his platform login via `GIT_AUTHOR_EMAIL_MAP`. Every activity write is fail-open — logging never blocks or breaks a primary request.

**Testing Mode** (toggle in Settings → Account) bands the session as `testing`; that session's activity is excluded from the analytical Team Activity view by default and never reaches the agent context. It is session-scoped, never persisted, and resets to analytical on the next login. An amber pill in the nav bar marks an active testing session.

**Post-deploy operator steps** — register the GitHub push webhook and run the historical commit backfill. Both need `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` set on Render; the webhook endpoint 401s every push until the secret is set. Full instructions: `docs/TEAM_ACTIVITY_SETUP.md`.

## Explainer Tooltips

Every chart title, table column header and key metric label on the Analytics and Dashboard pages carries a small ⓘ InfoIcon — the explainer agent made accessible inline rather than only through the Council screen. Two interaction levels:

- **Hover** (300ms) — a lightweight tooltip with pre-written static text from `frontend/src/constants/explainerTooltips.ts`. No API call.
- **Click** — opens `ExplainerPanel`, a right-side slide-in drawer that streams a live, data-anchored explanation from the explainer agent via `POST /api/council/explain`. The completed explanation is logged to `agent_interactions` as interaction type `explain`.

## Generator-Evaluator Harness

A quality harness wraps every agent's text generation in an evaluate-and-retry loop: the output is scored 0-10 against task-specific criteria by `claude-sonnet-4-6`, and a response below the 7.0 threshold is regenerated with the evaluator's feedback injected into the prompt — up to two retries. The best-scoring attempt is always used.

It is **infrastructure, invisible to end users** — no UI change, no API response-shape change; only the quality of agent output improves. It runs across the council specialists and both passes of the Academic Review (peer agents and the arbiter verdict). Harness errors are silent — the original response is used on any failure. Per-run metrics (retry count, score improvement) are logged to `agent_interactions` and surface in Team Activity.

This is part of the AI-use narrative for the final presentation: the system does not just generate agent output, it evaluates and improves it before the user ever sees it. Full design: `CLAUDE.md` → Generator-Evaluator Harness.

## Academic Export Package

The Reports screen's **Export Academic Package** button assembles every analytics visualisation into a single ZIP suitable for a paper submission. Charts are re-rendered **off-screen on white backgrounds** — the live dark UI is never touched — so the captured PNGs print cleanly and embed in a Word document.

The light render is driven by an explicit `theme?: ChartTheme` prop (`frontend/src/lib/exportTheme.ts`): every theme-aware chart defaults to `DARK_CHART_THEME` (pixel-identical to the live UI) and the export modal passes `LIGHT_CHART_THEME` — a white palette with darkened strategy series colours chosen for contrast on white. A CSS attribute flip cannot recolour ten distinct strategy series, so theming is a prop, not a stylesheet toggle. `frontend/src/utils/chartCapture.ts` rasterises each off-screen node via `html2canvas` at 2× resolution; a single chart that fails to capture yields a placeholder PNG rather than failing the whole package.

`POST /api/v1/export/package` takes a multipart payload (chart PNGs, table CSVs, a study-period metadata JSON) and returns `forest_capital_academic_export_[YYYY-MM-DD].zip` — `charts/`, `tables/`, `metadata/study_period.txt`, `metadata/chart_descriptions.txt`, and a `README.txt`. The export is logged to `agent_interactions` as interaction type `export` (team-gated, fail-open). Auth required.

Suggested citation for exported figures: *"Portfolio Intelligence System analytical output, Forest Capital / McColl School of Business FNA 670, [date]."*

## Document Generation

The Reports screen's **Generate Documents** panel produces the project's three graded deliverables as **first drafts** — structured, data-accurate, narratively coherent, and intended for Bob to refine, not to submit verbatim. Every figure is real platform data; every narrative section is written by the Academic Writer agent (run through the generator-evaluator harness); every file carries the *AI DRAFT — REQUIRES HUMAN REVIEW* banner.

- `POST /api/v1/export/midpoint-paper` → the three-page midpoint paper (`.docx`) — 12 pt Times New Roman, double-spaced, 1-inch margins, page numbers; four sections per the FNA 670 brief with the summary-statistics and regime-conditional tables embedded.
- `POST /api/v1/export/executive-brief` → the five-page executive brief (`.docx`) — title page, Executive Summary, Methodology, four Key Findings (tables embedded), Limitations, Final Recommendations.
- `POST /api/v1/export/presentation-deck` → the 16-slide final deck (`.pptx`) — a professional navy/white theme, light-mode charts rendered server-side with matplotlib, four native-table slides, narrative conclusions/recommendations. When called with an `editor_draft_id`, it instead exports the **canvas presentation editor** draft (see below).

### Canvas presentation editor

The `presentation_deck` editor (`/editor/:draftId`) is a free-form **Konva canvas** — a 960×540 (16:9) slide per deck slide (the midpoint paper and executive brief keep the TipTap rich-text editor). Text elements drag, resize and inline-edit; chart elements embed a live platform chart (`GET /api/v1/charts/render/{key}`, with a 5-minute render cache) and carry an amber *unverified* border until the presenter confirms them. AI Layout repositions a slide's elements; AI Copy rewrites a selected text element — both with an Apply/Dismiss review. `migration 022` converts existing decks to the canvas element schema; `build_editor_pptx` exports the canvas to a `.pptx`, mapping the 960×540 canvas 1:1 onto a 10×5.625in 16:9 slide.

The **chart picker drawer** (May 19 2026 expansion) offers **sixteen server-renderable charts** grouped into six display categories — Regime Analysis (`regime_signals`, `regime_conditional_returns`), Factors (`factor_loadings`, `factor_returns_attribution`), Performance (`rolling_correlation`, `cumulative_returns`, `rolling_sharpe`, `return_distribution`, `monthly_returns_heatmap`), Risk (`drawdown_periods`, `risk_return`, `sensitivity`), Significance (`significance_journey`, `oos_performance`, `p_value_distribution`), and Activity (`team_activity`). Two renderer families back the API: `academic_deck.render_deck_charts` ships the five charts the .pptx export uses; `tools/chart_renderers.py` (`render_extended_charts`) renders the eleven canvas-only charts in the same matplotlib light theme. The single-strategy charts default to **REGIME_SWITCHING vs BENCHMARK**; `oos_performance` splits at **the last 60 months** as the OOS window. Source data is read straight from the existing caches — no chart needs `qa_results_cache` (the per-gate fields live in `strategy_results_cache`); the regime charts re-fit `classify_hmm_regime` over the full monthly history (cached by series fingerprint). A chart whose data is cold renders a Pillow placeholder PNG rather than failing the canvas.

### Presentation script writer

Each deck slide carries an optional **speaker** assignment (a per-slide badge in the editor navigator). Once at least one slide has a speaker, **Generate Script** (`POST /api/v1/documents/script/generate`) turns the deck into a spoken multi-speaker script: it pulls the deck plus the team's executive brief and midpoint paper as academic context, runs the Academic Writer through the generator-evaluator harness, and stores the result as a `presentation_script` editor draft. The script opens in the TipTap editor with a per-slide speaker navigator and a 150-wpm delivery-time indicator (green inside 18–27 minutes). Export produces a **master script** DOCX or, per speaker, an **individual script** containing only that presenter's slides (`POST /api/v1/documents/drafts/{id}/export`).

`tools/academic_export.py` is the shared layer: `gather_document_data()` pulls every figure from data already in PostgreSQL (no `get_full_history()` / `run_all_strategies()` recompute), and `harness_narrative()` generates each prose section through the harness. **Graceful degradation:** any section whose source data is unavailable is filled with a `[DATA PENDING]` marker — a missing input never fails the document, and a grep for the marker tells Bob exactly what to supply. The midpoint paper's Next Steps section and the deck's narrative depend on the analytics caches and the last Academic Review verdict; warm the dashboard and run an Academic Review first for a complete first draft. Uploaded requirements documents in **Settings → Academic Documents** are injected into the Academic Writer's context, so the drafts are rubric-aware.

## Changelog

The `changelog` table is the source of record for what the platform can do and **why each capability matters academically**. It drives the What's New modal (shown once after login with the features added since the user's last visit) and the Settings → Release History section. Every entry carries an `academic_rationale` explaining how the feature helps the team earn higher marks.

**Changelog contract:** every database migration must insert at least one changelog row. `scripts/changelog_gate.py` enforces this — in CI and as a pre-commit hook — and fails any migration added without a changelog INSERT. See `CLAUDE.md` → Changelog, What's New, and CI/CD.

## Site Tour

A guided fifteen-step walkthrough of the whole platform — `SiteTour.tsx`, a controlled `react-joyride` tour mounted in `MainLayout` so it spans every route. It serves two audiences at once: **Forest Capital**, where it positions the platform as a serious analytical tool, and the **McColl School of Business**, where every step ties a feature to a specific grading criterion. Ten steps also name the team member the feature matters most to.

The tour auto-starts once per login session when a new tour version is pending (and no What's New modal is showing); the Settings → Account **Retake Site Tour** button and the What's New modal's **View updated site tour** button both force-start it. Completion and skip POST `/api/v1/changelog/mark-seen` so it does not re-trigger until a new version ships.

**Bumping the tour:** `TOUR_VERSION` in `backend/config.py` (currently 2) is the version gate. When the tour's steps change materially, increment it by 1 and ship a changelog entry in the same migration (migration 013 is the template). The bump re-surfaces the tour for every user below the new version. See `CLAUDE.md` → Site Tour.

## Guided UAT Test Runner

An interactive, logged, attested in-platform test runner — the operational counterpart to `docs/UAT_TEST_GUIDE.md`, which remains the readable source of truth for test cases.

**How testers access it:** Settings → Account → enable **Testing Mode**, then **Start Test Pass**. Pick *All Testers* or *My Section* (auto-selected by email). The runner navigates to each step's screen, highlights the element, and shows a floating panel with **Pass / Fail / Skip / Feedback**. Fail opens a structured failure report; Feedback files a suggestion (the step stays pending). A free-form **💡 Suggest** button files feedback with no step association. Both failure reports and feedback pass a quality gate (`claude-sonnet-4-6`) before storage — a vague submission gets one clarification prompt; the tester never sees a score. Feedback is AI-categorised for the backlog. Results persist, so a pass can be paused and resumed.

Results, structured failure reports, and the AI-categorised feedback backlog appear under **Settings → Test Results** (every tester) and **Settings → Test Administration** (admin only). Test activity also interleaves into the Team Activity timeline, and three operational login notifications surface new test cases, resolved failures, and feedback responses.

**Adding or changing test steps:** edit `frontend/src/constants/testScripts.ts` — each `TestStep` is one checklist item, with a `route`, an optional highlight `target`, an instruction and an expected result. When steps change materially, bump `TEST_SCRIPT_VERSION` in both `testScripts.ts` and `backend/config.py` so the unseen-step check re-surfaces them.

The attestation rows (`test_results`, migration 014) are an objective, timestamped record of systematic QA — evidence for the Analytical Appendix's transparency criterion. **Screenshots are best-effort:** stored on Render's ephemeral disk, they do not survive a redeploy; the attestation row is the durable record. See `CLAUDE.md` → Guided UAT Test Runner.

## Access Control

Access is **database-managed and permission-based** (migration 015). Every user has a `permissions` array; that array is the authoritative capability set. A **role** is a named preset over those permissions — there are three:

- **viewer** — explore all analytics, dashboards and charts; ask the AI council; use the inline ⓘ explainers. A guest (Dr. Panttser, a reviewer) is a viewer and sees a one-time welcome banner.
- **team_member** — the above plus the action features: academic document upload/delete, all export endpoints (academic package and the three generated documents), Academic Review, the guided test runner, and the Settings modifications.
- **sysadmin** — every permission, including the admin testing views and user management. Michael Ruurds is the sysadmin; the role is assigned by the migration seed, never from the UI.

A user whose permissions diverge from their role's preset shows as **Custom** in the UI.

**The sysadmin manages every user from inside the platform** — Settings → Users — adding, editing and deactivating users and tuning each one's permission checklist. Last-sysadmin guards prevent the platform from being left with no administrator.

Permission resolution is three-tier: the session JWT (embedded at login), then a `platform_users` lookup, then a **config fallback** that mirrors the migration seed — so a database outage never locks the team out. `ALLOWED_EMAILS` and `PROJECT_TEAM_EMAILS` are retained purely as that emergency fallback.

Frontend: `TeamGate` wraps every action element, gating it on the required permission — a user without it sees it muted with a lock icon, or hidden. Backend: the `require_permission(perm)` dependency returns 403 on every gated endpoint. The **AI council question is deliberately open** to every authenticated user — it is read-only, scope-guarded and rate-limited, and letting a guest interrogate the analysis is the whole point of sharing the platform. See `CLAUDE.md` → Database-Managed Access Control.

### Adding a user

Sign in as the sysadmin, open **Settings → Users**, click **Add User**, enter the email, pick a role preset (Viewer or Team Member), adjust the permission checklist if needed, and save. To revoke access, **Deactivate** the user — the row is kept so their activity history stays attributed.

## Mobile Support

The frontend is fully responsive from **320px width upward** — every screen, table, chart and panel works on a phone. The implementation is frontend-only; the desktop experience (≥1024px) is unchanged.

Three breakpoint tiers (Tailwind defaults): **mobile** `< 640px`, **tablet** `640–1023px` (`sm:`), **desktop** `1024px+` (`lg:`).

Key adaptations below `lg:`:

- The horizontal nav becomes a **hamburger drawer** — a left slide-in with the grouped nav items, the mode switcher and account controls.
- ExplainerPanel / DataExplainPanel and the TestRunner panel become **bottom sheets**; the What's New and Academic Export modals go full-screen.
- Wide data tables scroll horizontally with a **frozen first column** (the row label stays visible); the Dashboard strategy table also drops to a reduced column set with a "More columns" toggle.
- Interactive elements meet a **44px touch-target** minimum; `env(safe-area-inset-bottom)` keeps content clear of a phone's home bar.

**Tested viewports:** iPhone SE (375px), iPhone 14 (390px), iPad (768px) and desktop (1280/1440px), portrait and landscape — see `docs/mobile_checklist.md` for the manual verification checklist. Automated coverage is in `frontend/src/__tests__/mobile-responsive.test.tsx`.

**Known limitation:** jsdom does not evaluate CSS `@media` breakpoints, so the automated suite asserts the responsive utility classes and the drawer's React-state behaviour rather than the rendered breakpoint layout — the rendered layout is covered by the manual checklist.

## Automated Triage

The platform triages its own UAT backlog. When tester feedback and failure reports accumulate, an AI QA-lead agent (`claude-sonnet-4-6`) reads every unaddressed item and produces a structured triage report — **Immediate Actions**, **Quick Wins**, **Patterns and Themes**, **Post-Deadline Backlog**, **Summary** — and opens a GitHub issue for each blocking/major item. No manual extraction.

**Three triggers:**

- **Threshold** — when 5+ unaddressed feedback/failure items have accumulated since the last triage run.
- **Test pass** — when a tester completes a full test script (all steps attested).
- **Manual** — the sysadmin's "Run Triage Now" button.

Both automatic triggers are fire-and-forget — they never block the submission that fired them — and a concurrency lock ensures one run at a time. Every step is fail-open: a failure still stores the report with whatever completed (`status` = complete / partial / failed), and GitHub issue creation never aborts the run.

GitHub issues are opened automatically for the urgent items, tagged with severity and category labels (created on the repository if missing). Issue links appear on the triage report.

**Where to view:** Settings → Test Administration → **Triage Reports** (sysadmin only) — the latest report in full, a run button, and a history of previous reports. The sysadmin also sees a "🔍 Triage report ready" login notification. A triage report is produced automatically after every completed test pass.

## Statistical Audit

The **QA tab** is a unified quality-assurance hub with two sections: **Methodology Review** — the QA agent's 39-check methodology checklist, visible to every authenticated user — and **Statistical Audit** (described below). A **Run Full QA** button at the top runs both at once with unified progress and an overall verdict; a **Presentation View** renders a clean QA certificate for screen-sharing. The Statistical Audit panel was relocated to the QA tab from Settings — the full panel is project-team only, and a non-team viewer sees a read-only summary of the latest run.

Every analytical figure on the platform can be **independently re-verified**. The audit sends the raw data and the formula specifications to a *separate* model — **`claude-opus-4-7`**, independent of the `claude-sonnet-4-6` the platform computes with — which recomputes every metric from scratch and flags any discrepancy. It is the platform's strongest accuracy guarantee: every number shown to Forest Capital and faculty has been recomputed by a separate model, with full working shown.

**Three layers run in sequence:**

1. **Raw data verification** — six deterministic checks: benchmark CAGR sanity, asset-return ordering, factor-data alignment, monthly return bounds (±50%), weight constraints, and return-series length.
2. **Independent recomputation** — the auditor model recomputes the summary statistics, Carhart factor loadings, the efficient-frontier max-Sharpe point, the pre/post-2022 regime split and the rolling correlation, in five parallel task groups. A discrepancy is PASS within 0.01%, WARNING to 0.1%, FAIL beyond.
3. **Consistency checks** — ten deterministic checks that the same metric carries the same value everywhere, that the regime split is applied uniformly, and that the structural identities hold.

Every step is fail-open: a failure still stores the run with whatever completed, and a flaky auditor never manufactures a false critical.

**Two computation regimes.** The Analytics layer annualises monthly data with ×12; the Dashboard strategy table annualises daily data with ×252. CAGR is regime-independent and is cross-checked directly; Sharpe and max-drawdown differ between the layers *by construction*, so the audit documents the difference (in the report's *Computation Regimes* section) rather than flagging it.

**How to run it:** the **QA tab** → **Statistical Audit** section → **Run Full Audit** (project team). Use **Run Pre-Submission Audit** before a deadline — it runs the same three layers and its **Download Audit Report** produces a formatted text report intended for inclusion in the **Analytical Appendix** as evidence of independent statistical verification. Or use **Run Full QA** at the top of the QA tab to trigger the methodology checklist and the statistical audit together. A login notification flags an audit that completes with failures.

**Smart audit caching.** The Opus audit model is expensive, so an audit re-runs **only when the data it verifies has actually changed**. A lightweight SHA256 fingerprint of `market_data_monthly` / `ff_factors_monthly` / `strategy_results_cache` (row counts + newest dates) is stored on every completed run; `is_audit_current()` compares the live fingerprint against it. When the cache still holds, the QA tab shows a green *"Verified … · Data unchanged · No re-run needed"* banner and **Run Full QA** becomes a muted *Re-run Audit* — no Opus spend. When only one layer has drifted, the banner breaks it down (*Statistical audit: ✓ current / Methodology audit: ⚠️ stale*). Audits **fire automatically** on a data event — a successful incremental data ingestion, or `POST /api/v1/cache/invalidate` — re-verifying in the background, idempotent and fail-open. For the live presentation, a confirmation-gated **Run Live Demo** button forces a fresh audit regardless of the cache; demo runs are stored `triggered_by="demo"` and marked 🎯 in the audit history.

**Weight-constraint verification.** The backtester persists each strategy's per-rebalance target weights (`weight_schedule`), so Layer 1's weight-constraint check runs in full — sum-to-1, long-only and ≤1 verified at every rebalance. A strategy cached before weight persistence shipped warns until the cache is refreshed (`POST /api/v1/cache/invalidate`).

## Continuous Integration

Two GitHub Actions workflows:

- **`test.yml`** — on every branch push and PR: backend pytest, frontend Vitest + lint, and the live-deployment E2E run.
- **`ci.yml`** — on push to `main`: a database-backed pipeline. The backend job starts a `postgres:15` service, runs `alembic upgrade head`, runs the full pytest suite with coverage (so the DB round-trip tests actually execute), and runs the changelog gate. The frontend job runs `tsc --noEmit` and Vitest.

**Required GitHub Actions secrets:** none new for `ci.yml` — the test database is the ephemeral service container, so `DATABASE_URL` is a literal workflow env, not a secret. `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` (used by `test.yml`) remain optional repository secrets; the suite runs under `ENVIRONMENT=test` and tolerates them being absent.

**Pre-commit hooks** — install after cloning:
```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type pre-push
```
The changelog gate runs on every commit; pytest and the frontend typecheck run at push time.

**Single-branch limitation:** development currently commits directly to `main`. The recommended post-deadline upgrade is a `develop → main` pull-request flow with the `ci.yml` jobs as required status checks, so nothing reaches `main` without a green pipeline.

## Quick Start

### Backend
```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env           # Fill in API keys
uvicorn main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Visit http://localhost:5173 — you will be prompted to log in via magic link.  
In development mode the magic link prints to the backend terminal (no email required).

## Environment Variables

See `backend/.env.example` for all required variables. Key ones:

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key |
| `GOOGLE_API_KEY` | Gemini API key |
| `ALLOWED_EMAILS` | Comma-separated list of authorised @queens.edu addresses |
| `SECRET_KEY` | ≥32-char random string for token signing |
| `MASTER_API_KEY` | Developer-only key for `/api/dev/*` endpoints |
| `DATABASE_URL` | PostgreSQL connection string (optional — skips DB writes if absent) |
| `ENVIRONMENT` | `development` (magic link prints to terminal) or `production` |
| `GITHUB_REPO` | Repo the Team Activity commit sync + webhook target (default `saffamiker/forest-capital`) |
| `GITHUB_TOKEN` | PAT with `repo` scope — the commit-sync endpoint needs it (the repo is private) |
| `GITHUB_WEBHOOK_SECRET` | Validates GitHub push-webhook signatures — **required on Render** before the webhook endpoint accepts any event |

## Known Issues

**Issue #2 — HMM on Windows.** `hmmlearn` requires Microsoft C++ Build Tools on Windows. The HMM tests are marked skip on Windows; they run and pass in CI on Ubuntu. Install Visual Studio Build Tools or use WSL to run locally.

## Known Limitations

- **UAT screenshots are ephemeral.** Test-runner failure-report screenshots are stored on Render's ephemeral filesystem and do not survive a redeploy. The `test_results` attestation row (result, description, severity, timestamps) is the durable record — screenshots are supporting evidence only. An object store (S3 or equivalent) is the post-deadline fix.
- **Two agent-registry structures in `main.py` are not merged.** The model strings were centralised, but the two registry tables remain separate — merging them is a deferred refactor (code review M2/M7).
- **`schemas.py` example model strings are literals.** They do not reference the agent-model constants, to avoid a `models → agents` import cycle (code review M16).
- **`extract_document_text()` is PDF-only.** Markdown handling lives upstream in the `/api/v1/documents/academic/upload` endpoint (raw UTF-8 decode); the function's former non-PDF text branch was removed as dead code.
- **Single `main` branch.** Development currently commits directly to `main`. A `develop → main` PR flow with the `ci.yml` jobs as required status checks is the recommended post-deadline upgrade.
- **`academic_review.py` reads the team list from config.** The Academic Review agent builds its team-member list from `config.PROJECT_TEAM_EMAILS` rather than the active `team_member` users in `platform_users`. The config list and the seeded table agree, so there is no behavioural gap; reading it from `platform_users` is a deferred follow-up.

*Resolved since earlier drafts:* the `Connection._cancel` warning (NullPool engines on both the production off-loop write path and the test environment); three-factor Fama-French (now the Carhart four-factor model, MOM backfilled); the turnover proxy (the analytics layer now surfaces true `sum(|Δw|)/2` portfolio turnover); the Dashboard cumulative chart (now real growth-of-$1 data, not a synthetic series); and the hardcoded Sharpe confidence interval (now real intervals, or `[—]`).

## Roadmap

Work is tracked as a Kanban board (Backlog | In Progress | Done) — the board of record is in `CLAUDE.md`. The near-term focus is the June 3 midpoint: the written submission, an Academic Review session, and the per-member UAT passes through the guided test runner.

## Team

| Name | Role |
|------|------|
| Michael Ruurds | Lead Engineer (solo dev, 20 hrs/week) |
| Bob | Lead Analyst — written report, methodology, academic interpretation |
| Molly | Lead Presenter — slide deck, Forest Capital brief, July 1 demo |
| Dr. Panttser | Professor / Reviewer |

Key dates: Mid-checkpoint June 3 @ 6pm · Final presentation July 1 @ 6pm · McEwen 120

