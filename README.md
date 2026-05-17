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
| 6 | 🚧 In progress | **Phase 1:** 12 Statistical-Evidence + Regime-Analysis charts with `/api/v1/charts/data` bundle endpoint. **Phase 2:** Council Debate narrative fix, navigation persistence via Zustand stores, chart UX standardisation. **Phase 3:** Tiered QA system (Tier 1 deterministic + Tier 2 Sonnet background + Tier 3 Opus manual) with `qa_results_cache` table, nav-bar status badge, Present-mode gate (≥WARN + <48h + hash match). **Phase 4:** Commentary mode frontend bridge — `glossaryStore`, `ExplainableText` three-level wrapper, `ChartCommentStrip` (always-visible Sources line, mode-conditional narrative), `LearnModeBanner` + `LearnModeToggle`. **Phase 5:** Reports screen + Priority 1 (June 3) midpoint paper generator with `python-docx`, Academic Writer prose, AI DRAFT banner. **Phase 6:** Storyboard Editor + Presentation Script Writer. 15-slide AI draft (`POST /api/documents/storyboard/draft`) with Academic Writer speaker-note enrichment. Full CRUD via `tools/documents_cache.py` against `documents` / `document_versions` / `document_drafts` (migration 004). React `StoryboardEditor` page with drag-to-reorder, slide editor panel, running timing bar (GREEN ≤20m, AMBER ≤21m, RED >21m), Version History sidebar (named + auto-saves), and 30s debounced auto-save. `python-pptx` deck generator; `script_writer.py` produces full-team / Molly / Michael / Bob / rehearsal scripts at 130 wpm; Q&A doc with 18 questions across Forest Capital / MSFA Board / AI usage. Gemini 1.5 Pro assistant panel with paragraph-level red/green diff display, multi-turn conversation, scope guard, and mock fallback. Grok contrarian analyst remains alongside Gemini independent analyst. **Phase 8 (Explainer slice):** stable term IDs on the 7 Dashboard strategy-table headers + 5 Significance Journey Matrix gate headers, regime-badge label, and the 2022 Equity-Bond Correlation Breakdown banner; `QAAuditPanel` now calls `loadQA(audit.items)` on audit load and renders the four-section glossary narrative (what / why / failure-meaning / how-tested) inside each expanded checklist row. **Phase 9 (Academic Advisor — Agent 10):** Sonnet + Anthropic server-side `web_search_20250305` tool with citation integrity enforced at the agent boundary — every URL the model emits is filtered against the tool's actual returned URLs before reaching the frontend (`_filter_to_verified`), so no fabricated citation can survive. Three endpoints (`/api/advisor/analyse`, `/verify-finding`, `/citations`) with grade-aware rubric (Midpoint 10%, Brief 20%, Appendix 35%, Presentation 35%). Gold-accented `AdvisorPanel` mounted globally in `MainLayout` (floating button), hidden in Present mode; Reports screen wires a per-deliverable "Get Advisor Feedback" button using the controlled-open API to pin the panel to the right context. Session-cached `advisorStore` keyed by deliverable+query to avoid re-burning the ~$0.04-0.06 web-search cost on panel re-open. Fixed `test_provenance_json_source_types_are_valid` — added `ken_french_direct` to the valid source-type set for the FF factors direct HTTP fetcher. **Phase 9b (excerpt provenance):** added Anthropic `web_fetch_20250910` to the advisor's tools list and a second integrity gate in `_filter_to_verified` — every citation now carries an `excerpt` field that is set to a 2-3 sentence passage *only* when the URL was actually fetched (URL must appear in the parsed `web_fetch_tool_result` blocks). Failed fetches (paywall, 404, timeout) → excerpt = `null` → frontend shows "Excerpt unavailable — click to verify directly" on hover. `AdvisorPanel.CitationItem` renders a custom hover tooltip with gold accent showing the excerpt or fallback message, alongside a native `title` attribute for a11y / no-JS fallback, with the citation link opening in a new tab (`target="_blank" rel="noopener"`). **Phase 11 (Bob's section editor + remaining Explainer wiring):** `POST /api/reports/analytical-appendix` (HTML, 6 sections with Table 1 strategy comparison + Table 2 provenance registry auto-injected, AI DRAFT banner sticky in screen / page-break-avoiding in print) and `POST /api/reports/executive-brief-template` (.docx, 6 sections incl. 5 captioned chart placeholders) both available in the Reports manifest. `GET /api/agents/personas` surfaces every council agent's verbatim system prompt + model + module path. Bob's three deliverables can now be opened in a new `SectionEditor` page (`/reports/document/:id`) — three new endpoints back this flow: `POST /api/documents/section-doc/draft` creates a section-structured document with `ai_draft` (immutable) and `content` (Bob's edits) per section, `POST /api/documents/:id/sections/:section_id/regenerate` re-runs Academic Writer for one section only, `POST /api/documents/:id/export` compiles Bob's current draft back into .docx or HTML. Section editor UI: left section list with live word counts + total, middle editor surface per section with View AI Draft / Regenerate AI / Revert (with confirmation dialog), right Version History panel (collapsible, named + auto-saves, restore creates a new version row), Save Version dialog. AI DRAFT banner permanent at top, never dismissable. `PersonaModal` opens from a "View system prompt" link on every `AgentCard` in `CouncilDebate` — three tabs: PROMPT (verbatim with copy button), PLAIN ENGLISH (Explainer-generated via `glossaryStore.loadPersona`, cached per agent), THIS SESSION (agent's actual contribution to the current council run, falls back to passed `sessionContent` when Explainer is offline). Escape key + backdrop click + X all close. `StrategyCard` metric labels (Sharpe Ratio, CAGR, Max Drawdown, Volatility, CV Stability Score, Tier 1 Significance Tests, DSR / PSR / SPA p-value) wrapped in `ExplainableText` with stable term IDs matching the Dashboard table headers — same glossary entry hits whether the click is on the dashboard or the strategy card. **Phase 12 (Advisor input validation):** Get Advisor Feedback button now disabled when the query is empty or whitespace-only — prevents firing the ~$0.04-0.06 web-search call against an empty string. Submit handler trims the query before sending, so leading/trailing whitespace doesn't reach the backend. Placeholder text updated to "Ask about your findings, deliverables, or what to focus on..." to nudge the user toward concrete questions. The previous deliverable-specific `DEFAULT_QUERIES` fallback is removed — silent placeholders were producing low-quality LLM responses when users submitted blank inputs. **Phase 13 (Render hotfix — four bugs):** (1) **migration 006** widens `regime_signals_cache.hmm_regime` from INTEGER to VARCHAR(20) — the regime detector emits string labels (`'BULL'`, `'BEAR'`, `'TRANSITION'`) that the previous INTEGER column rejected with `InvalidTextRepresentation`. Upgrade casts existing rows via `postgresql_using="hmm_regime::text"`; downgrade nulls non-numeric rows first. (2) **Explainer Grok 400** — aligned `_call_grok` timeout (30s) and body shape with `agents/contrarian_analyst.py` literally; on 4xx the response body is now logged at `explainer_grok_http_error.body_preview[:500]` so future xAI spec drifts surface in Render logs immediately rather than as a bare "400 Bad Request". (3) **Haiku fallback truncated JSON** — bumped fallback `max_tokens` floor to `HAIKU_FALLBACK_MAX_TOKENS = 2000` (was 800 — `explain_qa` covered 30 items and hit the cap mid-string), and routed all five `explain_*` methods through new `_safe_json_parse` helper that tolerates fences, prose-wrapped JSON, truncated strings, and non-string inputs without raising. (4) **Incremental update KeyError** — `_append_incremental_daily` previously called `vix_series.set_index("date")["value"]`, but `_fred_fetch` returns a DataFrame with DATE as the **index** and the value column named after the series_id (`VIXCLS`, `DGS2`). Fixed to `vix_series.iloc[:, 0]` so the Series keeps its date index. |

