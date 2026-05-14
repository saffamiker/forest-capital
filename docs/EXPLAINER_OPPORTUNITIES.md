# Explainer Agent Opportunity Review

**Sprint 6 final-polish audit · 2026-05-14**

Now that the Explainer Agent is (a) routed to Grok-3-mini at low cost,
(b) cached idempotently in `glossaryStore`, and (c) wired through the
`ExplainableText` / `ChartCommentStrip` components, the cost of adding
a new explainable surface is low — typically one `<ExplainableText>`
wrapper or one `loadParameter()` call. This report inventories every
place the agent could plausibly add value, with priority and effort
estimates so Michael can triage which to action before July 1.

---

## Scope of current usage

| Surface | Endpoint | Where it fires |
|---|---|---|
| Dashboard summary tiles (4 metrics) | `/api/explain/terms` | `MetricTile` `term=` prop |
| Cumulative Returns chart | `/api/explain/chart` | `ChartCommentStrip` below the chart |
| Statistical Evidence (6 charts) | `/api/explain/chart` | One strip per chart |
| Regime Analysis (6 charts) | `/api/explain/chart` | One strip per chart |
| Learn More side panel | `references.json` only | Click "Learn more" on any tooltip |

**Three Explainer namespaces are wired in the store but have zero
consumers:**
- `loadParameter` — `/api/explain/parameter` (config-parameter explanations)
- `loadPersona` — `/api/explain/persona` (agent system-prompt explanations)
- `loadQA` — `/api/explain/qa` (30-point QA checklist explanations)

Every opportunity below is one of: (a) extending the `terms` namespace
to a new wrapped surface, or (b) wiring up one of the three idle
namespaces.

---

## 1. HIGH priority · trivial effort

These are 1–2 line edits that ship more explainer value per minute of
work than anything else on the list. Each is a strict subset of work
already proven on the Dashboard summary tiles.

### 1.1 Strategy comparison table column headers

