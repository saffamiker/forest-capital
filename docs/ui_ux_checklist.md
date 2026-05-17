# UI/UX Visual Checklist — Manual Browser Pass

A structured checklist for a human tester to run against the live app in a
browser. Catches what a code audit cannot: actual colour rendering, fonts,
animations, layout at real viewport widths. Run it at **1440px** width first,
then spot-check **1280px** and **1920px**.

The bar: *professional investment tool* for Forest Capital and the McColl
School of Business — not a student project.

For each item: ☐ check the box, note anything that falls short.

---

## Cross-cutting (every screen)

- [ ] Nav bar stays fixed at the top when the page scrolls
- [ ] The active nav item is unmistakably highlighted (electric-blue tint + border)
- [ ] Analytics is the second nav item, directly after Dashboard
- [ ] The settings gear shows the active treatment when on `/settings`
- [ ] Testing Mode amber pill appears in the nav only when a testing session is active
- [ ] No horizontal scrollbar at 1280px on any screen
- [ ] No element visibly overflows its card/container at 1280px / 1440px / 1920px
- [ ] Fonts render crisply — no fallback/serif flash; numbers in monospace, prose in sans
- [ ] Green/amber/red mean the same thing everywhere (good / caution / bad)
- [ ] No raw hex-coloured text that looks "off" against its background
- [ ] Loading spinners look intentional; nothing flashes blank then jumps

## Login

- [ ] Branding (McColl by default) is correct: logo, app name, institution line
- [ ] The email field and "Send me a secure link" button are aligned and sized comfortably
- [ ] Confirmation state after submit reads cleanly ("check your inbox…")
- [ ] An invalid / non-queens.edu email shows a clear inline error

## Dashboard

- [ ] Regime banner renders at the top with the BULL/BEAR/TRANSITION pill colour-coded
- [ ] The 2022 correlation banner shows real pre/post numbers (not "—", not a hardcoded value)
- [ ] The regime "as of" timestamp shows under the correlation banner
- [ ] Four summary tiles render with real values, aligned, equal height
- [ ] **Cumulative Returns chart renders real monthly data** — a curve per strategy, NOT the "Cumulative return series unavailable" empty state
- [ ] X-axis shows years (2002 … 2025), not raw dates; Y-axis shows "x" growth multiples
- [ ] Strategy legend toggle buttons above the chart show/hide lines correctly
- [ ] Strategy comparison table is the dominant element; all columns visible at 1440px
- [ ] Each strategy is the SAME colour in the chart and any coloured table accents
- [ ] Sharpe column shows `[low–high]` for strategies with a CI, `[—]` for those without
- [ ] Tier ranking, DSR, P(FDR), CV-score columns all render numbers, not blanks
- [ ] Data-freshness pill (computed date + Current/Ageing/Stale) shows on the table header
- [ ] Efficient frontier chart renders below; the max-Sharpe point is marked
- [ ] Numeric table columns are right-aligned; text columns left-aligned

## Analytics

- [ ] Page has a clear title ("Academic Analytics") + subtitle + study-period line
- [ ] Sections render in a logical order, each in its own card
- [ ] Cumulative total return chart renders real data
- [ ] Rolling correlation chart shows the 2022 regime-break marker clearly
- [ ] Regime-conditional table splits pre/post-2022 with Sharpe + CAGR
- [ ] Carhart four-factor table shows betas, alpha, R², and a significance flag
- [ ] Every table has a CSV export button in a consistent position
- [ ] ⓘ info icons on section titles open a tooltip on hover, a panel on click
- [ ] Missing values render as "—" in a consistent muted style

## Statistical Evidence

- [ ] All six charts render with titles, axes, and legends
- [ ] Significance Journey Matrix cells are legible; pass/fail colour-coded
- [ ] CPCV / Probabilistic-Sharpe / Walk-Forward charts have hover tooltips
- [ ] Strategy colours match the Dashboard exactly

## Regime Analysis

- [ ] All six charts render; regime labels colour-coded consistently
- [ ] Correlation breakdown chart highlights the 2022 period
- [ ] Factor exposure heatmap cells are readable; legend present
- [ ] Regime timeline is legible across the full 2002–2025 span

