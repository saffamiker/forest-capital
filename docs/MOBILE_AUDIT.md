# Mobile Audit — 380px Viewport

**Audit date:** 2026-05-19
**Scope:** Every page and major component surfaced by the brief, audited
at a 380px viewport (the iPhone-SE-portrait-class minimum the project
targets).
**Method:** Static read of the production frontend code; cross-checked
against `docs/mobile_checklist.md` (the May-17 mobile-responsive build)
to flag any surface that has regressed or was added since.

**Severity legend:**
- **BLOCKING** — unusable; the surface either cannot be operated by
  touch / a 380px viewport, or essential controls overflow off-screen.
- **DEGRADED** — works but poorly; functions but with cramped layout,
  truncated text, or excessive horizontal scrolling.
- **MINOR** — cosmetic only; no functional impact.

---

## 1. Dashboard

| Item | Status | Notes |
|---|---|---|
| Summary metric tiles | **MINOR** | 2-up on mobile via the mobile-responsive build. At 380px each tile is ~170px wide — readable; the larger numbers stay legible. |
| Strategy table | **DEGRADED** | `overflow-x-auto` with a sticky-left Strategy column lands. The reduced-column set + the "More columns" toggle works. At 380px the metric columns are 70-80px wide each — cramped but functional. Cell numerics stay readable; column header labels (eg `Excess Return`) wrap to two lines, sometimes three. Fix: trivial — tighter column-label abbreviations (`Excess Return` → `Excess`). |
| Significance journey matrix | **BLOCKING** | Row-per-gate × column-per-strategy with one PASS/FAIL dot per cell. The matrix renders inside the `significance_journey` chart (PNG, server-rendered) on the **canvas editor only** — on the Dashboard there is no equivalent live matrix. The chart's PNG fits inside its parent at 380px (`w-full` on the `<img>`). No blocker on the Dashboard itself. |
| Commentary Mode tooltips | **DEGRADED** | `InfoIcon` ⓘ buttons are 24px touch targets — short of the 44px minimum the project's mobile checklist asks for. The hover tooltip works on touch (tap-to-open), but the click handler opens an `ExplainerPanel` bottom sheet which DOES land correctly at ~60vh. The fix is at the InfoIcon level — bump the wrapping `<button>` to `min-h-[44px] min-w-[44px]`. **Fix: trivial CSS.** |

---

## 2. Analytics pages (AcademicAnalytics + Statistical Evidence + Regime Analysis)

| Item | Status | Notes |
|---|---|---|
| Chart rendering | **DEGRADED** | Every chart is wrapped in `<ResponsiveContainer width="100%" height={...}>` — the chart fills the viewport width at 380px. Y-axis labels with percent format (`tickFormatter={(v) => f"{v*100:.0f}%"}`) tend to overlap the leftmost data points on a narrow viewport. Legends below the chart wrap, sometimes to four rows for the 10-strategy series. **Fix: trivial CSS** — `margin={{ left: 0 }}` already set; chart heights should drop on mobile (`height={240}` instead of `320`). |
| AcademicAnalytics tables | **DEGRADED** | All four tables wrap in `overflow-x-auto` with `sticky left-0 z-10 bg-navy-800` on the first column. Lands. The Summary Statistics table has 9 columns of which 5 are numeric — at 380px ~3 columns visible at a time without scrolling. Header labels like "Annualised Volatility" wrap. The Factor Loadings table has 7 columns — at 380px ~2 columns visible. **Functional but cramped.** Fix: minor — abbreviate the longest column labels. |
| Regime Analysis | **DEGRADED** | Same overall pattern. The Regime Transition matrix renders as a small grid which is naturally responsive; the Factor Exposure Heatmap is wide and scrolls horizontally. |
| Statistical Evidence | **DEGRADED** | Same. The Significance Journey Matrix on Statistical Evidence is a recharts ComposedChart — it scales to width but the strategy-axis labels rotate 35° which becomes unreadable when there are 10 strategies in a 380px-wide chart. **Fix: trivial CSS** — rotate to 90° (vertical) on mobile. |

---

## 3. Reports page

