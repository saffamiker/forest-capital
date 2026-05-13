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
| 6 | ⏳ Pending | Report generators (analytical appendix, executive brief, midpoint template), Storyboard Editor, Script Writer, Version Control, Gemini Assistant panel, full regression suite, accessibility audit, presentation-ready demo |

## Test Counts (Sprint 5)

| Layer | Tests | Notes |
|-------|-------|-------|
| Backend (pytest) | 651 passed, 10 skipped | HMM tests skip on Windows (hmmlearn requires C++ build tools; passes in CI on Linux) |
| Frontend (Vitest) | 73 passed | Component, store, and export tests |
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
**Agents:** Anthropic SDK (Claude Opus 4 + Sonnet 4), Google GenerativeAI SDK (Gemini 2.0 Flash)  
**Frontend:** React 18, TypeScript 5 (strict), Vite, TailwindCSS, Recharts, Zustand, React Query  
**Database:** PostgreSQL (asyncpg), 8 tables: `data_series_registry`, `market_data_monthly`, `market_data_daily`, `data_validation_log`, `strategy_results_cache`, `regime_signals_cache`, `auth_attempts`, `used_magic_tokens`  
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

## Sprint 6 Preview

Sprint 6 (targeting Jun 22 – Jul 1) will deliver:
- Academic Writer Agent — APA 7th edition report generation (appendix, brief, midpoint)
- Storyboard Editor — drag-to-reorder slides, timing bar, owner assignment, speaker notes
- Script Writer — full team + individual scripts, rehearsal guide with timing cues
- Version Control — named snapshots, auto-save every 30s, restore from any prior version
- Gemini Assistant panel — inline natural language editing for storyboard and documents
- Full regression suite + performance benchmarks (p95 response times)
- Accessibility audit (axe-core, WCAG AA)
- Final git tag: v1.0.0-presentation

## Team

| Name | Role |
|------|------|
| Michael Ruurds | Lead Engineer (solo dev, 20 hrs/week) |
| Bob | Lead Analyst — written report, methodology, academic interpretation |
| Molly | Lead Presenter — slide deck, Forest Capital brief, July 1 demo |
| Dr. Panttser | Professor / Reviewer |

Key dates: Mid-checkpoint June 3 @ 6pm · Final presentation July 1 @ 6pm · McEwen 120

