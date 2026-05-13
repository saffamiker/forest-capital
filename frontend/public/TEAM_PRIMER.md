# Forest Capital Portfolio Intelligence System
## Team Primer — Using the Three Modes

Queens University · McColl School of Business · MSFA FNA 670 Practicum

---

## The Three Modes

The system has three operating modes selectable from the navigation bar.
Each serves a distinct purpose. Switch between them at any time — no data
is lost and no computations restart.

---

### ANALYST MODE (default)

**Who it is for:** Quants, analysts, Dr. Panttser reviewing methodology.

**What you see:** Full dashboard — all metrics, all technical columns.
DSR, P(FDR), CV Score, and Tier 1 gate counts are visible on every row.
QA Audit and Council are fully accessible. Maximum information density;
no explanatory chrome.

**When to use it:**
- Verifying numbers before a presentation
- Running the AI Council with a research question
- Checking the QA Audit status
- Exploring strategy results in depth

---

### 💬 COMMENTARY MODE

**Who it is for:** Bob writing the Analytical Appendix; any team member
preparing for Q&A; Forest Capital stakeholders who want context alongside
the numbers.

**What you see:** Everything in Analyst mode, plus:
- Comment strips below every chart — expand to see purpose, key findings,
  and suggested presentation narrative
- Underlined technical terms with hover definitions
- Analyst-register explanations drawn from actual session results —
  never generic boilerplate

**When to use it:**
- Bob: read every chart annotation before writing the Analytical Appendix.
  The Explainer Agent generates context specific to the actual results.
- Any team member: hover any underlined term to get its definition
  anchored to what the system actually found in this session.
- Preparing for Q&A: click any chart annotation to expand the full
  analyst note — it includes what the audience is likely to ask.

**Activate:** Click the `💬 Commentary` pill in the navigation bar.

---

### ⊞ PRESENT MODE

**Who it is for:** Molly presenting to Forest Capital on July 1st.
Forest Capital executives and the MSFA Board.

**What you see:** Clean view designed for a projected screen:
- Three key chart annotations auto-expanded (correlation breakdown,
  stress test comparison, significance matrix)
- All other annotations collapsed — expand on demand during Q&A
- Font sizes increased 10% for readability across a room
- Transitions slowed to 400ms — deliberate, not snappy
- Agent council summaries always visible below each agent card

**Access requirement:** Present mode is **locked** until the QA Audit
has been run and the result is WARN or PASS.

- **Not yet run** → clicking Present navigates to the QA Audit screen.
  Run the audit first.
- **FAIL** → Present mode remains locked (red 🔒 indicator).
  Review and resolve failing checks before the presentation.
- **WARN** → Present mode is unlocked with an amber badge.
  The limitations are disclosed — acceptable for presenting.
- **PASS** → Present mode is fully unlocked, no badge shown.

**When to use it:**
- Molly: the day before July 1st, open Present mode and walk through
  the full demo flow. Confirm branding is correct (Forest Capital toggle
  in the Settings cog, top-right).
- During the live demo on July 1st: stay in Present mode throughout.

---

## Before the Midpoint (June 3)

**Michael:** Confirm the backend is live on Render and all strategy
results are loading from the database (not recomputing on every request).
Verify the QA Audit runs successfully and returns results.

**Bob:**
1. Open Commentary mode
2. Read every chart annotation on the Main Dashboard
3. Hover every underlined metric to see its session-specific definition
4. Run the AI Council with the research question:
   *"Does diversification across equities and fixed income improve
   risk-adjusted performance relative to a 100% equity benchmark?"*
5. Review the QA Audit — understand which items show WARN and why
6. Use the Explainer Agent explanations as drafting material for the
   Analytical Appendix — verify every number before including it

**Molly:**
1. Open Present mode (run QA Audit first if needed)
2. Verify branding switches correctly to Forest Capital
3. Walk through all five dashboard screens as if presenting
4. Note which charts you plan to highlight — the comment strips
   include suggested presentation narrative for each

---

## Before the Final Presentation (July 1)

**Molly:**
- Open Present mode and complete a full dry run
- Confirm QA status is WARN or PASS
- Test the AI Council live demo flow — have two or three queries ready
- Export the presentation pack (Present mode → Export Pack button)
  for backup slides

**Bob:**
- Export the Analytical Appendix from the Reports screen
- Verify every number in the written brief against the system output
- Prepare answers to the statistical methodology questions the audience
  is most likely to ask (use Commentary mode → QA Audit annotations)

**Michael:**
- Confirm Render is on the paid tier (no cold-start delays during demo)
- Verify all five sanity checks pass (QA Audit → Sanity Check tab)
- Have the Admin screen ready to show data provenance if asked

---

## Key Numbers to Know

| Metric | Value | What it means |
|--------|-------|---------------|
| Monthly observations | 282 | Jan 2002 – Dec 2024; adequate power at p < 0.005 |
| Significance threshold | p < 0.005 | Benjamin et al. 2018 — stricter than conventional 0.05 |
| FDR correction | q < 0.005 | Applied across all 10 strategies simultaneously |
| CV Stability threshold | ≥ 0.60 | Minimum for a strategy to be recommended |
| Benchmark Sharpe | ~0.52 | 100% SPY over the full period |
| 2022 correlation | +0.48 avg | The central project finding — bonds failed to diversify |
| Pre-2022 correlation | −0.31 avg | Historical norm — bonds rose when equities fell |

---

## Frequently Asked Questions

**"Why does 0 of 10 strategies show as significant?"**
This is an honest result — intentionally honest. The threshold is
p < 0.005 with Benjamini-Hochberg FDR correction across all 10
strategies tested simultaneously. Passing all five Tier 1 gates at
this threshold simultaneously is extremely demanding. Strategies may
pass individual tests but fail others (e.g., pass full-period but fail
out-of-sample). The system reports this correctly rather than cherry-picking.

**"Can I trust the numbers?"**
Every metric is computed from the same pipeline the backend runs on
every session. The Sanity Check panel (QA Audit → Sanity Check tab)
validates 10 known historical values against expected ranges — S&P 500
CAGR, 2008 drawdown, 2022 bond return, HY spread peak, and others.
All five must be green before the presentation.

**"What does the AI Council actually do?"**
Six AI agents (five Claude, one Gemini) each analyse a different
dimension of the portfolio question — equity conditions, fixed income
diversification, risk management, backtesting rigour, and an
independent challenge from a separate AI system. The CIO (Claude Opus)
synthesises all inputs and makes a final recommendation. The QA Agent
audits the results independently and reports directly to Michael.

**"Why is Gemini on the council?"**
Groups of similar thinkers miss the same things. Gemini's role is
explicitly to challenge whatever the Claude agents conclude. Because
it has different training data and different tendencies, it surfaces
risks and alternative interpretations the Claude models might overlook.
The council is designed to disagree — the CIO must engage with Gemini's
challenge before making a final recommendation.

---

*Forest Capital Portfolio Intelligence System · Queens University McColl School of Business*
*MSFA FNA 670 Practicum · 2026 · Confidential*