| Item | Status | Notes |
|---|---|---|
| Document generation cards | **MINOR** | `grid grid-cols-1 md:grid-cols-3 gap-4` — single column on mobile. Each card stacks naturally. Buttons fill the card width. |
| Team Activity charts | **MINOR** | TeamActivityCharts renders timeline + commits as recharts; `width="100%"` fills the viewport. Legend wraps. Works. |
| Submission Guide panel | **DEGRADED** | The Submission Guide on the Reports header is a button that opens a side drawer. The drawer's CSS classes are not in this read — verify by inspection that it lands as a full-width bottom sheet on mobile (the established pattern). Likely fine. |
| Generate → Editor flow | **DEGRADED** | The Generate button POSTs and returns a job_id; the panel then shows "Generating…" with a Cancel button. Once complete, **Open in Editor** navigates to the in-app document editor (see surface 4). The async generation flow works on mobile — the toast announcement (`GenerationToast`) lands as a small fixed-position card. The destination (`/editor/:id`) is the blocking surface. |

---

## 4. Document editor (`/editor/:draft_id`) — TipTap path (midpoint, brief, script)

| Item | Status | Notes |
|---|---|---|
| Three panel layout | **BLOCKING** | The layout is `left aside (220px) + main (flex) + right aside (300px)`. At 380px viewport that is 520px of fixed-width chrome alone — both panels overlap or push the centre off-screen. The header has toggle buttons (`PanelLeftClose` / `PanelRightOpen`) so a user CAN close both panels, but the default state on first render is `leftOpen=true, rightOpen=true`. A first-time mobile user sees broken overlap, not a usable editor. **Fix: component change.** Below `lg` the panels need to (a) default closed, (b) when opened render as full-screen overlays (bottom sheet or drawer), not as side asides. |
| TipTap editor toolbar | **DEGRADED** | The TipTap toolbar (formatting buttons) is inside `RichTextEditor`. Not measured at 380px in this read — likely fits in one row or wraps to two. The text input area itself is `flex-1` so it fills whatever the main column has. Once panels are closed, the editor lands. |
| Left navigator panel | **DEGRADED** | 220px wide aside — at 380px it consumes 58% of the viewport. If opened on mobile it should render as a full-width overlay, not a fixed-width column. Same fix as the three-panel layout. |
| Writing Assistant panel | **DEGRADED** | 300px wide aside — at 380px it consumes 79%. Same overlay treatment needed. The Academic Review script note (`presentation_script`-only) and the rehearsal note are both small text blocks that wrap fine — the panel chrome is the blocker, not its contents. |
| `[[BOB]]` callout panels | **MINOR** | Render inline within the editor body. They are amber block panels with a "Mark as Complete" button — at 380px the button stays full-width within the block. Works. |
| `[[VERIFY]]` popups | **DEGRADED** | The verify popup floats anchored to a span in the editor. On mobile the floating popup may extend beyond the viewport edge if the underlying span is near the right edge. **Fix: trivial CSS** — clamp the popup with `max-w-[calc(100vw-32px)]`. |

**Bottom line:** the document editor is BLOCKING on mobile. The minimum fix is the three-panel-to-overlay change. Acceptable scope-cut: ship a banner that says "Open this in a larger window to edit" and disable touch-edit on mobile. The midpoint paper, executive brief, and script editors share this same blocker.

---

## 5. Canvas editor (`presentation_deck`)

| Item | Status | Notes |
|---|---|---|
| 960×540 Stage in 380px viewport | **DEGRADED** | The Konva `Stage` scales the 960×540 canvas to fit the available area (`setScale(Math.max(0.2, Math.min(1, w / CANVAS_WIDTH, h / CANVAS_HEIGHT)))`). At 380px viewport with both panels closed, `w ≈ 348`, so `scale ≈ 0.36`. The canvas renders at roughly 345×195 — readable text drops to ~5px equivalent. Functional for review, **not** functional for editing — text is too small to position, the Konva Transformer handles are too small to grab on a touch screen. |
| Toolbar buttons | **DEGRADED** | Located inside `CanvasSlideEditor.tsx`. Standard `lucide-react` icon buttons — should be the existing 44px touch-target pattern but were probably built at desktop pixel densities. Worth eyeballing in a real browser. |
| Chart picker drawer | **BLOCKING** | The right panel becomes the `ChartPicker` when adding a chart. The `ChartPicker` itself uses `w-full` cards internally, so its container is the gate. As long as the document-editor right panel renders as a 300px aside on mobile (see surface 4), the chart picker is unusable. After the three-panel overlay fix, the chart picker drawer rides on top correctly. **Fix: same as the document editor.** |
| Speaker notes field | **MINOR** | Inside the canvas editor, the speaker-notes textarea sits below the stage. Standard textarea, `w-full`, wraps text — works on mobile. |
| Left navigator | **DEGRADED** | Same EditorNavigator as the TipTap editors. The speaker-assignment badge dropdown is fiddly on touch (44px absolute mini-dropdown) but functional. Fix is the same overlay treatment. |

