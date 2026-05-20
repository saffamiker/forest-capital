# UAT Test Guide
## Forest Capital Portfolio Intelligence System
### FNA 670 — Summer 2026

This guide is the user acceptance testing (UAT) record for the Portfolio
Intelligence System. It serves three purposes:

1. **A UAT record** for quality assurance ahead of the May 27 midpoint
   paper, the June 3 cohort presentation, and
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

### Settings — Platform Engagement panel

- [ ] Navigate to Settings → Users.
- [ ] Scroll below the user table.
- [ ] Verify the **Platform Engagement** section is visible with the
      subtitle "Last 30 days — analytical sessions only".
- [ ] Verify a stacked-bar chart renders per user, showing activity
      broken down by interaction type (Council / Academic Review /
      Writing Assistant / Explain / QA / Export / Test Quality / Document
      Upload).
- [ ] Hover over a bar segment — verify a tooltip shows the interaction
      type and the count (e.g. "12 interactions" + "Council").
- [ ] Verify any zero-interaction user shows the muted "No activity in
      the last 30 days" state instead of an empty bar.
- [ ] Verify the **AI spend** line is visible for users with cost > $0
      (formatted as `$X.XX`), and absent on a $0 user.
- [ ] Verify the per-type list (left column) and the analytical /
      testing page-view split (right column) appear below each user's
      bar.

### QA page and audit controls

- [ ] Navigate to the **QA** page.
- [ ] Verify the **[Run Full QA]** and **[Run Full Audit]** buttons are
      visible at the top (sysadmin-only — confirm they are not visible
      when signed in as a viewer or team member).
- [ ] Click **[Run Full Audit]** — verify the global **QA Running** pill
      appears in the nav bar within ~1 second.
- [ ] Navigate to the Dashboard and back to the QA page — verify the
      pill is still present (it tracks the audit, not the route).
- [ ] On the **Statistical Audit** panel, find a finding with status
      **WARN**.
- [ ] Click **[Acknowledge]** on the WARN finding — verify a textarea
      opens for the resolution note.
