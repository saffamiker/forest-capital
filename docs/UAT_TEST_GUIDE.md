# UAT Test Guide
## Forest Capital Portfolio Intelligence System
### FNA 670 — Summer 2026

This guide is the user acceptance testing (UAT) record for the Portfolio
Intelligence System. It serves three purposes:

1. **A UAT record** for quality assurance ahead of the June 3 midpoint and
   the July 1 final presentation.
2. **A functional introduction** to the platform for Bob and Molly — working
   through your section is also the fastest way to learn the site.
3. **Evidence of testing discipline** for the Analytical Appendix (35% of the
   grade) — a documented, structured UAT pass is exactly the kind of rigour
   the appendix is assessed on.

Production app: **https://forest-capital.vercel.app**

> **This document is the source of truth for test cases.** The in-platform
> guided test runner (Settings → Testing Mode → Start Test Pass) runs these
> same cases interactively, with automated logging and attested results —
> navigating you to each screen, recording your pass/fail/skip, capturing
> structured failure reports and AI-categorised feedback. Use the runner for
> an attested pass; this document remains the readable reference and the
> place test cases are authored.

---

## Before You Start (ALL TESTERS)

1. **Enable Testing Mode before any test activity.**
   Go to **Settings → Account → Testing Mode → ON**. Confirm the amber
   **🧪 Testing Mode** pill is visible in the top navigation bar. While it is
   on, every click is logged as *testing* activity and is excluded from the
   Team Activity analytical view — so your testing does not contaminate the
   real engagement record behind the Roles & Division-of-Labor deliverable.
   Testing Mode is session-scoped and resets automatically on your next login.

2. **Use the production deployment** at https://forest-capital.vercel.app —
   not `localhost`.

3. **Log in with your Queens University email address.** A magic link is
   emailed to you; the link is single-use and expires in 15 minutes.

4. **For each test item, record one of:**
   - ✅ **Pass** — behaved as expected.
   - ❌ **Fail** — note exactly what you saw.
   - ⏭ **Skip** — note why.

5. **Report any failures to Michael before May 27th** so they can be fixed
   ahead of the midpoint submission.

---

## How This Guide Is Organised

Four sections. **Every tester completes Section 1 first**, then their own
role-specific section.

| Section | Tester | Focus |
|---|---|---|
| 1 | All testers | Core navigation and platform basics |
| 2 | Michael Ruurds | Engineering and analytics validation |
| 3 | Bob Thao | Written deliverables and council workflow |
| 4 | Molly Murdock | Presentation and visualisation validation |

---

## Section 1: All Testers

*Complete this section before your role-specific section.*

### Onboarding and navigation

- [ ] Site tour auto-launches on first login (or a **Retake Site Tour** button
      is available in Settings → Account).
- [ ] Tour steps are readable and make sense.
- [ ] Tour completion dismisses the tour cleanly.
- [ ] The What's New modal appears if there are unseen changelog entries.
- [ ] All nav items are visible and correctly ordered:
      **Dashboard → Analytics → Statistical Evidence → Regime Analysis →
      Council → QA Audit → Reports**.
- [ ] The active nav item is visually distinct.
- [ ] The Settings gear icon navigates to `/settings`.
- [ ] The Testing Mode toggle is visible in Settings → Account.
- [ ] The amber Testing Mode pill appears in the nav bar when Testing Mode
      is enabled.

### Dashboard

- [ ] The page loads without errors.
- [ ] The cumulative return chart renders with real data (not empty).
- [ ] The strategy table shows all 10 strategies.
- [ ] The efficient frontier curve is smooth and hyperbolic.
- [ ] All chart export buttons are present.
- [ ] Hovering an ⓘ icon shows a tooltip.
- [ ] Clicking an ⓘ icon opens the explainer panel.
- [ ] The explainer panel's "Ask the Council" button pre-populates a
      question on the Council screen.

### Council

- [ ] The Council screen loads.
- [ ] The "Ask the Council" question field accepts input.
- [ ] Submitting a question returns responses from multiple agents.
- [ ] Agent responses render as formatted markdown (not raw `*` and `#`
      characters).
- [ ] The Academic Review button is visually prominent (an amber card).
- [ ] Clicking Academic Review starts the review session.
- [ ] The loading state shows "Consulting the council…".
- [ ] The verdict renders with section headings and rating badges
      (Strong / Developing / Needs Work).
- [ ] The peer responses accordion is expandable.

### Reports

- [ ] The Reports page loads.
- [ ] The Generate Documents section is visible.
- [ ] The Team Activity section is at the top of the page.
- [ ] The Presentation View button is visible.
- [ ] Presentation View shows three charts at screen-share scale.
- [ ] CSV export works on Team Activity.

### Submission Guide