**Bottom line:** the canvas editor is the surface least suited to a 380px viewport — the canvas IS the working surface, and a 0.36× scale makes pixel-precise editing impossible. **Minimum fix: a "View only" banner on mobile** that disables the Transformer and the chart picker but lets a user navigate slides and read content. Real editing remains a desktop operation. Scope-cut: post-deadline backlog.

---

## 6. Script editor (`presentation_script`)

Same three-panel layout as surface 4. Adds two surfaces:

| Item | Status | Notes |
|---|---|---|
| Delivery time indicator | **MINOR** | Single line in EditorNavigator: `~22 min delivery · 3300 words` with a colour tone (green ok / amber warn). At 380px this fits on one line inside the 220px navigator. Works. |
| Speaker section navigator | **DEGRADED** | The navigator shows one section per slide with the speaker label. On mobile the slide-N-title and speaker line wrap. Functional but visually noisy. Fix: trivial CSS — single-line truncation with `truncate` on the heading. |

The Academic Review script note + the rehearsal footnote (May 19) both render correctly — they are small text blocks inside the existing navigator/assistant panels. Their blocker is the panel chrome, not the notes themselves.

---

## 7. QA page

| Item | Status | Notes |
|---|---|---|
| Audit panels | **DEGRADED** | The QA Audit panel (mode-aware tabs: Methodology / Statistical) renders as a tabbed view. Tabs stack vertically below `sm`. The Sanity Check panel and the 39-item methodology checklist both work on mobile — each check is a full-width row with PASS/WARN/FAIL badge. |
| WARN acknowledge workflow | **MINOR** | The "Acknowledge" button on a WARN row is small (~32px height). Worth bumping to 44px. **Fix: trivial CSS.** |
| Methodology checklist | **MINOR** | Each check is a card; click expands the four-section explanation. At 380px the expansion panel content is readable but the "WHAT IS BEING TESTED / WHY IT MATTERS" labels wrap. Acceptable. |

The Run Full QA / Presentation View buttons stack full-width on mobile. The presentation-view certificate view (when triggered) is desktop-oriented but acceptable as a presentation surface (not for mobile use).

---

## 8. Settings

| Item | Status | Notes |
|---|---|---|
| Users table | **DEGRADED** | UserManagementPanel — wide table with name / role / status / last login / activity count / AI cost / actions. Wrapped in `overflow-x-auto` with a sticky first column. Lands. |
| Activity breakdown | **N/A** | Not yet built — directive queued (see the queue note at the end of this audit). |
| Test runner | **DEGRADED** | The TestRunner panel is a bottom sheet on mobile (capped at 50vh per the mobile checklist). Lands. The TestResultsSection / TestAdminSections sections inside Settings are vertical card grids — each card is full-width on mobile. Works. |
| Account settings | **MINOR** | The four-section vertical stack is fine on mobile. The brand switcher (McColl / Forest Capital) renders as two selectable rows — comfortable touch targets. |

---

## 9. Council / Academic Review

| Item | Status | Notes |
|---|---|---|
| Message input | **MINOR** | The query input field + submit button stack full-width below `sm`. The Cancel button while a council query is in flight is also full-width. Works. |
| Response rendering | **MINOR** | Council messages render as agent cards (Markdown-rendered). Each card is full-width on mobile. The "Ask the Council about this" pre-filled question from the Explainer drawer works — the route state carries through. |
| Agent cards | **MINOR** | Each agent card is full-width. Cards stack vertically. The verdict from Academic Review renders below the cards — long markdown content with section ratings. Each section is its own block, readable on mobile. |

---

## 10. Navigation

Covered by the existing mobile-responsive build. Confirmed:

- `lg:hidden` hamburger at top-left below 1024px.
- MobileNavDrawer slide-in from the left with dark overlay.
- Three groups (Analysis / AI and Review / Output) with active-route highlighting.
- Mode switcher inside the drawer (not in the nav bar on mobile).
- Header pills: Testing Mode and QA Running both have `min-[380px]` queries to drop their text labels and show only the glyph below 380px width — works.
- Submission Guide button — sits on the Reports header, not the global nav, so unaffected.