- [ ] Enter a brief resolution note ("Reviewed; accepted as a documented
      limitation") and click **[Save acknowledgement]**.
- [ ] Verify the saved finding now shows an **Acknowledged** badge and
      the resolution note renders below the evidence.
- [ ] Click **[Download Audit Report]** to export the PDF.
- [ ] Open the PDF and verify the resolution note appears on the WARN
      finding's row — the acknowledgement carries into the export.

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

### Document generation — async pattern

Document generation runs as a background job: the **Generate** click
returns a job id immediately, the panel polls the job's status, and a
completion toast announces a job that finished while you were on
another screen. The polling store lives at module scope, so navigating
away from Reports never cancels the run.

- [ ] Navigate to Reports → Generate Documents.
- [ ] Click **[Generate Midpoint Paper]**.
- [ ] Verify the card **immediately** shows a spinner and "Generating
      your midpoint paper…".
- [ ] Verify the message "You can navigate away — we'll notify you
      when it's ready" appears below the spinner.
- [ ] Navigate to the Dashboard and back to Reports — verify
      generation continues in the background (the spinner and the
      navigate-away message are still on the card; the job did not
      restart).
- [ ] Wait for completion (30-60 seconds end-to-end).
- [ ] On completion, verify **[Open in Editor]** appears as the
      primary (electric-blue) button and **[Download DOCX]** as the
      secondary button.
- [ ] Verify the **Last generated** timestamp updates to "Just now".
- [ ] **Cancel test:** click **[Generate Midpoint Paper]** again, then
      click **[Cancel]** before it completes — verify the card
      resets to the initial state (Generate button visible, no
      spinner, the previous completed run's Open in Editor /
      Download still available).
- [ ] Click **[Download DOCX]** and verify:
  - [ ] Double-spaced, 12 pt font.
  - [ ] Four sections present with headings.
  - [ ] Real data tables embedded (summary statistics, regime table).
  - [ ] Team activity data in Section 3.
  - [ ] No `[DATA PENDING]` placeholders (if an Academic Review has been
        run and the dashboard caches are warm).
  - [ ] Three pages or under.
  - [ ] Page numbers present.
- [ ] **Re-download test:** click **[Download DOCX]** a second time on
      the same job — verify a 410 Gone surfaces with the message
      "This download has already been served. Regenerate the
      document if needed." The Open in Editor link still works
      because the draft survives.
- [ ] Click **[Generate Executive Brief]** and repeat the spinner +
      navigate-away + completion flow. Verify the brief carries:
  - [ ] Five sections present.
  - [ ] The title page is formatted correctly.
  - [ ] Tables embedded with real data.
  - [ ] An investment-audience tone.
  - [ ] A Limitations section is present.
- [ ] **Completion toast test:** click **[Generate Executive Brief]**,
      navigate to the Dashboard, and stay there. Verify a toast
      "Your executive brief is ready" appears in the Dashboard
      corner when generation completes — clicking [Open in Editor]
      on the toast navigates back to the editor; the close button
      dismisses.

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
- [ ] Verify `[[BOB]]` callouts render as full-width amber block panels
      with a [Mark as Complete] button.
- [ ] Click [Mark as Complete] on a `[[BOB]]` panel — verify the panel
      collapses and the section progress bar in the left panel updates.
- [ ] Click a `[[VERIFY]]` marker — verify a popup opens with the message
      "Verify this value against the Analytics page before removing this
      marker." and two buttons: [Mark as Verified] and [Cancel]. Click
      [Mark as Verified] — verify the amber highlight and marker text are
      removed. Click a second `[[VERIFY]]` marker and click [Cancel] —
      verify the popup closes and the marker remains intact.
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
presentation and visualisation lead. Record ✅ Pass / ❌ Fail / ⏭ Skip
inline for each numbered item (4.1–4.15, plus 4.10b and 4.13b).*

### 4.1 Presentation deck generation

- Navigate to the Reports page.
- Click **[Generate Presentation Deck]**.
- Wait for generation to complete.
- Verify the **[Open in Editor]** button appears as the primary CTA.
- Click **[Open in Editor]**.
- Verify the editor loads at `/editor/:draft_id`.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.2 Canvas editor — basic interaction

- Verify 16 slide cards are visible in the left-panel navigator.
- Verify the centre panel shows a Konva canvas Stage (not a stack of
  text cards).
- Click a slide in the navigator — verify the canvas updates to show
  that slide.
- Verify the **[[MOLLY]]** task callout is visible at the top of the
  editor.
- Dismiss the callout — verify it disappears and does not return on the
  same session.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.3 Canvas editor — text elements

- Click a text element on the canvas.
- Verify selection handles appear.
- Drag the element to a new position — verify it moves correctly.
- Double-click the element — verify an inline textarea appears for
  editing.
- Edit the text and click away — verify the new text is saved.
- Verify auto-save fires within 2 seconds ("Saving…" → "Saved [time]").

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.4 Canvas editor — chart elements

- Click **[+ Chart]** in the toolbar.
- Verify the chart picker drawer opens.
- Verify chart thumbnails load (5 charts visible).
- Click a chart to add it to the current slide.
- Verify the chart element appears on the canvas with an amber border.
- Click the chart element — verify the verify popup appears:
  "Verify this chart reflects current platform data."
- Click **[Mark as Verified]** — verify the amber border disappears.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.5 Canvas editor — format toolbar

- Click a text element to select it.
- Verify the format toolbar appears with font size, bold, italic and a
  colour picker.
- Change the font size — verify the text updates on the canvas.
- Toggle bold — verify the text weight changes.
- Change the colour using a preset — verify the text colour updates.
- Click **[Delete element]** — verify the element is removed from the
  canvas.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.6 Canvas editor — speaker assignment

- In the left-panel navigator, click **[+ Speaker]** on Slide 1.
- Type "Molly" and confirm.
- Verify a "Molly" badge appears on Slide 1 in the navigator.
- Verify a "Presenter: Molly" label appears above the canvas.
- Assign a different name to Slide 2 (e.g. "Bob").
- Verify previously-used names appear as suggestions on subsequent
  slides.
- Verify the **[Generate Script]** button is now enabled in the header.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.7 Canvas editor — AI features

- Click **[AI Layout]** in the toolbar.
- Verify a layout suggestion returns.
- Verify the current vs suggested preview is shown side by side.
- Click **[Dismiss]** — verify the canvas is unchanged.
- Click **[AI Layout]** again, then **[Apply]** — verify the canvas
  elements reposition.
- Select a text element — verify the **[AI Copy]** button appears in
  the toolbar.
- Click **[AI Copy]** — verify suggested replacement text appears.
- Click **[Apply]** — verify the text updates on the canvas.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.8 Canvas editor — speaker notes

- Scroll below the canvas to the speaker-notes field.
- Type a note for the current slide.
- Click **[Generate Talking Points]** — verify 3-5 bullet points return.
- Click **[Insert]** on one bullet — verify it appends to the notes
  field.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.9 Presentation Preview

- Click **[Presentation Preview]** in the header.
- Verify a full-screen overlay opens.
- Verify the slide content is visible.
- Verify the speaker notes are visible in the strip at the bottom.
- Navigate with the arrow keys — verify slides advance.
- Verify the slide counter shows "N / 16".
- Press **Esc** — verify the overlay closes cleanly.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.10 Export PPTX

- Click **[Export PPTX]** in the header.
- Verify a `.pptx` file downloads.
- Open it in PowerPoint.
- Verify the slide layout matches the canvas positions approximately.
- Verify the speaker notes are present on the slides.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.10b Mobile editor experience

*Test on a mobile device (iPhone / iPad portrait) or in a desktop
browser with the window resized below 1024 px wide.*

- Open the document editor on a screen narrower than 1024 px (any
  document type — start with a midpoint paper or script if you have
  one open).
- Verify the **left navigator** panel is hidden by default; the
  centre editor is full-width.
- Tap the **[≡ Sections]** button in the editor header — verify the
  navigator opens as a full-screen overlay drawer.
- Tap outside the drawer (or tap **[✕]**) — verify the drawer
  closes and the editor is visible again.
- Tap the **[✨ Assistant]** button in the editor header — verify
  the Writing Assistant opens as a full-screen overlay drawer with
  its own close button.
- Tap outside the assistant drawer (or **[✕]**) — verify it closes.
- Verify the centre editor remains full-width throughout — the
  drawers overlay the page, they do not push the centre column.
- Open the **canvas editor** (a `presentation_deck` draft) on the
  same narrow viewport.
- Verify the amber banner appears at the top:
  *"The presentation canvas editor works best on desktop. Open on a
  larger screen for full editing capability."*
- Verify the **[+ Chart]** toolbar button is hidden on touch — only
  Text and the format controls are shown.
- Tap a text element on the canvas — verify selection works but
  resize handles (the Konva Transformer) do not appear.
- Verify the speaker-notes textarea below the canvas is fully
  editable on mobile.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.11 Script generation

- Ensure at least one slide has a speaker assigned (from 4.6).
- Click **[Generate Script]** in the header.
- Verify the loading state is shown (generation takes 30-60 seconds).
- Verify the editor opens at `/editor/:draft_id` on completion.
- Verify the document type is `presentation_script`.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.12 Script editor

- Verify the **[[MOLLY]]** task callout is visible at the top.
- Verify the left panel shows slide sections with speaker names.
- Verify the delivery-time indicator is visible (e.g. "~22 min
  delivery").
- Verify the delivery time is amber if outside 18-27 minutes.
- Verify all 16 slides are covered in the script.
- Verify speaker labels are present for each slide section.
- Verify transitions are present between slides (blockquote style).
- Edit a paragraph — verify the delivery time updates live.
- Verify auto-save fires.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.13 Script export

- Verify the **[Export Master Script]** button is visible in the header.
- Verify an **[Export: {Name}]** button is visible for each unique
  speaker.
- Click **[Export Master Script]** — verify a `.docx` downloads.
- Open it in Word — verify all slides are present, speaker labels are
  visible, and transitions are italicised.
- Click **[Export: Molly]** (or whichever name was assigned) — verify a
  `.docx` downloads.
- Open it in Word — verify only Molly's slides are present, slide
  numbers and titles are included, and the page header shows
  "Molly — Forest Capital Presentation Script".

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.13b Academic Review from the script editor

- In the script editor, open the Writing Assistant panel (right
  side on desktop, [✨ Assistant] drawer on mobile).
- Verify the note below **[Run Academic Review]** reads:
  *"Academic Review for presentation scripts evaluates argument
  coherence, audience clarity, and slide coverage. Formatting
  scores do not apply."*
- Click **[Run Academic Review]**.
- Wait for the verdict (30–45 seconds).
- Verify the verdict renders inline below the button.
- Verify the verdict uses the **script-specific rubric** — five
  sections, each rated **Strong / Needs Work / Incomplete** (NOT
  the written-submission Strong / Developing / Needs Work scale).
- Verify the five section headings are:
  1. Argument Coherence Across Slides
  2. Clarity for a Mixed Faculty / Investor Audience
  3. Coverage of Key Findings
  4. Speaker Differentiation and Voice
  5. Overall Delivery Readiness
- Verify the verdict does NOT comment on citation formatting,
  paragraph structure, or footnotes — those criteria are skipped
  for a spoken-delivery document.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.14 Submission Guide

- Navigate to the Reports page.
- Click **[Submission Guide]** in the header.
- Verify Guide 2 is visible (Molly's guide).
- Verify steps 8-11 are present:
  Step 8 — Assign speakers; Step 9 — Generate script;
  Step 10 — Rewrite in your voice; Step 11 — Export scripts.
- Verify the deadline countdown shows the July 1st final
  presentation (June 3rd is a peer-review cohort event, not a
  submission gate and not shown as a countdown). The panel-note
  block at the bottom mentions the July 3rd panel.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

### 4.15 Rehearsal mode

- Open your generated `presentation_script` draft in the editor.
- Click **[Rehearse]** in the editor header.
- Verify a full-screen overlay opens with a top bar reading
  "Rehearsal Mode", a slide counter "Slide 1 of N", and an Exit
  button (also Esc).
- Verify the **left panel** shows the script for slide 1 — slide
  number + title (bold), speaker label, the section's body prose,
  and the transition line at the bottom prefixed →.
- Verify the **right panel** shows a static render of the slide
  itself — text elements positioned as in the canvas, chart elements
  as labelled placeholder boxes ("[rolling correlation]" etc.); the
  presenter's speaker notes appear in a muted strip at the bottom.
- Press the right arrow key — verify **both panels advance together**
  to slide 2's script and slide 2's canvas. Press the left arrow —
  verify both step back.
- Verify the **delivery time counter** ("~N min remaining") at the
  top right counts down as you advance slides — slide 1 should show
  more remaining time than the last slide.
- Press **Esc** — verify the overlay closes and the script editor is
  visible again with no state loss.
- If your script OR your deck is missing when you click [Rehearse],
  verify the overlay shows "Rehearsal requires both your presentation
  deck and script." with the specific missing-resource message, and
  a Close button that dismisses the modal.

**Result:** ✅ Pass / ❌ Fail / ⏭ Skip

---

## Test Sign-Off

At the end of your section, record the summary below. Sections 1-3 are
recorded as `- [ ]` checklist items; Section 4 (Molly) is recorded as
seventeen numbered items (4.1–4.15 plus the two May-19 additions
4.10b "Mobile editor experience" and 4.13b "Academic Review from the
script editor"), each carrying an inline ✅ Pass / ❌ Fail / ⏭ Skip
result — note the result and any failure detail against each.

| Field | Value |
|---|---|
| Tester | |
| Date | |
| Section completed | |
| Section 4 items 4.1–4.15 + 4.10b + 4.13b (Molly only) | _e.g. 17/17 pass_ |
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
- `[[BOB]]` callouts render as block panels resolved via [Mark as
  Complete]. `[[VERIFY]]` markers resolve via a popup with
  [Mark as Verified] / [Cancel].
- The canvas editor renders charts and runs AI Layout / AI Copy
  synchronously — stay on the page while a request is in flight;
  navigating away loses the result.
- Document generation runs in the background. You can navigate away
  freely — a toast notification will appear when your document is
  ready. A second download attempt after the file has been served
  returns 410 Gone — regenerate the document if needed.
- The chart picker shows **sixteen server-renderable charts** grouped
  by category — Regime Analysis, Factors, Performance, Risk,
  Significance, and Activity. Charts requiring QA or regime data show
  a placeholder if that data is unavailable.
