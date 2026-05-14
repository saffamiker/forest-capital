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
| 6 | 🚧 In progress | **Phase 1:** 12 Statistical-Evidence + Regime-Analysis charts with `/api/v1/charts/data` bundle endpoint. **Phase 2:** Council Debate narrative fix, navigation persistence via Zustand stores, chart UX standardisation. **Phase 3:** Tiered QA system (Tier 1 deterministic + Tier 2 Sonnet background + Tier 3 Opus manual) with `qa_results_cache` table, nav-bar status badge, Present-mode gate (≥WARN + <48h + hash match). **Phase 4:** Commentary mode frontend bridge — `glossaryStore`, `ExplainableText` three-level wrapper, `ChartCommentStrip` (always-visible Sources line, mode-conditional narrative), `LearnModeBanner` + `LearnModeToggle`. **Phase 5:** Reports screen + Priority 1 (June 3) midpoint paper generator with `python-docx`, Academic Writer prose, AI DRAFT banner. **Phase 6:** Storyboard Editor + Presentation Script Writer. 15-slide AI draft (`POST /api/documents/storyboard/draft`) with Academic Writer speaker-note enrichment. Full CRUD via `tools/documents_cache.py` against `documents` / `document_versions` / `document_drafts` (migration 004). React `StoryboardEditor` page with drag-to-reorder, slide editor panel, running timing bar (GREEN ≤20m, AMBER ≤21m, RED >21m), Version History sidebar (named + auto-saves), and 30s debounced auto-save. `python-pptx` deck generator; `script_writer.py` produces full-team / Molly / Michael / Bob / rehearsal scripts at 130 wpm; Q&A doc with 18 questions across Forest Capital / MSFA Board / AI usage. Gemini 1.5 Pro assistant panel with paragraph-level red/green diff display, multi-turn conversation, scope guard, and mock fallback. Grok contrarian analyst remains alongside Gemini independent analyst. **Phase 8 (Explainer slice):** stable term IDs on the 7 Dashboard strategy-table headers + 5 Significance Journey Matrix gate headers, regime-badge label, and the 2022 Equity-Bond Correlation Breakdown banner; `QAAuditPanel` now calls `loadQA(audit.items)` on audit load and renders the four-section glossary narrative (what / why / failure-meaning / how-tested) inside each expanded checklist row. **Phase 9 (Academic Advisor — Agent 10):** Sonnet + Anthropic server-side `web_search_20250305` tool with citation integrity enforced at the agent boundary — every URL the model emits is filtered against the tool's actual returned URLs before reaching the frontend (`_filter_to_verified`), so no fabricated citation can survive. Three endpoints (`/api/advisor/analyse`, `/verify-finding`, `/citations`) with grade-aware rubric (Midpoint 10%, Brief 20%, Appendix 35%, Presentation 35%). Gold-accented `AdvisorPanel` mounted globally in `MainLayout` (floating button), hidden in Present mode; Reports screen wires a per-deliverable "Get Advisor Feedback" button using the controlled-open API to pin the panel to the right context. Session-cached `advisorStore` keyed by deliverable+query to avoid re-burning the ~$0.04-0.06 web-search cost on panel re-open. Fixed `test_provenance_json_source_types_are_valid` — added `ken_french_direct` to the valid source-type set for the FF factors direct HTTP fetcher. |

## Test Counts (current)

| Layer | Tests | Notes |
|-------|-------|-------|
| Backend (pytest) | 810 passed, 10 skipped | HMM tests skip on Windows (hmmlearn requires C++ build tools; passes in CI on Linux). +35 tests in Sprint 6 Phase 9 (Academic Advisor: 9 filter-to-verified contract tests, 5 JSON parse tests, 14 endpoint tests, 5 deliverable-rubric tests, 3 mock-fixture integrity invariants). |
| Frontend (Vitest) | 134 passed | +20 advisor tests (advisorStore cache invariants, AdvisorPanel mode visibility incl. Present-mode hide, controlled-open API for Reports integration, citation-verification affordance always rendered). |
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
**Agents:** Anthropic SDK (Claude Opus 4.6 + Sonnet 4.6 + Haiku 4.5), Google GenerativeAI SDK (Gemini 1.5 Pro), xAI HTTP API (Grok 3 Mini — Contrarian Analyst, Sprint 6), Anthropic server-side `web_search_20250305` tool (Academic Advisor, Sprint 6 Phase 9 — citation integrity)  
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