## Test Counts (current)

| Layer | Tests | Notes |
|-------|-------|-------|
| Backend (pytest) | 1026 passed, 15 skipped | HMM tests skip on Windows (hmmlearn requires C++ build tools; passes in CI on Linux). +24 tests for the combined analytics enhancement pass: `test_momentum_factor.py` (6) covers the Ken French momentum-factor direct-HTTP fetch and the `mom`-column backfill of `ff_factors_monthly`; `test_analytics.py` (+10) covers the Carhart four-factor regression (unit MOM-beta recovery, three-factor fallback when MOM is absent or NULL), cumulative returns starting at 1.0, and the benchmark's 0.0 excess return / null information ratio; `test_strategy_enhancements.py` (8) covers true portfolio turnover (`sum(|Δw|)/2` per rebalance, non-negative, ~0 for fixed-weight statics) and the parameter-sensitivity sweep across all four dynamic strategies. Factor-loading regressions are now Carhart four-factor (MKT-RF / SMB / HML / MOM); the Dashboard turnover column shows genuine weight-change turnover. +6 tests for the Academic Review council endpoint (`POST /api/council/academic-review`): context assembly with documents and with missing types, peer fan-out covering every non-arbiter agent, the arbiter prompt carrying all peer responses, and the SSE stream emitting `peer_responses` before `arbiter_chunk`. The academic-document upload endpoint accepts `.pdf` and `.md` (extension-authoritative — `.md` is read directly as UTF-8, bypassing pypdf; any other extension is a 400). `extract_document_text()` is now PDF-only: its dead non-PDF branch and the three obsolete tests that exercised it were removed (net −2 vs the prior 998). +5 tests for the `/settings` page backend: `GET /api/v1/admin/data-status` (per-table row count, date range, green/amber/red staleness) and `GET /api/v1/analytics/config` (risk-free rate), plus the three new `academic_documents` types (`midpoint_draft`, `presentation_slides`, `presentation_script`). +4 tests in `test_optimizer.py` for the Efficient Frontier rewrite: `efficient_frontier()` now does a target-return sweep (minimise variance s.t. a fixed target return) over the full long-only space `[0,1]` instead of a risk-aversion sweep capped at `MAX_WEIGHT=0.40` — the 0.40 cap collapsed a 3-asset frontier into a near-straight sliver. Tests pin the hyperbola shape, the min-variance→max-return span, long-only weights, and that the tangency point beats every single asset. +14 tests in `test_academic_documents.py` for the document-upload feature: PDF/text extraction (`pypdf`), academic-context formatting and injection, migration 008 (`academic_documents` table), and upload-endpoint validation. Uploaded documents are injected into every agent's system context. +13 tests in `test_analytics.py` for the academic analytics layer (`tools/analytics.py`): summary statistics, 12-month rolling correlation with the 2022 regime break, regime-conditional performance (pre/post-2022 split), drawdown comparison, and Fama-French factor loadings (since extended to the Carhart four-factor model) — all served by `GET /api/v1/analytics/academic` and surfaced on the new Analytics page. The Efficient Frontier computes from `market_data_monthly`'s equity/IG/HY monthly returns — the same 3-asset universe the 10 strategies use — via `cache.get_monthly_returns()`, so the curve and the strategy scatter share one universe and annualization (`efficient_frontier(..., periods_per_year=12)`); no yfinance dependency. +1 test pinning the `periods_per_year` annualization. +8 tests in `test_optimizer.py` for the NaN/Inf guard: `_returns_have_finite_moments()` rejects empty / single-row / all-NaN-column return frames before the cvxpy/scipy solver, so a ticker yfinance fails to fetch falls back to equal weight with one log line instead of a "Problem data contains NaN or Inf" crash on every frontier point. `/api/optimize/weights` also drops all-NaN columns before `dropna()` so one dead ticker no longer wipes every row. Grok model upgraded `grok-3-mini` → `grok-4.3` (both grok-3-mini and grok-4 retired on OpenRouter, 404). Earlier: +6 Efficient Frontier tests — `/api/optimize/weights` returns the structured `{frontier_points, portfolio_points, max_sharpe_point, min_variance_point}` payload, `portfolio_points` read from the latest `strategy_results_cache` row (no `get_full_history()` recompute). |
| Frontend (Vitest) | 178 passed | +30 in Phase 11 + 7 in Phase 12 (advisor input validation): submit button disabled when query empty or whitespace-only, enabled on first non-whitespace char, re-disabled on clear, no axios call when clicked while disabled, query is trimmed on submit, placeholder text guides the user. |
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
**Agents:** Anthropic SDK (Claude Opus 4.6 + Sonnet 4.6 + Haiku 4.5), Google GenerativeAI SDK (Gemini 1.5 Pro), xAI HTTP API (Grok 3 Mini — Contrarian Analyst, Sprint 6), Anthropic server-side `web_search_20250305` + `web_fetch_20250910` tools (Academic Advisor, Sprint 6 Phase 9 — citation integrity with passage-level excerpt provenance)  
**Frontend:** React 18, TypeScript 5 (strict), Vite, TailwindCSS, Recharts, Zustand, React Query  
**Database:** PostgreSQL (asyncpg), 13 tables: `data_series_registry`, `market_data_monthly`, `market_data_daily`, `data_validation_log`, `strategy_results_cache`, `regime_signals_cache`, `auth_attempts`, `used_magic_tokens`, `qa_results_cache` (Sprint 6 Phase 3), `documents` / `document_versions` / `document_drafts` (Sprint 6 Phase 5 — Storyboard Editor), `ff_factors_monthly` (Sprint 6 Phase 7 — direct Ken French fetch)  
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