- [ ] Verify the **📋 Submission Guide** button is visible in the Reports
      page header.
- [ ] Click it — verify the side panel opens with the guide(s) relevant
      to your role: Bob sees Guide 1 (Midpoint Paper), Molly sees Guide 2
      (Final Presentation), Michael sees both.
- [ ] Verify a **deadline countdown** is visible at the top of each guide
      (amber at 5 days or fewer, red at 2 days or fewer).
- [ ] Verify each guide leads with the tracking note — work done on the
      platform is the documented contribution record.

### Settings

- [ ] All six sections render: **Organisation / Data and Study Period /
      Analytics Configuration / Academic Documents / Account /
      Release History**.
- [ ] The brand switcher changes branding (McColl vs Forest Capital).
- [ ] Data status shows green / amber / red staleness pills.
- [ ] Academic Documents shows the uploaded files.
- [ ] Release History shows changelog entries.
- [ ] The Retake Site Tour button works.

---

## Section 2: Michael Ruurds — Engineering and Analytics Validation

*Complete Section 1 first.*

### Analytics page

- [ ] Analytics is second in the nav (directly after Dashboard).
- [ ] All six components render: Cumulative returns / Rolling correlation /
      Rolling excess return / Regime-conditional table / Drawdown comparison /
      Factor loadings.
- [ ] The study period shows 2002-07-31 to 2025-12-31 (282 months).
- [ ] Rolling correlation shows the pre/post-2022 averages in the footer.
- [ ] The 2022 correlation regime-break marker is visible on all relevant
      charts.
- [ ] The factor loadings table shows the MOM column (Carhart four-factor
      confirmed).
- [ ] The sensitivity analysis section renders.
- [ ] The strategy methodology panel renders.
- [ ] All CSV exports download correctly.
- [ ] All ⓘ icons are present and functional.
- [ ] Data provenance annotations are visible below each component.

### Data integrity

- [ ] The Dashboard cumulative chart shows real data (not `Math.sin()` noise).
- [ ] The Sharpe CI column shows real intervals or `[—]` (not a hardcoded
      ±0.10).
- [ ] The efficient frontier max-Sharpe point is on or near the curve.
- [ ] Regime Switching plots near or above the frontier (thesis validation).
- [ ] Factor loadings show computed p-values (`*` markers on significant
      loadings).
- [ ] Information ratio shows N/A for the benchmark.

### Settings — data admin

- [ ] The Data and Study Period section shows all 15 tables.
- [ ] `market_data_monthly` and `ff_factors_monthly` show a red staleness
      pill — *expected*, the dataset is locked at December 2025.
- [ ] `academic_documents` shows green (recently uploaded).
- [ ] Analytics Configuration shows the DTB3 risk-free rate value.

### Team Activity validation

- [ ] Michael Ruurds' commits appear in the Team Activity timeline.
- [ ] The commit count matches `git log` (approximately 100).
- [ ] The activity-over-time chart shows the project build history.
- [ ] Presentation View is screen-share quality at 1920×1080.

### CI/CD

- [ ] Make a trivial commit to `main`.
- [ ] The GitHub Actions workflow passes.
- [ ] The commit appears in Team Activity within 60 seconds via the webhook.
- [ ] The changelog gate passes (no new migration without a changelog entry).

### Security

- [ ] An unauthenticated request to any `/api/v1/*` endpoint returns 401.
- [ ] Production startup would fail if `SECRET_KEY` is unset (verify the
      `config.py` fail-fast logic).

---

## Section 3: Bob Thao — Written Deliverables and Council Workflow

*Complete Section 1 first. This section is your primary workflow as written
deliverables lead.*

### Council for analytical interrogation

- [ ] Navigate to Council.
- [ ] Ask: *"What is the strongest argument that diversification failed in
      2022 based on our data?"*
- [ ] Verify the response is specific, cites actual metrics, and is
      well-formatted.
- [ ] Click an ⓘ on any Analytics metric you don't recognise — verify the
      explanation makes sense.
- [ ] Click "Ask the Council about this" from an explainer — verify the
      question pre-populates correctly on the Council screen.

### Academic Review

- [ ] Navigate to Council and click Academic Review (the amber button).
- [ ] Wait for the full verdict (this may take 30–45 seconds).
- [ ] Verify the verdict has all five sections: Data Sufficiency /
      Requirements Alignment / Deliverable Quality / Priority Investigation /
      Overall Readiness.
- [ ] Each section has a rating badge.
- [ ] The Priority Areas are specific and numbered.
- [ ] The Overall Readiness section gives a clear, honest assessment.
- [ ] The peer responses accordion shows multiple agent perspectives.

> **Why this matters for your workflow:** the midpoint paper's "Next Steps"
> section is generated from the most recent Academic Review verdict. Run a
> review *before* generating the midpoint paper so that section is complete
> rather than `[DATA PENDING]`.