## Council

- [ ] The "Ask the council" input + Convene button are clear
- [ ] **The Academic Review button is easy to spot** — not visually outranked by the Convene button
- [ ] Submitting a query shows streaming agent cards with "Analysing…" skeletons
- [ ] Each agent card uses its accent colour; Gemini purple, Grok orange, CIO last
- [ ] Agent output renders readably — headings/lists/bold are formatted, not literal `**`/`-`
- [ ] A failed council query shows a clear error message + retry — NOT a blank screen
- [ ] A cancel/stop control is available while a run is streaming
- [ ] Agent model badges are accurate (no retired model names)

## Academic Review

- [ ] The Academic Review button looks like a deliberate, important action
- [ ] On click, the loading state communicates it is multi-step (peers, then verdict)
- [ ] The verdict renders with clear section separation
- [ ] Strong / Developing / Needs-Work rating badges are prominent and colour-coded
- [ ] The peer-responses accordion is clearly labelled and expands smoothly
- [ ] Before first run, there is a one-line description of what Academic Review does

## QA Audit

- [ ] The 30-check summary card renders with the pass/warn/fail counts
- [ ] Filter tabs work; checklist items expand with explanation panels
- [ ] PASS/WARN/FAIL badges colour-coded consistently with the rest of the app

## Reports

- [ ] Team Activity is prominent — not buried at the bottom of the page
- [ ] The "Presentation View" toggle is easy to find and clearly labelled
- [ ] In Presentation View the three charts are large and legible enough to screen-share
- [ ] The activity timeline has a visible CSV export button
- [ ] "Include testing activity" toggle is clearly labelled
- [ ] Commit entries are visually distinct from AI-interaction entries
- [ ] Empty state (no activity yet) shows a meaningful message, not a blank panel
- [ ] Bob's and Molly's deliverable cards render cleanly

## Settings

- [ ] Six sections, each with a heading, description, and a divider between them
- [ ] Organisation section: the McColl ↔ Forest Capital switch works; UI updates instantly
- [ ] Data and Study Period: per-table rows with row counts, dates, staleness pills
- [ ] A legend explains green/amber/red staleness (and that a Stale pill on the locked dataset is expected)
- [ ] Academic Documents panel states supported file types (.pdf, .md)
- [ ] Uploading a document shows it in the list; empty state reads cleanly
- [ ] Testing Mode toggle explains the auto-reset-on-next-login behaviour
- [ ] Release History lists entries with "New" badges on unseen ones
- [ ] Academic rationale in each release entry is visually distinct (amber accent)
- [ ] "Retake Site Tour" button triggers the tour

## What's New modal

- [ ] Opens once after login when there are unseen entries
- [ ] Each entry's academic rationale is visually distinct from its description
- [ ] "View updated site tour" button works and closes the modal first
- [ ] "Got it" / backdrop click / Escape all close it

## Site Tour

- [ ] Tooltip styling matches the dark theme (no white default Joyride styling)
- [ ] "Step X of Y" footer, Back / Skip / Next buttons render correctly
- [ ] Final step button reads "Start Exploring"
- [ ] Cross-route steps navigate and resume without a flash of the wrong page
- [ ] "Most relevant for:" line shows on the steps that have it

## Modes (Analyst / Commentary / Present)

- [ ] The three-mode selector is visible and the active mode is highlighted
- [ ] Present mode is gated on QA status (locked / amber-badged as appropriate)
- [ ] Switching modes does not reduce information density or break layout
- [ ] Commentary mode shows chart comment strips; Analyst mode hides them

## Branding

- [ ] In McColl mode: McColl logo, "Portfolio Intelligence System", Queens line
- [ ] In Forest Capital mode: FC branding in header, footer, and any exported document
- [ ] Favicon matches the active brand

## Empty / error states (force them)

- [ ] Team Activity with no data → meaningful empty message
- [ ] Academic Documents with nothing uploaded → meaningful empty message
- [ ] A chart with no data → labelled empty state, never a bare axes frame
- [ ] Any failed fetch → an actionable error (what went wrong + retry), never blank