`/settings` is a single scrollable page reached from the nav-bar gear icon, with five sections:

1. **Organisation** — McColl / Forest Capital brand switcher (drives the header branding).
2. **Data and Study Period** — read-only data-table status from `GET /api/v1/admin/data-status`: per-table row count, date range, last-updated timestamp and a green/amber/red staleness pill (green = newest data within 15 days of today, amber 15–30, red > 30), plus a study-period summary line.
3. **Analytics Configuration** — the risk-free rate from `GET /api/v1/analytics/config` (mean monthly FRED DTB3 ×12 — the same value the efficient frontier and analytics layer use), shown read-only.
4. **Academic Documents** — the `AcademicDocumentsPanel` upload UI (moved here from the Reports view). Accepts **PDF and Markdown (.md)**; file type is decided by extension, authoritative over MIME type — PDFs go through pypdf, `.md` files are read directly as UTF-8. Uploaded documents are injected into every AI agent's system context. Deep-linkable via `/settings#academic-documents`.
5. **Account** — signed-in email + Sign out.

**Staleness pills — expected behaviour:** `market_data_monthly` and `ff_factors_monthly` show **red** pills by design. The dataset is intentionally locked at December 2025, so the newest data is several months behind "today". The pill reports recency-vs-today; a red pill here is the dataset's deliberate end date, not a pipeline failure.

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