### Document generation

- [ ] Navigate to Reports → Generate Documents.
- [ ] Click **Generate Midpoint Paper** and wait for generation (30–60s).
- [ ] Download the `.docx` file, open it in Word, and verify:
  - [ ] Double-spaced, 12 pt font.
  - [ ] Four sections present with headings.
  - [ ] Real data tables embedded (summary statistics, regime table).
  - [ ] Team activity data in Section 3.
  - [ ] No `[DATA PENDING]` placeholders (if an Academic Review has been run
        and the dashboard caches are warm).
  - [ ] Three pages or under.
  - [ ] Page numbers present.
- [ ] Click **Generate Executive Brief**, download, and verify:
  - [ ] Five sections present.
  - [ ] The title page is formatted correctly.
  - [ ] Tables embedded with real data.
  - [ ] An investment-audience tone.
  - [ ] A Limitations section is present.

### Document Editor

*Run after generating the midpoint paper above.*

- [ ] On the midpoint-paper card, click **[Open in Editor]** (the primary
      button after generation — not Download).
- [ ] Verify the editor loads at `/editor/:draft_id` with a three-panel
      layout: navigator (left), document (centre), Writing Assistant (right).
- [ ] Verify the **AI DRAFT** banner and the amber **BOB — YOUR TASKS**
      callout appear at the top.
- [ ] Verify `[[VERIFY]]` markers render as amber-highlighted spans in the
      document body.
- [ ] Verify `[[BOB]]` callouts render as amber-highlighted spans.
- [ ] Click a `[[BOB]]` callout and confirm the prompt — verify the marker
      is removed and the left-panel section progress bar advances.
- [ ] Click a `[[VERIFY]]` marker and confirm the prompt — verify the amber
      highlight is removed.
- [ ] Type a sentence anywhere in the document body — verify the header
      shows "Saving…" then "Saved [time]" (auto-save runs every 30s).
- [ ] In the Writing Assistant panel (right), type *"Is my methodology
      section clear?"* — verify a specific, relevant response returns
      (it should reference your actual content, not be generic).
- [ ] Select a sentence in the editor — verify the **✨ Ask AI** button
      appears above the selection.
- [ ] Click it — verify the Writing Assistant panel opens and the chat
      input is pre-filled with the selected text quoted.
- [ ] Send the pre-filled message — verify the response references the
      specific passage you selected.
- [ ] After the session, open Team Activity on the Reports page — verify
      the writing-assistant exchange is recorded.
- [ ] Click **[Run Academic Review]** in the Writing Assistant panel —
      verify the inline verdict appears below the button.
- [ ] If unresolved markers remain, verify the warning appears:
      "You have [n] unresolved markers".
- [ ] In the left panel under Versions, click **Save**, label it
      "UAT test" — verify it appears in version history with a restore
      control.
- [ ] Click **[Export DOCX]** in the editor header — verify a `.docx`
      file downloads containing the current editor content.

### Settings — Academic Documents

- [ ] Navigate to Settings → Academic Documents.
- [ ] Verify both Markdown files are listed: `midpoint_requirements` and
      `final_presentation_requirements`.
- [ ] Upload a test `.md` file and verify it appears in the list.
- [ ] Delete the test file.
- [ ] Verify the Reports-screen annotation links correctly to Settings.

---

## Section 4: Molly Murdock — Presentation and Visualisation Validation

*Complete Section 1 first. This section is your primary workflow as
presentation and visualisation lead.*

### Chart quality and comprehension

- [ ] Navigate to Analytics.
- [ ] For each chart, hover the ⓘ and verify the tooltip explains it clearly
      in plain English.
- [ ] Click the ⓘ on Rolling Correlation — verify the explanation mentions
      the 2022 regime break.
- [ ] Click the ⓘ on Factor Loadings — verify the Carhart four-factor model
      is explained.
- [ ] Click the ⓘ on Efficient Frontier — verify the point for a dynamic
      strategy above the curve is explained.
- [ ] Verify the 2022 regime-break marker is consistent across Rolling
      Correlation, Rolling Excess Return, and Cumulative Returns.

### Presentation View

- [ ] Navigate to Reports → Team Activity and click Presentation View.
- [ ] Verify three charts display at full-screen scale: Activity over time /
      Team contribution split / Agent engagement breakdown.
- [ ] Charts must be readable at 1920×1080 (projected-room quality).
- [ ] Verify the charts show real data (not empty).
- [ ] The team contribution split shows all three team members.
- [ ] Exit Presentation View cleanly.

### Presentation deck