**Location:** [`frontend/src/components/Dashboard.tsx:364`](frontend/src/components/Dashboard.tsx#L364)
**Description:** The header row contains the 7 most-asked-about
acronyms in the entire app (`CAGR`, `Sharpe [95% CI]`, `Max DD`,
`DSR`, `p (FDR)`, `CV Score`, `Tier 1`). Currently rendered as raw
strings in a `.map()`. Wrap each in `<ExplainableText term="...">` so
clicking the column header opens the standard Level-2 panel with what
/ why / in-context.
**Priority:** HIGH — every dashboard visitor reads this row
**Effort:** trivial (~10 minutes)
**Endpoint:** `terms` (already loaded by the page)
**Implementation note:** The 10 strategy rows themselves are
deliberately not wrapped — wrapping cells would render 70 instances
per page. Headers are the right grain.

### 1.2 Significance Journey Matrix column headers

**Location:** [`frontend/src/components/charts/SignificanceJourneyMatrix.tsx:97`](frontend/src/components/charts/SignificanceJourneyMatrix.tsx#L97)
**Description:** Same five columns as the strategy table (T-TEST, FDR,
DSR, OOS, CV). The matrix already has a `metricName` field on each
gate definition that would be a perfect tooltip source. Wrap the
column header `<th>` cells in `<ExplainableText>` keyed by a stable
term ID (`tier1_t_test`, `tier1_fdr_correction`, etc.).
**Priority:** HIGH — this matrix is on the Analytical Appendix-facing
Statistical Evidence screen, audience scrutiny is highest here
**Effort:** trivial
**Endpoint:** `terms`

### 1.3 Regime Indicator badge tooltip

**Location:** [`frontend/src/components/RegimeIndicator.tsx`](frontend/src/components/RegimeIndicator.tsx)
**Description:** The Dashboard header shows `BULL · VIX 14.3 · 10Y-2Y
0.42` etc. The regime label itself (`BULL`/`BEAR`/`TRANSITION`) is the
single most-asked-about element. One `<ExplainableText
term="regime_classification">` wrapping the badge text covers it.
**Priority:** HIGH
**Effort:** trivial

### 1.4 2022 Correlation Breakdown warning banner

**Location:** [`frontend/src/components/Dashboard.tsx:228`](frontend/src/components/Dashboard.tsx#L228)
**Description:** The amber banner under the regime indicator explains
the 2022 equity-bond correlation breakdown — the central project
finding. The phrase "Equity-Bond Correlation Breakdown" is a perfect
explainable term. Wrap the bolded heading.
**Priority:** HIGH — central project finding
**Effort:** trivial

---

## 2. HIGH priority · moderate effort

These each take 30–90 minutes but unlock surfaces that are currently
silent.

### 2.1 QA Audit panel — `explain_qa` integration

**Location:** [`frontend/src/components/QAAuditPanel.tsx:32`](frontend/src/components/QAAuditPanel.tsx#L32)
**Description:** The QA Audit screen renders 30 checklist items with
description, evidence, and fix. The expanded check rows have empty
real estate where a "What this check tests / Why it matters / What
failure would mean for THIS audit" panel would be high-value. The
backend `/api/explain/qa` endpoint exists and the `loadQA` store
method is wired — nothing consumes it. Call `loadQA(audit.items)` on
audit load; render `glossaryStore.qa[check_id].what / why /
failure_meaning / how_tested` inside the expanded row.
**Priority:** HIGH — QA is the methodology-defence screen, Bob will
spend the most time here writing the Analytical Appendix
**Effort:** moderate (one new useEffect, four new sections inside the
expanded row, plus type plumbing)
**Endpoint:** `loadQA` / `/api/explain/qa`
**Cost note:** Fires one Grok call when the audit loads. Once per
session, cached after.

### 2.2 Council Debate — "View system prompt" persona explanations

**Location:** [`frontend/src/components/CouncilDebate.tsx:23-99`](frontend/src/components/CouncilDebate.tsx)
**Description:** Each `AgentCard` renders the agent's name, accent
colour, and `message.content`. The CLAUDE.md spec calls for a "View
system prompt" link on each card that opens a modal with three tabs:
**PROMPT** (verbatim), **PLAIN ENGLISH** (Explainer-generated), **THIS
SESSION** (Explainer-generated). The `loadPersona` store method is
wired but unused.
**Priority:** HIGH — the audience will ask "why does the council have
six agents?" on June 3 and July 1; this is the explanatory surface
that answers it
**Effort:** moderate (~2 hours — new modal component, fetch system
prompts from a new `GET /api/agents/personas` endpoint or constants
file, wire `loadPersona` calls on tab open)
**Endpoint:** `loadPersona` / `/api/explain/persona`
**Implementation note:** The system prompts already exist in
`backend/agents/*.py` as `_SYSTEM_PROMPT` constants. A small `GET
/api/agents/personas` endpoint that returns `{agent_name:
{prompt, model}}` keeps the prompts in one place.

### 2.3 Sanity Check panel — explain each of the 10 checks

**Location:** [`frontend/src/components/SanityCheckPanel.tsx`](frontend/src/components/SanityCheckPanel.tsx)
**Description:** The 10 sanity assertions (S&P CAGR 8-12%, BND 2022
return, equity-bond correlation breakdown, etc.) display
expected/actual/status but no rationale. Wrapping each row in
`<ExplainableText term="sanity_check_${id}">` lets the user click and
learn why each threshold exists. Could share the `loadQA` namespace
since these are methodology checks.
**Priority:** HIGH — Sanity Check is the data-integrity defence
**Effort:** moderate (10 new term IDs in the explainer prompt + wrap
sites)

### 2.4 Strategy detail card (selected-strategy expansion)

**Location:** [`frontend/src/components/Dashboard.tsx:380+`](frontend/src/components/Dashboard.tsx) (when `selectedStrategy` is set)
**Description:** Clicking a row in the strategy table opens an
expanded card with `StrategyCard` showing all the metrics for that
strategy. The metric labels in that card are not wrapped. Same
treatment as the table headers would work — wrap each label.
**Priority:** HIGH (it's the deep-dive surface for picking a recommendation)
**Effort:** moderate (one `<ExplainableText>` per label inside
StrategyCard)

---

## 3. MEDIUM priority · trivial effort

Polish work that nobody specifically asked for but each fits in a
single commit.

### 3.1 Chart axis labels and reference lines

**Location:** All 12 chart components in `frontend/src/components/charts/`
**Description:** Each chart has X/Y axis labels and `<ReferenceLine>`
annotations (e.g. `pre-2022 average -0.31`). Currently static
strings. Wrapping them in `<ExplainableText>` would let the user
click any axis label for context. Cost is low because the labels are
short and frequently identical across charts (`Sharpe`, `OOS Sharpe`,
`Cumulative return`).
**Priority:** MEDIUM
**Effort:** trivial per label, but there are ~30 across all charts

### 3.2 MetricTile sub-text labels on Dashboard

**Location:** [`frontend/src/components/Dashboard.tsx:264-280`](frontend/src/components/Dashboard.tsx#L264-L280)
**Description:** Sub-text under each MetricTile ("Pass all 5 Tier 1
gates", "Walk-forward out-of-sample", "100% SPY 2002–2024") is
informative but not explainable. For the Tier 1 sub-text in particular,
wrapping `Tier 1 gates` would let users click directly from the sub-text.
**Priority:** MEDIUM
**Effort:** trivial

### 3.3 Disagreement Heatmap row labels

**Location:** [`frontend/src/components/DisagreementHeatmap.tsx:130`](frontend/src/components/DisagreementHeatmap.tsx#L130)
**Description:** Strategy names in the leftmost column are rendered
as plain text. Wrapping each in `<ExplainableText
term="strategy_${name}">` would let users click for "what does
REGIME_SWITCHING actually do" without leaving the Council screen.
**Priority:** MEDIUM
**Effort:** trivial
**Dependency:** Requires the `terms` namespace to grow per-strategy
entries — backend `explain_terms` prompt would need to be updated to
emit these, or a new term-id convention.

### 3.4 Provenance Sources line — clickable series IDs

**Location:** `ChartCommentStrip` Sources line (every chart in
Commentary + Present mode)
**Description:** "Sources" line currently formats as `S&P 500 Monthly
Returns: Excel (provided by Dr. Panttser) · BND: Excel (FRED) · …`.
Each series name could be a clickable explainable that opens a panel
with the source rationale, validation status, and Y-charts vs FRED
explanation.
**Priority:** MEDIUM (already meaningful as displayed; clickable would
deepen)
**Effort:** trivial (wrap each `display_name` in the sources line)

---

## 4. MEDIUM priority · moderate effort

### 4.1 Storyboard Editor slide-field hover hints

**Location:** [`frontend/src/pages/StoryboardEditor.tsx:463-540`](frontend/src/pages/StoryboardEditor.tsx)
**Description:** Each slide field (`Headline`, `Key point`, `Speaker
note`, `Transition`) has a `<Field label="...">`. Wrapping the label
in `<ExplainableText term="storyboard_field_${name}">` would let Molly
click each label for "what makes a good headline" / "speaker note
register guidelines" / etc.
**Priority:** MEDIUM (Molly's onboarding to the editor)
**Effort:** moderate (new term IDs + curated prompt content)

### 4.2 Reports card descriptions — generate preview

**Location:** [`frontend/src/pages/Reports.tsx:DeliverableCard`](frontend/src/pages/Reports.tsx)
**Description:** Each report card has a static description. An
Explainer call could enrich this with a session-specific preview:
"This midpoint paper will reference 0/10 significant strategies, a
benchmark CAGR of 8.54%, and the 2022 correlation breakdown finding."
**Priority:** MEDIUM (helps Bob/Molly preview before generating)
**Effort:** moderate (new `explain_report_preview` namespace or
re-use `loadParameter`)

### 4.3 Council Debate — per-message strategy mentions

**Location:** `AgentCard` content rendering, [`CouncilDebate.tsx:81`](frontend/src/components/CouncilDebate.tsx#L81)
**Description:** Agent narratives mention strategy names (REGIME_SWITCHING,
VOL_TARGETING, etc.) inline. Currently rendered as `whitespace-pre-wrap`
plain text. A post-render pass could detect known strategy names and
wrap each occurrence in `<ExplainableText>` so the audience can click
any mentioned strategy from any agent's text.
**Priority:** MEDIUM
**Effort:** moderate (regex pass over text + per-strategy term IDs)
**Risk:** Could fire many requests if not deduped through the cache
(but `glossaryStore.terms` already caches per-key, so safe).

---

## 5. MEDIUM priority · complex effort

### 5.1 Parameter Explorer panel

**Location:** New screen or Admin tab
**Description:** The `/api/explain/parameter` endpoint is wired (and
the `loadParameter` store method exists) but there's no UI consumer.
A panel that lists every config parameter from `backend/config.py`
(`P_THRESHOLD_PRIMARY`, `WALK_FORWARD_TRAIN`, `BL_TAU`, etc.) and
shows "current value · what it controls · what would change if you
moved it" would be a powerful methodology explainer.
**Priority:** MEDIUM (great for Bob writing the methodology section
of the Analytical Appendix)
**Effort:** complex (new page, parameter catalogue, prompt
engineering to keep the Explainer grounded in actual session results)

### 5.2 Live Demo / Present-mode running commentary

**Location:** New floating panel in Present mode
**Description:** During the July 1 demo, a small floating panel
visible only in Present mode could rotate through the 3 highlighted
charts' narratives at presenter-paced timing (auto-advance every
30s). Backed by the existing `loadChart` cache, so no new infra.
**Priority:** MEDIUM (rehearsal-pace aid)
**Effort:** complex (new component, timing coordination, mode
detection)

---

## 6. LOW priority · trivial effort

### 6.1 Login / Auth screens

**Location:** [`frontend/src/pages/LoginPage.tsx`](frontend/src/pages/LoginPage.tsx)
**Description:** "Magic link · scanner-safe · single-use" could each
be explainable. Likely overkill — these pages are seen once per session.
**Priority:** LOW

### 6.2 Team Primer cross-links

**Location:** `frontend/public/TEAM_PRIMER.md`
**Description:** Section headings in the Team Primer could deep-link
to the relevant ExplainableText panels in the app. Doesn't change UX
much because the user is already in the explainer-rich app.
**Priority:** LOW
**Effort:** trivial (anchor links)

### 6.3 Footer / branding line

**Location:** `MainLayout.tsx` (no footer currently — would need to add)
**Description:** Year range, build hash, last data refresh — all
could be explainable. None of them matter.
**Priority:** LOW

---

## 7. LOW priority · complex effort

### 7.1 Automatic regression alerting via Explainer

**Location:** New background job
**Description:** When strategy results materially shift between
pipeline runs (e.g. VOL_TARGETING Sharpe drops from 1.02 to 0.65),
the Explainer could generate a "what changed" narrative for the
Admin screen. Requires diffing two snapshots — not trivial.
**Priority:** LOW
**Effort:** complex
**Note:** Belongs in a Sprint 7-style "operational observability"
phase, not Sprint 6 polish.

### 7.2 Audio narration generation

**Location:** New endpoint
**Description:** Pipe the chart `narrative` outputs to a text-to-speech
service so each ChartCommentStrip has a "Play" button. Useful for
accessibility but adds a paid TTS dependency.
**Priority:** LOW
**Effort:** complex

---

## Summary by effort

| Effort | HIGH | MEDIUM | LOW |
|---|---|---|---|
| trivial | 4 (§1.1–1.4) | 4 (§3.1–3.4) | 3 (§6.1–6.3) |
| moderate | 4 (§2.1–2.4) | 3 (§4.1–4.3) | — |
| complex | — | 2 (§5.1–5.2) | 2 (§7.1–7.2) |

## Recommended slice for next commit

If picking a single follow-up session, the highest leverage subset is:

1. **§1.1** — Strategy table column headers (10 min)
2. **§1.2** — Significance Journey Matrix headers (10 min)
3. **§1.3** — Regime indicator badge (5 min)
4. **§1.4** — 2022 correlation banner (5 min)
5. **§2.1** — QA Audit `explain_qa` integration (~90 min)

Total: ~2 hours, unlocks the four most-scrutinised surfaces on the
Dashboard plus the methodology-defence screen Bob will spend the most
time on. Backend endpoints are all already live and tested — frontend
wiring only.

Items §2.2 (Council persona explanations) and §2.4 (strategy detail
card) are next-best HIGH-priority candidates but each takes ~2 hours
on their own.

---

*Report generated as part of the Sprint 6 UI/UX Agent Explainer
opportunity review. No code changes were made; Michael selects which
items to action.*
