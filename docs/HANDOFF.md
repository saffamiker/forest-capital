# Forest Capital — Conversation Handoff
**Date:** May 15, 2026  
**For:** New Claude conversation session  
**Project:** FNA 670 MSFA Practicum — Forest Capital Portfolio Intelligence System

---

## LIVE URLS
- Frontend: https://forest-capital.vercel.app
- Backend: https://forest-capital.onrender.com
- Repo: C:\Users\micha\forest-capital

## CURRENT STATE
- Sprint 6: COMPLETE
- Kanban: active (post-sprint polish mode)
- Tests: 921 backend / 178 frontend
- Migrations: 001-007 (head)
- CI: all three jobs green including E2E

## TECH STACK
- Backend: FastAPI + PostgreSQL (asyncpg) on Render Hobby (512MB)
- Frontend: React/TypeScript/Vite on Vercel
- AI: Claude Sonnet/Opus 4.7, Gemini 1.5 Pro, Grok-3-mini (OpenRouter)
- Data: yfinance, FRED API, Ken French direct HTTP

## IMMEDIATE BUGS (fix before anything else)

### 1. Memory leak fix — PENDING PUSH
Commit is ready but not pushed. Root cause: `/api/v1/qa/status`
polls every 30s, each poll calls `_read_history_from_db()` which
creates a fresh `create_async_engine()` + `asyncio.run()`. Over
3.5h this leaks ~35% memory (65% → 100% OOM).

Fix approved:
- Reuse database.py module-level pooled engine
- Memoize `get_full_history()` with 30s TTL
- Replace per-call ThreadPoolExecutor in qa_tiered.py with singleton

**Action:** Push the pending commit and deploy.

### 2. Efficient frontier blank
`POST /api/optimize/weights` returns 401 with "Provide X-API-Key"
even with valid X-Session-Token. The endpoint has wrong auth
dependency — using master key auth instead of session auth.

**Fix:** In main.py change the auth dependency on
`POST /api/optimize/weights` from `require_api_key` to
`get_current_user` (session-based auth matching all other endpoints).

### 3. FF factors fetching every request (partially fixed)
`months_behind >= 3` threshold set but currently db_last=202603
and today=202605 so months_behind=2 — still fetching every request.
Ken French hasn't published April 2026 data yet.
Will self-resolve when KF publishes April data (db_last becomes 202604).
**No action needed — monitor.**

### 4. Performance Attribution Waterfall
BENCHMARK monthly_returns fix shipped (commit 9767cb8).
Should now show real values. Verify on live site.

---

## KANBAN — CURRENT PRIORITIES

### CRITICAL (before June 3)
- [ ] Push memory leak fix commit
- [ ] Fix efficient frontier auth (wrong dependency)
- [ ] Verify Performance Attribution Waterfall working
- [ ] Level 1 code review audit
- [ ] Team Test Guide (docs/TEAM_TEST_GUIDE.md)
- [ ] Execute test script with Bob + Molly
- [ ] Fix punch list from test results
- [ ] Professor link sent (~May 20)
  - Add professor email to ALLOWED_EMAILS on Render
  - Two email variants drafted and ready

### IMPORTANT (before July 1)
- [ ] Backtest period locked at December 2025 for academic deliverables
  Add BACKTEST_END_DATE = 2025-12-31 config parameter
  Live dashboard keeps current regime indicator
  Written paper and presentations use fixed study period
- [ ] Significance framing — MetricTile amber note update
  "3 strategies show meaningful outperformance" in Present mode
- [ ] Explainer §2.2 council persona explanations
- [ ] Grok upgrade grok-3-mini → grok-3
- [ ] Demo rehearsal end-to-end
- [ ] v1.0.0-presentation git tag

### POLISH (nice to have)
- [ ] Team Primer styled modal (replaces static .md)
- [ ] WCAG AA audit
- [ ] Performance benchmarks

---

## KEY ARCHITECTURAL DECISIONS

### Data Architecture
- PostgreSQL is source of truth for all historical data
- market_data_monthly: 282 rows (2002-2025 Excel + LQD bridge + yfinance incremental)
- ff_factors_monthly: 1197 rows (Ken French direct HTTP)
- Strategy results cached in strategy_results_cache (JSONB)
- Regime signals cached in regime_signals_cache (JSONB, 15min TTL)