- [ ] Navigate to Reports → Generate Documents.
- [ ] Click **Generate Presentation Deck** and wait for generation (30–60s).
- [ ] Download the `.pptx` file, open it in PowerPoint, and verify:
  - [ ] 16 slides present.
  - [ ] A navy/white professional theme (not the dark platform theme).
  - [ ] The title slide is formatted correctly.
  - [ ] Charts embedded on the relevant slides (not broken image
        placeholders).
  - [ ] Slide 5 has the rolling correlation chart (the 2022 regime break).
  - [ ] Slide 8 has the cumulative returns chart.
  - [ ] Slide 15 ("How We Built This") has real activity counts.
  - [ ] All text is readable (not white-on-white or black-on-black).
  - [ ] No lorem ipsum or placeholder text.

### Presentation Editor

*Run after generating the presentation deck above.*

- [ ] On the presentation-deck card, click **[Open in Editor]**.
- [ ] Verify the centre panel renders **slide cards** — an editable title,
      content and speaker-notes field per slide — not a rich-text editor.
- [ ] Verify at least one slide shows amber **data-point markers**.
- [ ] Click **Mark data points verified** on a slide — verify the marker
      row clears to a verified state.
- [ ] Click the speaker-notes field on any slide and type a sentence.
- [ ] Click **[Generate Talking Points]** below the notes field — verify
      4-6 bullet points return.
- [ ] Click the insert (+) control on one bullet — verify it appends to
      the speaker-notes field.
- [ ] Verify the slide shows a **Complete** indicator once its notes are
      written and its data points are verified.
- [ ] Verify the left-panel progress indicator updates for that slide.
- [ ] Click **[Presentation Preview]** in the header bar — verify a
      full-screen overlay opens.
- [ ] Verify the speaker notes show in a strip below each slide, labelled
      "Your notes (not visible to audience)".
- [ ] Navigate with the arrow keys — verify slides advance and the
      counter ("N / 16") updates.
- [ ] Press **Esc** — verify the overlay closes and returns to the editor.
- [ ] Click **[Export PPTX]** in the editor header — verify a `.pptx`
      file downloads, and open it to confirm your edited speaker notes
      are present on the slides.

### Writing Assistant (Presentation)

- [ ] In the editor, open the Writing Assistant panel (right) and type a
      question about a slide — verify a specific, relevant response.
- [ ] Select a word or phrase in a slide's content or speaker-notes
      field, then use the Writing Assistant chat to ask about it —
      verify the response is specific to the slide content.

### Export package

- [ ] Navigate to Reports and click **Export Academic Package**.
- [ ] Watch the progress steps complete.
- [ ] Download the ZIP file and verify it contains:
  - [ ] A `/charts/` folder with PNG files.
  - [ ] A `/tables/` folder with CSV files.
  - [ ] A `/metadata/` folder with `study_period.txt` and `README.txt`.
  - [ ] Charts are light mode (white background, dark text).
  - [ ] Charts are high resolution (not blurry).
- [ ] Open one chart PNG in an image viewer — verify it is suitable for
      embedding in a Word document.

### Peer review preparation

- [ ] Navigate to Council and run an Academic Review session.
- [ ] Read the Overall Readiness section.
- [ ] Identify the top Priority Area for Further Investigation.
- [ ] Ask the council: *"What questions might a peer reviewer ask about our
      regime analysis methodology?"*
- [ ] Verify the response is specific and helpful for preparation.

---

## Test Sign-Off

At the end of your section, record:

| Field | Value |
|---|---|
| Tester | |
| Date | |
| Section completed | |
| Failures found | |
| Notes | |

Send the completed sign-off and any failure notes to **ruurdsm@queens.edu**.

---

## Known Limitations (not failures — expected behaviour)

- `market_data_monthly` and `ff_factors_monthly` show red staleness pills in
  Settings. This is **correct** — the dataset is intentionally locked at
  December 2025, so the newest row is several months behind "today".
- Sharpe CI shows `[—]` for strategies where the probabilistic-Sharpe
  calculation is unavailable.
- Information Ratio shows N/A for the benchmark — zero tracking error makes
  it mathematically undefined.
- Document generation produces **first drafts**. Narrative sections require
  Bob's review and refinement before submission, and a section whose source
  data is unavailable is marked `[DATA PENDING]` rather than failing the
  document.
- Generated presentation charts may differ slightly from the platform charts
  (matplotlib server-side rendering vs Recharts frontend rendering).
- The in-editor **Export DOCX / Export PPTX** button renders the file
  directly from the current editor content — a faithful export of what
  the author has edited. It does not re-embed the regenerated data
  tables; the table-rich version is the one produced by Reports →
  Generate Documents.
- In the editor, a `[[VERIFY]]` or `[[BOB]]` marker is resolved by clicking
  it and confirming the prompt (which deletes the marker text) — there is
  no separate "Mark as Complete" button.