**Status: MINOR** across the board. The nav system is mobile-complete.

---

## Summary — fixes required to make the platform usable at 380px

### BLOCKING (must fix before mobile is supported)

1. **Document editor three-panel layout** — left and right asides render as 220px / 300px fixed-width columns even at 380px viewport. Below `lg` they need to (a) default closed and (b) when opened render as full-screen overlays. **Component change** (`DocumentEditor.tsx`), affects TipTap and script editors. Same fix unblocks the chart picker drawer.

2. **Significance Journey Matrix** — no blocker on the Dashboard itself; the chart's PNG fits inside its parent at 380px. (Promoted out of blocking after re-read.)

### DEGRADED (works but poorly)

3. **InfoIcon touch targets** — ⓘ buttons are ~24px on mobile; need `min-h-[44px] min-w-[44px]`. **Trivial CSS.**

4. **Canvas editor at 0.36× scale** — the canvas IS the working surface; pixel-precise editing is impossible at this scale. **Component change** — recommend a "Viewing on mobile — open on desktop to edit" banner that disables the Transformer and the chart picker but lets the user navigate slides. The editing flow is a desktop operation by nature; deferring this is acceptable.

5. **Analytics chart Y-axis label overlap** — percent-formatted tick labels overlap the leftmost data points on a narrow viewport. **Trivial CSS** — drop chart heights to ~240px on mobile, set `margin={{ left: 12 }}`.

6. **Significance Journey Matrix axis labels** — 35° rotation on the strategy axis becomes unreadable with 10 strategies at 380px. **Trivial CSS** — rotate to 90° on mobile.

7. **Column header abbreviations** — `Annualised Volatility`, `Excess Return`, `Information Ratio`, `Recovery (months)` all wrap to two-three lines on mobile. **Trivial CSS** — shorter labels with the long form in the InfoIcon tooltip.

8. **`[[VERIFY]]` popup viewport clamp** — floating popup can overflow the viewport edge. **Trivial CSS** — `max-w-[calc(100vw-32px)]`.

9. **Speaker section navigator wrapping** — slide title + speaker label wraps awkwardly in the 220px navigator. **Trivial CSS** — `truncate` on the title.

10. **Submission Guide drawer on Reports** — verify it lands as a full-width bottom sheet on mobile, not a side drawer.

### MINOR (cosmetic only — not addressed)

- Dashboard column header text wrapping
- Reports / Settings card grids — already 1-column on mobile, no change needed
- TipTap toolbar pixel density — eyeball in a real browser
- WARN acknowledge button height
- QA methodology checklist expansion-panel label wrapping
- Account settings four-section vertical stack
- Brand switcher row touch targets

---

## Scope notes — surfaces I could not audit at the code level

- **Rehearsal mode** — directive queued (May 19); not yet built. Will need its own mobile pass once shipped. The brief positions it as a faculty-panel demo surface; desktop-only is acceptable.
- **Activity breakdown panel** — directive queued; not yet built. Same.
- **TipTap toolbar pixel density** — relies on the upstream TipTap render; an in-browser check is the only reliable measure.
- **Real-device touch behaviour** — Konva touch events on iOS Safari for the canvas editor specifically (drag, resize, Transformer pinch) are beyond static code review.

---

## Recommended sequencing for a mobile pass

If a single mobile-pass commit is the target:

1. **One BLOCKING fix only** — the document editor three-panel-to-overlay treatment below `lg`. This is the single most disruptive mobile blocker and the only one that gates the editor workflows (Bob's midpoint, Bob's brief, Molly's script editor, Molly's canvas editor).

2. **Trivial CSS sweep** — items 3, 5, 6, 7, 8, 9 above. Together they take the platform from "uncomfortable" to "professional" on mobile. Roughly 1-2 hours of contiguous CSS work.

3. **Canvas editor mobile banner** — single-line warning + Transformer-disable on touch. Defers real mobile canvas editing to the post-deadline backlog (item already on the post-deadline list).

The team's actual mobile use case is likely browsing on a phone during the train ride to McColl, not editing the midpoint paper. The trivial-CSS sweep + the document-editor fix would close the gap for that use case without committing to full mobile-edit parity.