### Study Period Decision
Backtest period should be LOCKED at December 2025 for academic deliverables.
The 2022 equity-bond correlation breakdown is the central finding
(pre-2022 avg: 0.06, post-2022 avg: 0.68). This must remain
structurally significant and not be diluted by new post-2022 data
as bonds potentially revert to negative correlation.
Live dashboard regime indicator continues to use current data.

### Significance Framing
0/10 strategies pass all 5 Tier 1 gates at p < 0.005 (FDR corrected).
This is NOT a failure — it is honest rigour per Benjamin et al. (2018).
Three strategies show economically meaningful outperformance:
- Regime Switching: Sharpe 0.63 vs 0.52 benchmark (+11 bps)
- Momentum Rotation: Sharpe 0.58 (+6 bps)
- Equal Weight: Sharpe 0.57 (+5 bps)
Always frame with economic significance alongside statistical threshold.

### AI Models (current)
- CIO: claude-opus-4-7 (upgraded from opus-4, retirement Jun 15)
- Agents: claude-sonnet-4-6
- QA Tier 3: claude-opus-4-7
- Explainer: grok-3-mini via OpenRouter (sk-or- prefix)
- Independent Analyst: gemini-1.5-pro
- Contrarian Analyst: grok-3-mini via OpenRouter
- Academic Advisor: claude-sonnet-4-6 + web_search

### XAI/OpenRouter Config
Auto-detection by key prefix in agents/_xai_config.py:
- sk-or-... → OpenRouter (https://openrouter.ai/api/v1, model x-ai/grok-3-mini)
- xai-... → Direct xAI (https://api.x.ai/v1, model grok-3-mini)
XAI_BASE_URL and XAI_MODEL env vars override auto-detection.

---

## RENDER SHELL COMMANDS (frequently needed)

### Clear strategy cache
```bash
python -c "
import asyncio, os, asyncpg
async def run():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(url)
    await conn.execute('DELETE FROM strategy_results_cache')
    print('Cache cleared')
    await conn.close()
asyncio.run(run())
"
```

### Check migration version
```bash
cd backend && alembic current
```

### Check FF factors row count
```bash
python -c "
import asyncio, os, asyncpg
async def run():
    url = os.environ['DATABASE_URL'].replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(url)
    count = await conn.fetchval('SELECT COUNT(*) FROM ff_factors_monthly')
    print('FF factors rows:', count)
    await conn.close()
asyncio.run(run())
"
```

---

## RECENT COMMIT LOG (last 10)
```
9767cb8  Fix BENCHMARK monthly_returns missing from backtester
dd17903  Fix optimize_weights bogus assets kwarg
83648d9  Update claude-opus-4-6 → claude-opus-4-7 everywhere
21d72ef  Attribution diagnostic logs + FF staleness threshold=3
88c076b  XAI OpenRouter auto-detection via _xai_config.py
083dc05  Opus 4 → 4.7 model upgrade
0527167  FF factors optional args + DB-driven incremental
a04b853  Close Sprint 6 — README + MANIFEST updated
5bce9fe  Four bug fixes (hmm_regime VARCHAR, Grok 400, JSON truncation, date column)
```

---

## PRESENTATIONS
- June 3: Midpoint check-in (Queens University McColl School of Business)
- July 1: Final presentation to Forest Capital
- Professor outreach email: ~May 20 (two variants drafted)
- Team: Michael (lead engineer), Bob Thao (analysis), Molly Murdock (presentation)

## PROFESSOR EMAIL
Two variants ready to send — "Technical focus" and "Concise and direct".
Add professor email to ALLOWED_EMAILS on Render before sending.
Target: May 19-20 after code review and test script complete.

---

## CLAUDE.md LOCATION
The full project spec is in CLAUDE.md (8,190 lines) in the repo root.
Always push CLAUDE.md updates:
```powershell
git add CLAUDE.md
git commit -m "CLAUDE.md — [description]"
git push
```

## HOW TO CONTINUE
1. Start new Claude conversation
2. Paste this HANDOFF.md as context
3. Reference CLAUDE.md for full spec
4. Pick up from IMMEDIATE BUGS section above