## Known Issues

**Issue #2 — HMM on Windows**  
`hmmlearn` requires Microsoft C++ Build Tools on Windows. 10 HMM tests are marked skip on Windows; they run and pass in CI on Ubuntu. Install Visual Studio Build Tools or use WSL to run locally.

## Known Limitations

- **`extract_document_text()` is PDF-only.** Markdown handling lives upstream in the `/api/v1/documents/academic/upload` endpoint (raw UTF-8 decode); the function's former non-PDF text branch was removed as dead code.
- **`Connection._cancel` RuntimeWarning** surfaces in some test runs (asyncpg connection teardown across `asyncio.run()` boundaries) — under investigation; does not affect correctness.
- **Single `main` branch.** Development currently commits directly to `main`. A `develop → main` PR flow with required status checks is recommended post-deadline.

## Sprint 6 Roadmap

**Shipped to date** (Phases 1–5):
- All 12 Statistical Evidence + Regime Analysis charts driven by `/api/v1/charts/data`
- Tiered QA system (deterministic Tier 1, Sonnet Tier 2 background, Opus Tier 3 manual) with nav-bar badge and Present-mode gate
- Commentary mode bridge — `glossaryStore`, `ExplainableText`, `ChartCommentStrip` with always-visible Sources line
- Grok contrarian analyst alongside Gemini independent analyst
- Council Debate narrative bug fixed; navigation persistence across all 5 stores
- Reports screen + Priority 1 midpoint paper generator (`/api/reports/midpoint-template`) with `python-docx`, Academic Writer prose, AI DRAFT banner — addresses June 3 deadline
- Alembic migrations 003 (`qa_results_cache`) and 004 (`documents` / `document_versions` / `document_drafts`)

**Remaining for Sprint 6 close (target Jul 1)**:
- Executive Brief + Analytical Appendix generators (Bob's remaining deliverables)
- Full regression suite + performance benchmarks (p95 response times)
- Accessibility audit (axe-core, WCAG AA)
- Final git tag: `v1.0.0-presentation`

**Shipped in Phase 6** (this commit):
- Storyboard Editor UI — drag-reorder, slide editor, timing bar, 30s auto-save, Version History sidebar
- `POST /api/documents/storyboard/draft` + full CRUD on `documents` / `document_versions` / `document_drafts`
- `POST /api/reports/generate-from-storyboard/:id` — `.pptx` deck, full-team / Molly / Michael / Bob / rehearsal scripts, Q&A `.docx`
- Presentation Script Writer at 130 wpm with voice differentiation per owner
- Gemini Assistant panel with red/green paragraph diff and per-message Apply/Skip

## Team

| Name | Role |
|------|------|
| Michael Ruurds | Lead Engineer (solo dev, 20 hrs/week) |
| Bob | Lead Analyst — written report, methodology, academic interpretation |
| Molly | Lead Presenter — slide deck, Forest Capital brief, July 1 demo |
| Dr. Panttser | Professor / Reviewer |

Key dates: Mid-checkpoint June 3 @ 6pm · Final presentation July 1 @ 6pm · McEwen 120

