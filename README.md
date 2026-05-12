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
| 4 | ⏳ Pending | All AI agents live (Equity, Fixed Income, Risk, Quant, Gemini, CIO, QA, Explainer), council WebSocket streaming, scope guard, rate limiting, credit cap, production deployment (Render + Vercel) |
| 5 | ⏳ Pending | Statistical Evidence + Regime Analysis dashboards, Commentary mode, ChartCommentStrip, export infrastructure (PNG/CSV/ZIP), Sanity Check panel |
| 6 | ⏳ Pending | Report generators (appendix PDF, executive brief, midpoint template), full regression suite, accessibility audit, presentation-ready demo |

## Test Counts (Sprint 3)

| Layer | Tests | Notes |
|-------|-------|-------|
| Backend (pytest) | 356 passed, 10 skipped | HMM tests skip on Windows (hmmlearn requires C++ build tools; passes in CI on Linux) |
| Frontend (Vitest) | 47 passed | Component and store tests |
| E2E (Playwright) | Non-blocking | Backend startup timing issue in CI — see Known Issues |

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
**Database:** PostgreSQL (asyncpg), 4 tables: `data_series_registry`, `market_data_monthly`, `market_data_daily`, `data_validation_log`  
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

**Issue #1 — E2E CI timeout (non-blocking)**  
The Playwright E2E job has `continue-on-error: true` in `.github/workflows/test.yml`. The backend starts correctly locally but the health check endpoint times out after 120s in GitHub's Linux runners. Root cause under investigation. Backend and frontend unit tests (356 + 47) are fully green. E2E must be fixed before Sprint 4 deployment.

**Issue #2 — HMM on Windows**  
`hmmlearn` requires Microsoft C++ Build Tools on Windows. 10 HMM tests are marked skip on Windows; they run and pass in CI on Ubuntu. Install Visual Studio Build Tools or use WSL to run locally.

## Sprint 4 Preview

Sprint 4 (targeting May 25 – Jun 1) will deliver:
- All 8 AI agents wired up and streaming (Equity, Fixed Income, Risk, Quant, CIO, Gemini, QA, Explainer)
- Council WebSocket endpoint with token-by-token streaming
- Scope guard (Haiku-powered in-scope classifier before every council query)
- Rate limiting (slowapi) and daily credit cap enforcement
- Production deployment: Render (backend) + Vercel (frontend)
- Magic link email via SendGrid in production
- All 4 team members can log in at the live URL before June 3 mid-checkpoint

## Team

| Name | Role |
|------|------|
| Michael Ruurds | Lead Engineer (solo dev, 20 hrs/week) |
| Bob | Lead Analyst — written report, methodology, academic interpretation |
| Molly | Lead Presenter — slide deck, Forest Capital brief, July 1 demo |
| Dr. Panttser | Professor / Reviewer |

Key dates: Mid-checkpoint June 3 @ 6pm · Final presentation July 1 @ 6pm · McEwen 120

