/**
 * testScripts.ts
 *
 * The guided UAT test runner's scripts — the structured, code-versioned
 * form of docs/UAT_TEST_GUIDE.md. Each checklist item in the guide is one
 * TestStep here; the guide remains the human-readable source of truth.
 *
 * Scripts are versioned with the code (not user-editable, not in the
 * database). The matching backend gate is config.TEST_SCRIPT_VERSION —
 * bump both together when a script's steps change materially.
 *
 * `target` is a CSS selector the runner highlights (the data-tour anchors
 * placed for the site tour are reused where they line up); null means a
 * centred, non-anchored step. `route` is the page the runner navigates to
 * before showing the step.
 */

export interface TestStep {
  id: string
  route: string
  target: string | null
  title: string
  instruction: string
  expectedResult: string
  allowSkip: boolean
}

export interface TestScript {
  id: string
  version: number
  title: string
  assignedTo: 'all' | 'michael' | 'bob' | 'molly'
  steps: TestStep[]
}

// Bumped in lockstep with backend config.TEST_SCRIPT_VERSION.
// v2 (May 22 2026) — adds the report-writer editor flow + macro research +
// explainer CIO follow-up + diversification chart steps to all three
// scripts; adds QA audit / failure / issue tracker steps to Molly's script.
// v3 (May 25 2026) — UAT guide reset after PRs 147-153.
// v4 (May 26 2026) — addresses the today-PR change axes that invalidated
// the v3 scripts:
//   - Academic Review moved /council -> /qa (commit 9ff578b, May 25);
//     17 tests had route='/council' against the [data-tour="academic-
//     review"] anchor that now lives on the QA Hub. Route updated.
//   - [[BOB]] section callouts removed from the report template
//     (PR #176); the writer's [BOB] block emission semantics
//     changed (PR #178). The bob_writer_bob_blocks test expected
//     visible callout BADGES — reworded to inline markers.
//   - word_count_over_budget is now warn-only after the rationalization
//     pass (PR #184); bob_writer_word_counts and bob_writer_final_check
//     reworded to reflect the new gate.
//   - The midpoint paper no longer carries trailing [BOB] PRE-POPULATED
//     BLOCKS after References (PR #178 commit 70a9290); bob_doc_midpoint
//     expectation refreshed.
// v5 (May 26 2026 — same day as v4) — adds ~25 new tests covering
// today's shipped functionality that had no prior coverage:
//   - Citation Review 3-level redesign (Finding > Type > Citation,
//     analytical findings source, gap warning, checkbox match,
//     relevance filter, show-all toggle) — 11 tests in Bob's section
//   - QA badge IN02 exclusion + acknowledge-flip behaviour + force
//     full-audit bypass — 3 tests in Molly's section
//   - S10 blue CONFIRMED INTENTIONAL badge — 1 test in Bob's section
//   - Submission pipeline step 4 status semantics + no_audit bypass —
//     2 tests in Bob's section
//   - Word count rationalization pass + warn-only over-budget — 2
//     tests in Bob's section
//   - Inline interpretation (no trailing [BOB] PRE-POPULATED blocks) +
//     Section-3 personalization callout — 2 tests in Bob's section
//   - Mobile drawer clearance on Settings + nav — 2 tests in
//     All Testers' section
//   - access_test_panel permission visibility after PR #185 back-fill —
//     2 tests in Michael's section
// v6 (May 28 2026) — landing-page past/present/future arc: 12 new tests
// in Molly's section covering the CIO Live Recommendation card, the
// Layer 4 forward confidence chart (three simulated series + outperform
// probabilities), and the combined-layout / Performance-Record-link
// checks.
// v7 (May 28 2026) — site restructure: / now serves Investment Outlook
// (not the dashboard); the dashboard merged into Analytics at /analytics.
// All Testers' dashboard-anchor steps (strategy-table, efficient-frontier,
// cumulative) repointed to /analytics; nav-order step updated. Molly gains
// interaction-pattern steps (info icons + explainer, chart tooltips,
// loading / error / responsive) and a nav-order check.
// v8 (May 29 2026) — Molly gains 18 Rebalancing History steps on the
// Council Performance Record page: Section 1 (Implied Asset Allocation —
// 100% rows, regime contrast, Largest Change, US dates, sort) and Section
// 2 (Strategy Blend Weights — all strategies, 100% rows, BULL vs
// BEAR/TRANSITION defensive weighting, Total Shift sanity), plus
// cross-section parity and responsive/tooltip checks.
// v9 (June 3 2026) — covers PR #257 (/admin/health runtime panel),
// PR #264 (AuditWarningsBanner in the document editor), and PR #265
// (council-metrics CIO-input aggregate). michael_ruurds_v1 gains six
// /admin/health steps (Settings quick-link → page load → invariant
// verdict → Layer 4 → warm history → any-user access) plus one curl
// check against /api/v1/admin/council-metrics confirming the
// cio_token_reduction_vs_baseline aggregate is in the response.
// molly_murdock_v1 gains five AuditWarningsBanner steps wedged between
// molly_deck and molly_export_zip (open the just-generated draft,
// banner renders, flag rows show finding+suggestion, expand/collapse,
// persists on re-open per migration 051).
export const TEST_SCRIPT_VERSION = 9

const allTesters: TestScript = {
  id: 'all_testers_v1',
  version: TEST_SCRIPT_VERSION,
  title: 'Section 1 — All Testers',
  assignedTo: 'all',
  steps: [
    // ── Onboarding and navigation ──────────────────────────────────────
    {
      id: 'tour_autolaunch', route: '/', target: null,
      title: 'Site tour availability',
      instruction: 'Recall whether the guided site tour launched on your '
        + 'first login, and open Settings → Account to find the Retake '
        + 'Site Tour button.',
      expectedResult: 'The tour auto-launched on first login, or the '
        + 'Retake Site Tour button is available in Settings.',
      allowSkip: true,
    },
    {
      id: 'tour_readable', route: '/', target: null,
      title: 'Tour steps are readable',
      instruction: 'Retake the site tour and read each step.',
      expectedResult: 'Every tour step reads clearly and makes sense.',
      allowSkip: true,
    },
    {
      id: 'tour_dismiss', route: '/', target: null,
      title: 'Tour dismisses cleanly',
      instruction: 'Complete or skip the site tour.',
      expectedResult: 'The tour closes cleanly — no leftover overlay or '
        + 'highlight remains on screen.',
      allowSkip: true,
    },
    {
      id: 'whats_new_modal', route: '/', target: null,
      title: "What's New modal",
      instruction: 'Recall the moment you logged in.',
      expectedResult: 'If there were unseen changelog entries, a What\'s '
        + 'New modal appeared; with nothing new, no modal showed.',
      allowSkip: true,
    },
    {
      id: 'nav_order', route: '/', target: '[data-tour="nav-dashboard"]',
      title: 'Navigation order',
      instruction: 'Look at the top navigation bar.',
      expectedResult: 'Items appear in order: Investment Outlook → '
        + 'Analytics → Council Record → Statistical Evidence → Regime '
        + 'Analysis → Council → QA Audit → Reports.',
      allowSkip: true,
    },
    {
      id: 'nav_active', route: '/', target: '[data-tour="nav-dashboard"]',
      title: 'Active nav item is distinct',
      instruction: 'Note how the current page is shown in the nav bar.',
      expectedResult: 'The active nav item is visually distinct from the '
        + 'others.',
      allowSkip: true,
    },
    {
      id: 'settings_gear', route: '/', target: null,
      title: 'Settings gear navigates',
      instruction: 'Click the gear icon at the right of the nav bar.',
      expectedResult: 'It navigates to the /settings page.',
      allowSkip: true,
    },
    {
      id: 'testing_toggle_visible', route: '/settings',
      target: '[data-tour="testing-mode"]',
      title: 'Testing Mode toggle visible',
      instruction: 'On the Settings page, find the Account section.',
      expectedResult: 'The Testing Mode toggle is visible in Settings → '
        + 'Account.',
      allowSkip: true,
    },
    {
      id: 'testing_pill', route: '/settings', target: null,
      title: 'Testing Mode pill in nav',
      instruction: 'With Testing Mode enabled (it is — you are running '
        + 'this test), look at the nav bar.',
      expectedResult: 'An amber "🧪 Testing Mode" pill is shown in the nav '
        + 'bar.',
      allowSkip: true,
    },
    // ── Analytics (the merged dashboard, now at /analytics) ────────────
    {
      id: 'dash_loads', route: '/analytics', target: null,
      title: 'Analytics page loads',
      instruction: 'Open Analytics (second in the nav).',
      expectedResult: 'The page loads with no visible errors. It holds the '
        + 'former dashboard content (strategy table, frontier, stress '
        + 'tests) plus the academic analytics below.',
      allowSkip: true,
    },
    {
      id: 'dash_cumulative', route: '/analytics', target: null,
      title: 'Cumulative return chart renders',
      instruction: 'Look at the cumulative returns chart on Analytics.',
      expectedResult: 'The chart renders with real data — it is not empty.',
      allowSkip: true,
    },
    {
      id: 'dash_strategy_table', route: '/analytics', target: '[data-tour="strategy-table"]',
      title: 'Strategy table shows 10 strategies',
      instruction: 'Look at the strategy comparison table.',
      expectedResult: 'All 10 strategies are listed.',
      allowSkip: true,
    },
    {
      id: 'dash_frontier', route: '/analytics', target: '[data-tour="efficient-frontier"]',
      title: 'Efficient frontier curve',
      instruction: 'Look at the efficient frontier chart.',
      expectedResult: 'The frontier curve is smooth and hyperbolic.',
      allowSkip: true,
    },
    {
      id: 'dash_export_buttons', route: '/analytics', target: null,
      title: 'Chart export buttons present',
      instruction: 'Look at the top-right of each Analytics chart.',
      expectedResult: 'Every chart has an export button.',
      allowSkip: true,
    },
    {
      id: 'dash_info_hover', route: '/analytics', target: '[data-tour="strategy-table"]',
      title: 'InfoIcon hover tooltip',
      instruction: 'Hover an ⓘ icon next to a strategy name or column '
        + 'header.',
      expectedResult: 'A tooltip appears with a plain-English description.',
      allowSkip: true,
    },
    {
      id: 'dash_info_click', route: '/analytics', target: '[data-tour="strategy-table"]',
      title: 'InfoIcon click opens explainer',
      instruction: 'Click an ⓘ icon.',
      expectedResult: 'The ExplainerPanel drawer opens and streams an '
        + 'explanation.',
      allowSkip: true,
    },
    {
      id: 'dash_ask_council', route: '/analytics', target: null,
      title: 'Explainer → Ask the Council prefill',
      instruction: 'In the ExplainerPanel, click "Ask the Council about '
        + 'this".',
      expectedResult: 'The Council screen opens with a question '
        + 'pre-populated in the field.',
      allowSkip: true,
    },
    // ── Council ────────────────────────────────────────────────────────
    {
      id: 'council_loads', route: '/council', target: '[data-tour="council"]',
      title: 'Council screen loads',
      instruction: 'Open the Council screen.',
      expectedResult: 'The page loads with no visible errors.',
      allowSkip: true,
    },
    {
      id: 'council_input', route: '/council', target: '[data-tour="council"]',
      title: 'Question field accepts input',
      instruction: 'Type a question into the "Ask the Council" field.',
      expectedResult: 'The field accepts the text.',
      allowSkip: true,
    },
    {
      id: 'council_submit', route: '/council', target: null,
      title: 'Council returns multi-agent responses',
      instruction: 'Submit a portfolio-analysis question and wait.',
      expectedResult: 'Responses come back from multiple agents.',
      allowSkip: true,
    },
    {
      id: 'council_markdown', route: '/council', target: null,
      title: 'Responses render as markdown',
      instruction: 'Read the agent responses.',
      expectedResult: 'Responses render as formatted markdown — not raw '
        + '* and # characters.',
      allowSkip: true,
    },
    {
      // v4 (May 26 2026) — Academic Review moved from Council to QA
      // Hub (commit 9ff578b). The `[data-tour="academic-review"]`
      // anchor now lives in AcademicReviewSection on QAHub.tsx.
      id: 'council_review_button', route: '/qa',
      target: '[data-tour="academic-review"]',
      title: 'Academic Review button is prominent',
      instruction: 'Open the QA Audit screen and find the Academic '
        + 'Review trigger.',
      expectedResult: 'It is a visually prominent amber card.',
      allowSkip: true,
    },
    {
      id: 'council_review_start', route: '/qa',
      target: '[data-tour="academic-review"]',
      title: 'Academic Review starts',
      instruction: 'Click the Academic Review button on QA Audit.',
      expectedResult: 'The review session begins.',
      allowSkip: true,
    },
    {
      id: 'council_review_loading', route: '/qa', target: null,
      title: 'Academic Review loading state',
      instruction: 'Watch the screen immediately after starting the '
        + 'review.',
      expectedResult: 'A loading state shows "Consulting the council…".',
      allowSkip: true,
    },
    {
      id: 'council_verdict', route: '/qa', target: null,
      title: 'Verdict renders with badges',
      instruction: 'Wait for the Academic Review verdict to finish.',
      expectedResult: 'The verdict renders with section headings and '
        + 'Strong / Developing / Needs Work rating badges.',
      allowSkip: true,
    },
    {
      id: 'council_peer_accordion', route: '/qa', target: null,
      title: 'Peer responses accordion',
      instruction: 'Find the peer responses section under the verdict.',
      expectedResult: 'The peer responses accordion expands and collapses.',
      allowSkip: true,
    },
    // ── Reports ────────────────────────────────────────────────────────
    {
      id: 'reports_loads', route: '/reports', target: null,
      title: 'Reports page loads',
      instruction: 'Open the Reports screen.',
      expectedResult: 'The page loads with no visible errors.',
      allowSkip: true,
    },
    {
      id: 'reports_generate_docs', route: '/reports', target: null,
      title: 'Generate Documents section',
      instruction: 'Look for the Generate Documents section on Reports.',
      expectedResult: 'The Generate Documents section is visible.',
      allowSkip: true,
    },
    {
      id: 'reports_team_activity', route: '/reports',
      target: '[data-tour="team-activity"]',
      title: 'Team Activity at top of Reports',
      instruction: 'Look at the top of the Reports page.',
      expectedResult: 'The Team Activity section is at the top.',
      allowSkip: true,
    },
    {
      id: 'reports_present_button', route: '/reports', target: null,
      title: 'Presentation View button',
      instruction: 'Find the Presentation View button in Team Activity.',
      expectedResult: 'The Presentation View button is visible.',
      allowSkip: true,
    },
    {
      id: 'reports_present_view', route: '/reports', target: null,
      title: 'Presentation View shows three charts',
      instruction: 'Click Presentation View.',
      expectedResult: 'Three charts display at screen-share scale.',
      allowSkip: true,
    },
    {
      id: 'reports_csv_export', route: '/reports', target: null,
      title: 'Team Activity CSV export',
      instruction: 'Use the CSV export on the Team Activity section.',
      expectedResult: 'A CSV file downloads.',
      allowSkip: true,
    },
    // ── Settings ───────────────────────────────────────────────────────
    {
      id: 'settings_sections', route: '/settings', target: null,
      title: 'Settings sections render',
      instruction: 'Scroll through the Settings page.',
      expectedResult: 'All sections render: Organization, Data and Study '
        + 'Period, Analytics Configuration, Academic Documents, Account, '
        + 'Release History.',
      allowSkip: true,
    },
    {
      id: 'settings_brand', route: '/settings', target: null,
      title: 'Brand switcher works',
      instruction: 'In Organization, switch between McColl and Forest '
        + 'Capital.',
      expectedResult: 'The branding changes across the app.',
      allowSkip: true,
    },
    {
      id: 'settings_data_status', route: '/settings', target: null,
      title: 'Data status pills',
      instruction: 'Look at the Data and Study Period section.',
      expectedResult: 'Each data table shows a green / amber / red '
        + 'staleness pill.',
      allowSkip: true,
    },
    {
      id: 'settings_academic_docs', route: '/settings', target: '#academic-documents',
      title: 'Academic Documents lists files',
      instruction: 'Find the Academic Documents section.',
      expectedResult: 'Uploaded documents are listed.',
      allowSkip: true,
    },
    {
      id: 'settings_release_history', route: '/settings', target: null,
      title: 'Release History',
      instruction: 'Find the Release History section.',
      expectedResult: 'Changelog entries are listed.',
      allowSkip: true,
    },
    {
      id: 'settings_retake_tour', route: '/settings', target: null,
      title: 'Retake Site Tour',
      instruction: 'Click the Retake Site Tour button in Account.',
      expectedResult: 'The site tour starts from the beginning.',
      allowSkip: true,
    },
    // ── Macro research tile (v2) ────────────────────────────────────────
    {
      id: 'macro_research_tile', route: '/', target: null,
      title: 'Macro research tile renders',
      instruction: 'Look at the Dashboard for the macro research tile.',
      expectedResult: 'The macro research tile is visible with a recent '
        + 'digest summary and last-updated timestamp.',
      allowSkip: true,
    },
    {
      id: 'macro_research_freshness', route: '/', target: null,
      title: 'Macro digest freshness indicator',
      instruction: 'Check the macro research tile timestamp.',
      expectedResult: 'The timestamp is within the last 24 hours, OR '
        + 'a (stale) warning is shown if older.',
      allowSkip: true,
    },
    {
      id: 'macro_citation_badges', route: '/', target: null,
      title: 'Macro digest citation badges',
      instruction: 'Look at the macro research tile signals.',
      expectedResult: 'Each signal carries a source URL badge linking to '
        + 'a trusted source (Fed / BIS / NBER / similar).',
      allowSkip: true,
    },
    // ── Explainer CIO follow-up (v2) ────────────────────────────────────
    {
      id: 'explainer_council_followup', route: '/analytics', target: null,
      title: 'Explainer → Ask the Council follow-up',
      instruction: 'Open an InfoIcon (ⓘ) explainer on a metric, then '
        + 'click "Ask the Council about this".',
      expectedResult: 'The Council screen opens with a contextual '
        + 'question pre-populated naming the metric and its value.',
      allowSkip: true,
    },
    {
      id: 'explainer_council_runs', route: '/council', target: null,
      title: 'Pre-populated question runs',
      instruction: 'On the Council screen, submit the pre-populated '
        + 'question (do not edit it).',
      expectedResult: 'The council answers the question with reference '
        + 'to the specific metric.',
      allowSkip: true,
    },
    // ── Diversification analytics charts (v2) ───────────────────────────
    {
      id: 'analytics_diversification', route: '/analytics', target: null,
      title: 'Diversification charts render',
      instruction: 'Scroll to the diversification section on Analytics.',
      expectedResult: 'Marginal Contribution to Risk, Capture Ratios, '
        + 'Correlation Heatmap, and Return Distribution charts all '
        + 'render with real data.',
      allowSkip: true,
    },
    {
      id: 'analytics_diversification_explainers', route: '/analytics', target: null,
      title: 'Diversification charts have explainers',
      instruction: 'Click the ⓘ on each diversification chart.',
      expectedResult: 'Each chart has a plain-English explanation.',
      allowSkip: true,
    },
    // ── Feedback backlog ID column (v2) ─────────────────────────────────
    {
      id: 'feedback_id_column', route: '/settings', target: null,
      title: 'Feedback backlog ID column',
      // UAT 2026-05-24 (#119) — clarified the permission scope.
      // The Test Administration section header is visible to anyone
      // with `access_test_panel` (team_member + sysadmin role
      // presets carry it). The data INSIDE — failures table,
      // feedback backlog, issue tracker — is gated on view_admin
      // (sysadmin only). A team_member sees the section heading
      // and an empty-state / 403 message inside; a sysadmin sees
      // the full data including the ID column.
      instruction: 'In Settings → Test Administration → Feedback '
        + 'Backlog, check the table columns. If you are signed in '
        + 'as a team_member (Bob, Molly) you can open the section '
        + 'but the data is sysadmin-only — pass this step if you '
        + 'see the section header. If you are signed in as the '
        + 'sysadmin (Michael), confirm the table carries an ID '
        + 'column showing each feedback row id.',
      expectedResult: 'Sysadmin: there is an ID column showing the '
        + 'feedback row id. Team-member: the Test Administration '
        + 'section header is visible (data shows an empty / 403 '
        + 'state — sysadmin-only by design).',
      allowSkip: true,
    },
    // ── Mobile FloatingSectionNav drawer clearance (v5, PR #171) ────────
    {
      id: 'mobile_drawer_nav_clearance', route: '/', target: null,
      title: 'Mobile drawer clears the nav bar',
      // v5 (May 26 2026, PR #171) — the FloatingSectionNav drawer
      // was overlapping the top nav at mobile widths. Fix shipped
      // a top-offset so the drawer starts below the 56px nav.
      instruction: 'Resize the browser to <640px width (or use a '
        + 'phone) and open any page that shows the section navigator '
        + '(QA Audit, Settings, Statistical Evidence, Regime '
        + 'Analysis, or Reports). Open the drawer.',
      expectedResult: 'The section drawer starts BELOW the top nav '
        + 'bar — no overlap; the nav stays clickable.',
      allowSkip: true,
    },
    {
      id: 'mobile_drawer_settings_overlap', route: '/settings', target: null,
      title: 'Mobile drawer does not cover Settings content',
      // v5 (May 26 2026, PR #171) — the drawer's z-index + position
      // was covering settings sections below it. Fix ensured the
      // drawer is a peer to content, not an overlay covering it.
      instruction: 'Resize to <640px width. Open Settings, scroll to '
        + 'the Data and Study Period section.',
      expectedResult: 'The section navigator does not obscure the '
        + 'staleness pills or table rows; content remains readable '
        + 'while the drawer is open.',
      allowSkip: true,
    },
  ],
}

const michael: TestScript = {
  id: 'michael_ruurds_v1',
  version: TEST_SCRIPT_VERSION,
  title: 'Section 2 — Michael Ruurds (Engineering & Analytics)',
  assignedTo: 'michael',
  steps: [
    // ── Analytics page ─────────────────────────────────────────────────
    {
      id: 'an_nav_position', route: '/analytics', target: '[data-tour="analytics-header"]',
      title: 'Analytics is second in nav',
      instruction: 'Look at the nav bar order.',
      expectedResult: 'Analytics sits second, directly after Dashboard.',
      allowSkip: true,
    },
    {
      id: 'an_six_components', route: '/analytics', target: '[data-tour="analytics-header"]',
      title: 'Analytics components render',
      instruction: 'Scroll through the Analytics page. The page now '
        + 'carries the original six plus the diversification suite '
        + '(Item 8) — fourteen components in total. Confirm each is '
        + 'present and rendering data, not blank.',
      // UAT 2026-05-24 (#118) — the prior list named six. The page
      // has 14 components since the Item 8 diversification suite +
      // sensitivity analysis + strategy methodology panel landed.
      // Return Distribution and Marginal Contribution to Risk
      // (MCTR) — both flagged as missing from the test — are now
      // explicitly listed.
      expectedResult: 'All fourteen Analytics components render with '
        + 'data: (1) summary statistics, (2) cumulative returns, '
        + '(3) rolling correlation, (4) rolling excess return, '
        + '(5) regime-conditional table, (6) drawdown comparison, '
        + '(7) drawdown duration, (8) tail risk (VaR / CVaR), '
        + '(9) up/down capture scatter, (10) crisis performance, '
        + '(11) marginal contribution to risk (MCTR), '
        + '(12) return distribution + normality, '
        + '(13) Carhart factor loadings, (14) parameter '
        + 'sensitivity. Strategy Methodology panel sits below the '
        + 'fourteen as a reference (not counted in the 14).',
      allowSkip: true,
    },
    {
      id: 'an_study_period', route: '/analytics', target: null,
      title: 'Study period',
      instruction: 'Find the study-period line on the Analytics page.',
      expectedResult: 'It shows the live study period in MM-DD-YYYY '
        + 'format (e.g. "07-31-2002 → 04-30-2026") with the current '
        + 'month count (286 or higher on Render — the pipeline '
        + 'auto-extends each time a calendar month closes).',
      allowSkip: true,
    },
    {
      id: 'an_rolling_corr_avgs', route: '/analytics', target: '[data-tour="rolling-correlation"]',
      title: 'Rolling correlation pre/post averages',
      instruction: 'Look at the rolling correlation chart footer.',
      expectedResult: 'Pre- and post-2022 average correlations are shown.',
      allowSkip: true,
    },
    {
      id: 'an_regime_marker', route: '/analytics', target: null,
      title: '2022 regime-break marker',
      instruction: 'Check the charts that span 2022.',
      expectedResult: 'The 2022 correlation regime-break marker is visible '
        + 'on all relevant charts.',
      allowSkip: true,
    },
    {
      id: 'an_factor_mom', route: '/analytics', target: '[data-tour="factor-loadings"]',
      title: 'Factor loadings MOM column',
      instruction: 'Look at the factor loadings table.',
      expectedResult: 'The MOM column is present — Carhart four-factor '
        + 'confirmed.',
      allowSkip: true,
    },
    {
      id: 'an_sensitivity', route: '/analytics', target: null,
      title: 'Sensitivity analysis renders',
      instruction: 'Find the sensitivity analysis section.',
      expectedResult: 'The sensitivity analysis section renders.',
      allowSkip: true,
    },
    {
      id: 'an_methodology_panel', route: '/analytics', target: null,
      title: 'Strategy methodology panel',
      instruction: 'Find the strategy methodology panel.',
      expectedResult: 'The methodology panel renders.',
      allowSkip: true,
    },
    {
      id: 'an_csv_exports', route: '/analytics', target: null,
      title: 'Analytics CSV exports',
      instruction: 'Use a CSV export on an Analytics table.',
      expectedResult: 'The CSV downloads correctly.',
      allowSkip: true,
    },
    {
      id: 'an_info_icons', route: '/analytics', target: null,
      title: 'Analytics InfoIcons',
      instruction: 'Hover and click ⓘ icons across the Analytics page.',
      expectedResult: 'All ⓘ icons are present and functional.',
      allowSkip: true,
    },
    {
      id: 'an_provenance', route: '/analytics', target: null,
      title: 'Data provenance annotations',
      instruction: 'Look below each Analytics component.',
      expectedResult: 'Data provenance annotations are visible.',
      allowSkip: true,
    },
    // ── Data integrity ─────────────────────────────────────────────────
    {
      id: 'di_cumulative_real', route: '/', target: null,
      title: 'Dashboard cumulative shows real data',
      instruction: 'Inspect the Dashboard cumulative chart shape.',
      expectedResult: 'It shows real strategy data — not synthetic noise.',
      allowSkip: true,
    },
    {
      id: 'di_sharpe_ci', route: '/', target: '[data-tour="strategy-table"]',
      title: 'Sharpe CI column',
      instruction: 'Look at the Sharpe CI column in the strategy table.',
      expectedResult: 'It shows real intervals or [—] — never a hardcoded '
        + '±0.10.',
      allowSkip: true,
    },
    {
      id: 'di_frontier_max_sharpe', route: '/', target: '[data-tour="efficient-frontier"]',
      title: 'Frontier max-Sharpe point',
      instruction: 'Find the max-Sharpe point on the efficient frontier.',
      expectedResult: 'It sits on or near the frontier curve.',
      allowSkip: true,
    },
    {
      id: 'di_regime_switching', route: '/', target: '[data-tour="efficient-frontier"]',
      title: 'Regime Switching vs frontier',
      instruction: 'Find Regime Switching on the efficient frontier.',
      expectedResult: 'It plots near or above the frontier — thesis '
        + 'validation.',
      allowSkip: true,
    },
    {
      id: 'di_factor_pvalues', route: '/analytics', target: '[data-tour="factor-loadings"]',
      title: 'Factor loadings p-values',
      instruction: 'Look at the factor loadings table cells.',
      expectedResult: 'Significant loadings carry a * marker — computed '
        + 'p-values, not hardcoded.',
      allowSkip: true,
    },
    {
      id: 'di_info_ratio', route: '/analytics', target: null,
      title: 'Information ratio for benchmark',
      instruction: 'Find the benchmark row in the summary statistics.',
      expectedResult: 'Information ratio shows N/A for the benchmark.',
      allowSkip: true,
    },
    // ── Settings — data admin ──────────────────────────────────────────
    {
      id: 'sda_15_tables', route: '/settings', target: null,
      title: 'Data and Study Period — table count',
      instruction: 'Look at the Data and Study Period section.',
      expectedResult: 'All data tables are listed with status.',
      allowSkip: true,
    },
    {
      id: 'sda_staleness_red', route: '/settings', target: null,
      title: 'Expected red staleness pills',
      instruction: 'Find market_data_monthly and ff_factors_monthly.',
      expectedResult: 'Both show a red staleness pill — expected, the '
        + 'dataset is locked at December 2025.',
      allowSkip: true,
    },
    {
      id: 'sda_academic_green', route: '/settings', target: null,
      title: 'academic_documents green',
      instruction: 'Find academic_documents in the table status list.',
      expectedResult: 'It shows green (recently uploaded).',
      allowSkip: true,
    },
    {
      id: 'sda_rf_rate', route: '/settings', target: null,
      title: 'Analytics Configuration risk-free rate',
      instruction: 'Look at the Analytics Configuration section.',
      expectedResult: 'It shows the DTB3 risk-free rate value.',
      allowSkip: true,
    },
    // ── Team Activity validation ───────────────────────────────────────
    {
      id: 'tav_michael_commits', route: '/reports', target: '[data-tour="team-activity"]',
      title: 'Commits in the timeline',
      instruction: 'Look at the Team Activity timeline.',
      expectedResult: "Michael Ruurds' commits appear in the timeline.",
      allowSkip: true,
    },
    {
      id: 'tav_commit_count', route: '/reports', target: null,
      title: 'Commit count',
      instruction: 'Check the commit count against git log.',
      expectedResult: 'The count is plausible (roughly 100).',
      allowSkip: true,
    },
    {
      id: 'tav_activity_chart', route: '/reports', target: null,
      title: 'Activity-over-time chart',
      instruction: 'Look at the activity-over-time chart.',
      expectedResult: 'It shows the project build history.',
      allowSkip: true,
    },
    {
      id: 'tav_present_quality', route: '/reports', target: null,
      title: 'Presentation View quality',
      instruction: 'Open Presentation View at a 1920×1080 viewport.',
      expectedResult: 'The charts are screen-share quality.',
      allowSkip: true,
    },
    // ── CI/CD ──────────────────────────────────────────────────────────
    {
      id: 'cicd_commit', route: '/', target: null,
      title: 'Trivial commit to main',
      instruction: 'Make a trivial commit to the main branch.',
      expectedResult: 'The commit is pushed successfully.',
      allowSkip: true,
    },
    {
      id: 'cicd_actions', route: '/', target: null,
      title: 'GitHub Actions passes',
      instruction: 'Watch the GitHub Actions workflow for that commit.',
      expectedResult: 'The workflow passes.',
      allowSkip: true,
    },
    {
      id: 'cicd_webhook', route: '/reports', target: null,
      title: 'Commit appears via webhook',
      instruction: 'Check Team Activity after the commit.',
      expectedResult: 'The commit appears within ~60 seconds via the '
        + 'webhook.',
      allowSkip: true,
    },
    {
      id: 'cicd_changelog_gate', route: '/', target: null,
      title: 'Changelog gate',
      instruction: 'Confirm the changelog gate behaviour in CI.',
      expectedResult: 'The gate passes — no new migration without a '
        + 'changelog entry.',
      allowSkip: true,
    },
    // ── Security ───────────────────────────────────────────────────────
    {
      id: 'sec_401', route: '/', target: null,
      title: 'Unauthenticated requests return 401',
      instruction: 'Call any /api/v1/* endpoint without authentication.',
      expectedResult: 'The request returns HTTP 401.',
      allowSkip: true,
    },
    {
      id: 'sec_secret_key', route: '/', target: null,
      title: 'SECRET_KEY fail-fast',
      instruction: 'Review the config.py production fail-fast logic.',
      expectedResult: 'Production startup fails if SECRET_KEY is unset.',
      allowSkip: true,
    },
    // ── Permissions — access_test_panel back-fill (v5, PR #185) ────────
    {
      id: 'permission_test_panel_visible', route: '/settings', target: null,
      title: 'Test Administration visible after permission back-fill',
      // v5 (May 26 2026, PR #185) — migration 046 back-filled the
      // access_test_panel permission onto every active team_member
      // and sysadmin row. Before the migration, every existing
      // platform_users row was missing the permission (it was added
      // to ROLE_PRESETS on May 24 with NO companion back-fill until
      // PR #185). After the migration + sign-out/in, the Test
      // Administration section renders on Settings.
      instruction: 'Sign out and sign back in (to refresh the JWT '
        + 'with the back-filled permission), then scroll to the '
        + 'bottom of the Settings page.',
      expectedResult: 'The Test Administration section is visible at '
        + 'the bottom of Settings (below Test Results), with Failure '
        + 'Reports / Feedback Backlog / Issue Tracker tabs.',
      allowSkip: true,
    },
    {
      id: 'permission_test_panel_team_member', route: '/settings', target: null,
      title: 'Team member sees Test Administration (read-only)',
      // v5 (May 26 2026, PR #185) — team_member preset carries
      // access_test_panel + view_uat_status; Bob and Molly see the
      // section and the data tables (read-only — mutate buttons
      // gate on manage_users).
      instruction: 'Sign in as Bob (thaob@) or Molly (murdockm@) and '
        + 'open Settings → Test Administration.',
      expectedResult: 'The section renders with Failure Reports / '
        + 'Feedback Backlog / Issue Tracker tabs visible. Data tables '
        + 'populate (not 403). Action buttons (Mark Resolved, etc.) '
        + 'are hidden or 403 on click — sysadmin-only.',
      allowSkip: true,
    },

    // ── /admin/health runtime panel — PR #257 (v9, June 3 2026) ─────
    // Six checks pinning the panel discoverability + every section.
    // Any authenticated user can read /admin/health; the page is
    // intentionally NOT sysadmin-gated so non-admin team members can
    // verify the analytical surface is live before a demo.
    {
      id: 'michael_health_nav',
      route: '/settings', target: '[data-tour="admin-health-runtime-link"]',
      title: 'Settings → Runtime health panel quick link',
      instruction: 'In Settings → Data and Study Period, find the '
        + '"Runtime health panel →" card.',
      expectedResult: 'The card is visible and shows /admin/health as '
        + 'the destination on the right.',
      allowSkip: false,
    },
    {
      id: 'michael_health_loads', route: '/settings', target: null,
      title: '/admin/health loads on click',
      instruction: 'Click the Runtime health panel card.',
      expectedResult: 'The browser navigates to /admin/health and the '
        + 'page renders without error.',
      allowSkip: false,
    },
    {
      id: 'michael_health_verdict', route: '/admin/health', target: null,
      title: 'Top-line invariant verdict shows',
      instruction: 'Read the top of the /admin/health page.',
      expectedResult: 'The top-line invariant verdict is visible '
        + 'showing PASS, WARN, or FAIL.',
      allowSkip: false,
    },
    {
      id: 'michael_health_layer4', route: '/admin/health', target: null,
      title: 'Layer 4 data-quality fixtures display',
      instruction: 'Scroll to the Layer 4 section.',
      expectedResult: 'Layer 4 display-fixture cards are visible with '
        + 'a per-fixture status indicator.',
      allowSkip: false,
    },
    {
      id: 'michael_health_history', route: '/admin/health', target: null,
      title: 'Warm history shows the last 7 runs',
      instruction: 'Scroll to the warm-history section.',
      expectedResult: 'At least one historical row is shown (last 7 '
        + 'runs).',
      allowSkip: true,
    },
    {
      id: 'michael_health_any_user', route: '/admin/health', target: null,
      title: 'Page loads for any authenticated user',
      instruction: 'Sign in as a non-sysadmin team member (Bob or '
        + 'Molly) and open /admin/health directly via the URL.',
      expectedResult: 'The page loads — no 401 / 403, no sysadmin '
        + 'gate.',
      allowSkip: true,
    },

    // ── Council metrics aggregate — PR #265 (v9, June 3 2026) ───────
    // Sysadmin-gated endpoint, no UI panel exists today, so the
    // assertion is a curl smoke check from a shell:
    //   curl -s 'https://analyticsdesk.app/api/v1/admin/council-metrics' \\
    //        -H 'X-API-Key: <MASTER_API_KEY>' | jq '.aggregates'
    // confirms the response carries cio_token_reduction_vs_baseline
    // per question_type — the like-for-like bundle signal PR #265
    // shipped. The frontend panel that consumes this is post-deadline
    // backlog; the endpoint contract is testable today.
    {
      id: 'michael_council_metrics_curl', route: '/settings', target: null,
      title: 'Council metrics endpoint: cio_token_reduction_vs_baseline',
      instruction: 'From a shell with MASTER_API_KEY set, curl '
        + 'GET /api/v1/admin/council-metrics on the production base '
        + 'URL with X-API-Key: $MASTER_API_KEY. Inspect the '
        + 'aggregates block in the response.',
      expectedResult: 'The endpoint returns 200, and the response '
        + 'carries cio_token_reduction_vs_baseline keyed per '
        + 'question_type (regime / recommendation / risk / '
        + 'statistical / forward) — present even when the value is '
        + 'null on a cold dataset.',
      allowSkip: true,
    },
  ],
}

const bob: TestScript = {
  id: 'bob_thao_v1',
  version: TEST_SCRIPT_VERSION,
  title: 'Section 3 — Bob Thao (Written Deliverables & Council)',
  assignedTo: 'bob',
  steps: [
    // ── Council for analytical interrogation ───────────────────────────
    {
      id: 'bob_council_nav', route: '/council', target: '[data-tour="council"]',
      title: 'Open the Council',
      instruction: 'Navigate to the Council screen.',
      expectedResult: 'The Council screen loads.',
      allowSkip: true,
    },
    {
      id: 'bob_council_ask', route: '/council', target: null,
      title: 'Ask the 2022 diversification question',
      instruction: 'Ask: "What is the strongest argument that '
        + 'diversification failed in 2022 based on our data?"',
      expectedResult: 'The council answers the question.',
      allowSkip: true,
    },
    {
      id: 'bob_council_quality', route: '/council', target: null,
      title: 'Response quality',
      instruction: 'Read the council response.',
      expectedResult: 'It is specific, cites actual metrics, and is '
        + 'well-formatted.',
      allowSkip: true,
    },
    {
      id: 'bob_explainer', route: '/analytics', target: null,
      title: 'Analytics metric explainer',
      instruction: 'Click an ⓘ on an Analytics metric you do not '
        + 'recognize.',
      expectedResult: 'The explanation makes sense.',
      allowSkip: true,
    },
    {
      id: 'bob_explainer_council', route: '/analytics', target: null,
      title: 'Explainer → Ask the Council',
      instruction: 'From an explainer, click "Ask the Council about '
        + 'this".',
      expectedResult: 'The question pre-populates correctly on the '
        + 'Council screen.',
      allowSkip: true,
    },
    // ── Academic Review ────────────────────────────────────────────────
    // v4 (May 26 2026) — Academic Review moved from Council to QA Hub
    // (commit 9ff578b). All seven steps in this block follow the
    // button to /qa.
    {
      id: 'bob_review_start', route: '/qa', target: '[data-tour="academic-review"]',
      title: 'Start an Academic Review',
      instruction: 'On the QA Audit screen, click the amber Academic '
        + 'Review button.',
      expectedResult: 'The review session starts.',
      allowSkip: true,
    },
    {
      id: 'bob_review_wait', route: '/qa', target: null,
      title: 'Wait for the verdict',
      instruction: 'Wait for the full verdict (30–45 seconds).',
      expectedResult: 'The verdict streams in and completes.',
      allowSkip: true,
    },
    {
      id: 'bob_review_sections', route: '/qa', target: null,
      title: 'Verdict has five sections',
      instruction: 'Read the verdict.',
      expectedResult: 'It has all five sections: Data Sufficiency, '
        + 'Requirements Alignment, Deliverable Quality, Priority '
        + 'Investigation, Overall Readiness.',
      allowSkip: true,
    },
    {
      id: 'bob_review_badges', route: '/qa', target: null,
      title: 'Section rating badges',
      instruction: 'Look at each verdict section.',
      expectedResult: 'Each section has a rating badge.',
      allowSkip: true,
    },
    {
      id: 'bob_review_priority', route: '/qa', target: null,
      title: 'Priority areas are specific',
      instruction: 'Read the Priority Investigation section.',
      expectedResult: 'Priority areas are specific and numbered.',
      allowSkip: true,
    },
    {
      id: 'bob_review_readiness', route: '/qa', target: null,
      title: 'Overall Readiness assessment',
      instruction: 'Read the Overall Readiness section.',
      expectedResult: 'It gives a clear, honest assessment.',
      allowSkip: true,
    },
    {
      id: 'bob_review_peers', route: '/qa', target: null,
      title: 'Peer responses accordion',
      instruction: 'Expand the peer responses accordion.',
      expectedResult: 'It shows multiple agent perspectives.',
      allowSkip: true,
    },
    // ── Document generation ────────────────────────────────────────────
    {
      id: 'bob_doc_midpoint', route: '/reports', target: null,
      title: 'Generate the midpoint paper',
      // v4 (May 26 2026) — PR #178 commit 70a9290 changed the writer
      // prompt so interpretation goes INLINE within sections 1-4
      // rather than into trailing [BOB] PRE-POPULATED BLOCKS at the
      // end of the document. PR #176 separately removed the [[BOB]]
      // section-callout boilerplate. The midpoint paper structure
      // is now: four headed sections with all analytical content
      // INSIDE the sections, References, end — no trailing orphan
      // paragraphs after References.
      instruction: 'In Generate Documents, click Generate Midpoint '
        + 'Paper, wait (30–60s), download the .docx and open it in '
        + 'Word.',
      expectedResult: 'The .docx is double-spaced 12 pt, has four '
        + 'headed sections with all analytical content interleaved '
        + 'INSIDE the sections (not in trailing [BOB] blocks after '
        + 'References), embedded data tables, team activity in '
        + 'Section 3, page numbers, runs to three pages or under. '
        + 'No [[BOB]] section-callout boilerplate. With warm caches '
        + 'no [DATA PENDING] markers.',
      allowSkip: true,
    },
    {
      id: 'bob_doc_brief', route: '/reports', target: null,
      title: 'Generate the executive brief',
      instruction: 'Click Generate Executive Brief, download and open it.',
      expectedResult: 'The .docx has five sections, a correctly formatted '
        + 'title page, embedded real-data tables, an investment-audience '
        + 'tone, and a Limitations section.',
      allowSkip: true,
    },
    // ── Settings — Academic Documents ──────────────────────────────────
    {
      id: 'bob_acdocs_nav', route: '/settings', target: '#academic-documents',
      title: 'Open Academic Documents',
      instruction: 'Go to Settings → Academic Documents.',
      expectedResult: 'The Academic Documents section loads.',
      allowSkip: true,
    },
    {
      id: 'bob_acdocs_md_files', route: '/settings', target: '#academic-documents',
      title: 'Both Markdown files listed',
      instruction: 'Look at the document list.',
      expectedResult: 'midpoint_requirements and '
        + 'final_presentation_requirements are both listed.',
      allowSkip: true,
    },
    {
      id: 'bob_acdocs_upload', route: '/settings', target: '#academic-documents',
      title: 'Upload a test .md file',
      instruction: 'Upload a small test .md file.',
      expectedResult: 'It appears in the document list.',
      allowSkip: true,
    },
    {
      id: 'bob_acdocs_delete', route: '/settings', target: '#academic-documents',
      title: 'Delete the test file',
      instruction: 'Delete the test .md file you just uploaded.',
      expectedResult: 'It is removed from the list.',
      allowSkip: true,
    },
    {
      id: 'bob_acdocs_link', route: '/reports', target: null,
      title: 'Reports → Settings link',
      instruction: 'Find the academic-documents annotation on the Reports '
        + 'screen.',
      expectedResult: 'It links correctly to Settings → Academic '
        + 'Documents.',
      allowSkip: true,
    },
    // ── Report Writer — verified-data midpoint paper flow (v2) ──────────
    {
      id: 'bob_writer_entry', route: '/reports', target: '[data-tour="report-writer-entry"]',
      title: 'Report Writer entry card',
      instruction: 'On the Reports screen, find the Report Writer card.',
      expectedResult: 'A Report Writer card is visible with an Open '
        + 'Report Writer link.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_open', route: '/reports/writer', target: null,
      title: 'Open the Report Writer',
      instruction: 'Click Open Report Writer.',
      expectedResult: 'The /reports/writer page loads with a template '
        + 'selector pre-selecting the FNA670 midpoint template.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_pipeline', route: '/reports/writer', target: null,
      title: 'Pipeline steps panel',
      instruction: 'Look at the left sidebar.',
      expectedResult: 'The Generation Pipeline shows all eleven steps '
        + '(Stage Findings → Download), each with a status pill.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_rubric', route: '/reports/writer', target: null,
      title: 'Rubric panel collapsible',
      instruction: 'Click the Grading Rubric panel to expand it.',
      expectedResult: 'It expands to show four criteria (Clarity & '
        + 'Rigor, Analytical Progress, Results Quality, Division of '
        + 'Labor) with indicators of success.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_generate', route: '/reports/writer', target: null,
      title: 'Generate Draft',
      instruction: 'Click Generate Draft. Wait for the writer to finish '
        + '(30–60 seconds).',
      expectedResult: 'The pipeline panel lights up steps 1–7 as '
        + 'Complete; step 8 shows a count of remaining callout points; '
        + 'the editor populates with the draft.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_bob_blocks', route: '/reports/writer', target: null,
      title: 'Inline [BOB] markers render',
      // v4 (May 26 2026) — PR #176 removed the [[BOB]] section-level
      // callouts from the report template; PR #178 commit 70a9290
      // changed the writer's prompt so all interpretation goes inline
      // rather than into trailing [BOB] PRE-POPULATED BLOCKS. What
      // remains are INLINE [BOB] / [DATA REQUIRED] / [CITATION
      // REQUIRED] markers within the section body that the editor
      // highlights as amber callout chips — not the prior full-block
      // callout badges.
      instruction: 'Scroll to the preview pane and look for inline '
        + 'amber-highlighted markers within the section text.',
      expectedResult: 'Inline [BOB] / [DATA REQUIRED] / [CITATION '
        + 'REQUIRED] markers are highlighted in the editor body. The '
        + 'paper does NOT have trailing [BOB] blocks appearing after '
        + 'the References section.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_resolve', route: '/reports/writer', target: null,
      title: 'Resolve a [BOB] block',
      instruction: 'Click any callout badge, enter replacement text, '
        + 'click Done.',
      expectedResult: 'The block is replaced inline; the callout count '
        + 'decrements; the editor reflects the new paper text.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_iterate_select', route: '/reports/writer', target: null,
      title: 'AI iteration toolbar enables on selection',
      instruction: 'Highlight a sentence in the editor.',
      expectedResult: 'The Rephrase / Tighten / Expand / Ask the Writer '
        + 'buttons become enabled.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_iterate_rephrase', route: '/reports/writer', target: null,
      title: 'Rephrase a selection',
      instruction: 'With text selected, click Rephrase. Wait for the '
        + 'proposal, then click Accept.',
      expectedResult: 'A proposal renders with the rewritten text. '
        + 'Accept replaces the selection inline.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_iterate_warnings', route: '/reports/writer', target: null,
      title: 'Iteration warns on new unverified numbers',
      instruction: 'Use Ask the Writer with an instruction that would '
        + 'introduce a fabricated number (e.g. "Add a Sharpe ratio of '
        + '1.75").',
      expectedResult: 'If the writer introduces a new unverified number, '
        + 'the proposal shows an amber warning naming it before Accept.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_word_counts', route: '/reports/writer', target: null,
      title: 'Word count sidebar updates',
      // v4 (May 26 2026) — PR #184 added the rationalization pass
      // BEFORE the post-check, so a section landing >10% over budget
      // is compressed in place on Generate. The colour rules still
      // apply, but red-status sections are typically rationalized
      // back to green/amber before the sidebar settles.
      instruction: 'Look at the Word Counts sidebar after Generate '
        + 'Draft completes.',
      expectedResult: 'Each section shows current words / budget; over '
        + 'budget renders amber, 10%+ over renders red. The May-26 '
        + 'rationalization pass typically compresses red sections '
        + 'back into the amber or green band before the sidebar '
        + 'settles, so seeing all green / amber is the expected '
        + 'happy path.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_final_check', route: '/reports/writer', target: null,
      title: 'Run Final Check',
      // v4 (May 26 2026) — PR #184 moved word_count_over_budget OUT
      // of flag_count and INTO a separate warning_count. A section
      // still over budget after rationalization is a warn-only
      // signal — it never blocks download.
      instruction: 'After resolving every [BOB] block, click Run Final '
        + 'Check.',
      expectedResult: 'Step 9 turns green; the flag count drops to 0; '
        + 'the Download Paper button becomes enabled. A red word-count '
        + 'badge on any section is warn-only (PR #184) and does NOT '
        + 'block download.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_academic_review', route: '/reports/writer', target: null,
      title: 'Run Academic Review',
      instruction: 'Click Run Academic Review.',
      expectedResult: 'The four-criterion review renders with score '
        + 'badges (Strong / Developing / Needs Work), a readiness pill '
        + '(Ready to Submit / Needs Minor Revision / Needs Significant '
        + 'Revision), and per-flag lists where applicable.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_suggestions', route: '/reports/writer', target: null,
      title: 'Apply a suggestion',
      instruction: 'Expand a criterion card and read its suggestion.',
      expectedResult: 'The suggestion is specific and actionable; the '
        + 'Apply suggestion button is visible for non-trivial gaps.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_download_paper', route: '/reports/writer', target: null,
      title: 'Download paper .docx',
      instruction: 'Click Download Paper.',
      expectedResult: 'A .docx file downloads with the FNA670 header, '
        + 'footer page numbers, four numbered sections, and Bob\'s '
        + 'resolved text inline (no remaining [BOB] markers).',
      allowSkip: true,
    },
    {
      id: 'bob_writer_download_appendix', route: '/reports/writer', target: null,
      title: 'Download appendix .docx',
      instruction: 'Click Download Appendix.',
      expectedResult: 'A second .docx downloads with four appendices '
        + '(A: Platform Overview, B: Full Findings, C: Team Activity '
        + 'Log, D: Validation Summary) plus a References section built '
        + 'from verified citations.',
      allowSkip: true,
    },
    // ── Citation Review — 3-level redesign (v5, PR #178 / #186 / #189) ──
    // The Citation Review panel was redesigned May 26 2026 to surface a
    // 3-level Finding > Type > Citation hierarchy. PR #186 added
    // analytical findings as a source; PR #187 made the sourcing prompt
    // request citation type diversity; PR #189 added a deterministic
    // concept_id-based relevance filter. These tests cover the new
    // panel surface end to end.
    {
      id: 'cr_panel_loads', route: '/reports/writer', target: null,
      title: 'Citation Review panel opens',
      instruction: 'Open the Report Writer, generate a draft, then '
        + 'click the Citation Review section header to expand it.',
      expectedResult: 'The panel renders with a header summary chip '
        + '("N findings · M citations") and one collapsible Finding '
        + 'section per high+medium-rank finding.',
      allowSkip: true,
    },
    {
      id: 'cr_finding_sources_badged', route: '/reports/writer', target: null,
      title: 'Finding source badges colour-coded by source',
      // v5 — PR #186 added analytical findings; the panel now shows
      // emerald (Analytical) / blue (Audit) / purple (QA) badges.
      instruction: 'Look at the badges on each Finding section header.',
      expectedResult: 'Three distinct badge colours: emerald for '
        + 'Analytical, blue for Audit, purple for QA. Each badge '
        + 'names the source.',
      allowSkip: true,
    },
    {
      id: 'cr_analytical_findings_visible', route: '/reports/writer', target: null,
      title: 'Analytical findings surface in the panel',
      // v5 — PR #186 bug fix. Before #186 the panel only listed
      // audit + QA findings; analytical findings from Step 1 were
      // invisible.
      instruction: 'After running Step 1 (Stage Findings), open '
        + 'Citation Review. Count the Analytical-source findings.',
      expectedResult: 'At least 6 HIGH and 4 MEDIUM analytical '
        + 'findings appear (BENCHMARK COMPETITIVENESS, REGIME SHIFT '
        + 'EVIDENCE, TAIL RISK DIVERGENCE, etc.), each with an '
        + 'emerald "Analytical" badge.',
      allowSkip: true,
    },
    {
      id: 'cr_type_subgroups', route: '/reports/writer', target: null,
      title: 'Citations grouped by 6-value citation type',
      // v5 — PR #178 6-value taxonomy + PR #187 sourcing prompt.
      instruction: 'Expand a Finding section that has multiple '
        + 'citations.',
      expectedResult: 'Citations are grouped into sub-headers by '
        + 'type — Theoretical / Empirical / Methodological / '
        + 'Regulatory / Data source / Practitioner. Each sub-header '
        + 'shows a "N of M matched" count.',
      allowSkip: true,
    },
    {
      id: 'cr_type_diversity_in_sourcing', route: '/reports/writer', target: null,
      title: 'Step 2 sourcing produces type-diverse citations',
      // v5 — PR #187 taught the sourcing prompt the 6-value taxonomy
      // and added a diversity steer across passes. Before the PR
      // every citation came back as "theoretical".
      instruction: 'Trigger Step 2 (Source Citations) and open '
        + 'Citation Review. Look at the citation types across the '
        + 'panel.',
      expectedResult: 'Citations span at least 2 distinct types '
        + 'across the panel — not all theoretical. Data-anchored '
        + 'concepts (FRED series, BAML index) tend to land as '
        + 'data_source; regulatory concepts as regulatory.',
      allowSkip: true,
    },
    {
      id: 'cr_gap_warning', route: '/reports/writer', target: null,
      title: 'Finding with 0 matches shows the gap warning',
      // v5 — PR #178 redesign.
      instruction: 'Find a Finding section with no matched citations '
        + 'yet (matched_count = 0).',
      expectedResult: 'The section renders an amber "No supporting '
        + 'citations yet — tick a citation below to record it as '
        + 'evidence for this finding" warning.',
      allowSkip: true,
    },
    {
      id: 'cr_checkbox_match', route: '/reports/writer', target: null,
      title: 'Tick a citation against a finding',
      // v5 — PR #178 redesign. matched_count increments via the
      // POST /api/v1/citations/match endpoint.
      instruction: 'Find a relevant citation under a finding and '
        + 'tick its checkbox.',
      expectedResult: 'The checkbox flips checked; the Finding '
        + 'section header\'s "N matched" count increments by 1.',
      allowSkip: true,
    },
    {
      id: 'cr_checkbox_unmatch', route: '/reports/writer', target: null,
      title: 'Untick a citation against a finding',
      // v5 — PR #178 redesign. DELETE /api/v1/citations/match.
      instruction: 'Untick a previously checked citation.',
      expectedResult: 'The checkbox flips unchecked; the Finding\'s '
        + '"N matched" count decrements by 1; the citation reflows '
        + 'to the unmatched position within its type sub-group.',
      allowSkip: true,
    },
    {
      id: 'cr_relevance_filter_default', route: '/reports/writer', target: null,
      title: 'Citations pre-filtered by concept_id relevance',
      // v5 — PR #189 (concept_id whole-word match, replacing the
      // broken token-overlap heuristic from PR #188).
      instruction: 'Expand a Finding section. Count the citations '
        + 'visible by default.',
      expectedResult: 'Only citations whose concept_id whole-word '
        + 'matches against the finding\'s title or description '
        + 'render. Typical count: 1–3 per finding, not all 10. A '
        + 'relevance summary line shows "Showing N of M citations '
        + 'relevant to this finding · [Show all]".',
      allowSkip: true,
    },
    {
      id: 'cr_show_all_toggle', route: '/reports/writer', target: null,
      title: '"Show all" reveals every citation under the finding',
      // v5 — PR #188/#189 escape hatch for relevance-heuristic
      // false negatives.
      instruction: 'Click the "Show all" link in a Finding section\'s '
        + 'relevance summary.',
      expectedResult: 'Every citation in the pool renders under the '
        + 'finding (typically all 10). The toggle text flips to '
        + '"Show only relevant".',
      allowSkip: true,
    },
    {
      id: 'cr_matched_always_visible', route: '/reports/writer', target: null,
      title: 'A matched citation renders even when not heuristically relevant',
      // v5 — PR #188/#189 matched-citation bypass. The user\'s
      // explicit match always wins over the heuristic.
      instruction: 'Tick a citation against a finding it is NOT '
        + 'heuristically relevant to (Show all first to find it; '
        + 'tick the checkbox; toggle back to "Show only relevant").',
      expectedResult: 'The matched citation continues to render in '
        + 'the default view even though the relevance heuristic '
        + 'would have hidden it. Untick it and it disappears from '
        + 'the default view (the relevance filter applies again).',
      allowSkip: true,
    },
    // ── QA badge (v5, PR #176) ──────────────────────────────────────────
    {
      id: 'qa_badge_in02_excluded', route: '/qa', target: null,
      title: 'QA badge excludes IN02 (Academic Review attestation)',
      // v5 — PR #176. IN02 is the Academic Review attestation; its
      // WARN state is by design (a manual reviewer action is
      // required before a paper can ship). It must never contribute
      // to the QA badge count.
      instruction: 'Open the QA Audit screen. Find the IN02 row in '
        + 'the methodology checklist.',
      expectedResult: 'IN02 displays its current state (typically '
        + 'WARN) but does NOT contribute to the QA badge\'s warning '
        + 'count. The badge\'s "N warnings remaining" number EXCLUDES '
        + 'IN02.',
      allowSkip: true,
    },
    {
      id: 'qa_badge_acknowledge_flip', route: '/qa', target: null,
      title: 'Acknowledging the last WARN flips the badge to green',
      // v5 — PR #176. The QA badge turns green when every WARN
      // is acknowledged (the IN02 exclusion above + acknowledged
      // warnings counted as PASS).
      instruction: 'Acknowledge every WARN finding on the QA Audit '
        + 'screen via the per-card Acknowledge action.',
      expectedResult: 'After the last WARN is acknowledged, the QA '
        + 'badge in the nav bar flips from amber to green.',
      allowSkip: true,
    },
    {
      id: 'qa_badge_force_full_audit', route: '/qa', target: null,
      title: 'Force Full Audit bypasses the cache',
      // v5 — PR #171 4-fix bundle. The Force Audit button passes
      // force=true to the run endpoint so a fresh audit runs even
      // when the data-hash cache says one is current.
      instruction: 'Click "Force Full Audit" on the QA Audit screen.',
      expectedResult: 'A new audit run starts (visible in the run '
        + 'history) even if the most recent run is the same data '
        + 'hash. The audit completes with a fresh timestamp.',
      allowSkip: true,
    },
    // ── S10 blue CONFIRMED INTENTIONAL badge (v5, PR #177) ──────────────
    {
      id: 's10_blue_intentional_badge', route: '/qa', target: null,
      title: 'S10 shows blue CONFIRMED INTENTIONAL, not amber',
      // v5 — PR #177. S10 is the "single-strategy bias" disclosure.
      // It is intentionally documented in the paper, not a defect,
      // so the methodology audit renders it as blue CONFIRMED
      // INTENTIONAL rather than amber WARN.
      instruction: 'Find S10 in the methodology checklist on the QA '
        + 'Audit screen.',
      expectedResult: 'S10 renders with a blue "CONFIRMED INTENTIONAL" '
        + 'badge, not an amber WARN.',
      allowSkip: true,
    },
    // ── Submission pipeline step 4 (v5, PR #177) ────────────────────────
    {
      id: 'pipeline_step4_complete_on_audit_pass',
      route: '/reports/writer', target: null,
      title: 'Pipeline step 4 turns complete when audit passes',
      // v5 — PR #177. Step 4 (Pull Validation Data) status used to
      // be derived from run.status === \'pass\' (always false — the
      // enum is complete | failed | running). Fixed to use
      // auditCompleted && failed === 0 semantic.
      instruction: 'Trigger Step 4 after a clean audit run.',
      expectedResult: 'Step 4 turns green-complete when the audit '
        + 'has run, all layers completed, and zero failures. It '
        + 'does NOT stay amber.',
      allowSkip: true,
    },
    {
      id: 'pipeline_step4_no_audit_bypass',
      route: '/reports/writer', target: null,
      title: 'Step 4 with _no_audit:true does not block downstream',
      // v5 — PR #177. A Step 4 warning with _no_audit:true is the
      // canonical bypass flag (no statistical audit was needed for
      // this draft).
      instruction: 'Generate a draft for a template that does not '
        + 'require statistical audit; observe Step 4 + downstream.',
      expectedResult: 'Step 4 renders as warning with a _no_audit '
        + 'marker but downstream steps still fire. The pipeline is '
        + 'not blocked.',
      allowSkip: true,
    },
    // ── Word count rationalization + warn-only over-budget (v5, PR #184) ──
    {
      id: 'writer_rationalization_compresses',
      route: '/reports/writer', target: null,
      title: 'Sections >10% over budget compress automatically',
      // v5 — PR #184. The rationalization pass runs BEFORE the
      // post-check on Generate Draft, compressing sections that
      // would land red on the word-count sidebar.
      instruction: 'Click Generate Draft on the FNA670 midpoint '
        + 'template. Wait for the writer to finish (30–60s). Look '
        + 'at the Word Counts sidebar.',
      expectedResult: 'All four sections land within ±10% of their '
        + 'budgets on the FIRST generate (no red badge typically). '
        + 'Any pre-rationalization red state was compressed away by '
        + 'the pass.',
      allowSkip: true,
    },
    {
      id: 'writer_word_count_warn_only',
      route: '/reports/writer', target: null,
      title: 'Red word-count is warn-only, never blocks download',
      // v5 — PR #184. word_count_over_budget moved from flag_count
      // (hard gate) to warning_count (visible-but-non-blocking).
      instruction: 'Resolve every [BOB] block but leave a section '
        + 'red on word count. Click Run Final Check, then Download '
        + 'Paper.',
      expectedResult: 'Run Final Check turns Step 9 green and the '
        + 'flag count drops to 0 EVEN WITH a red word-count badge '
        + 'on a section. Download Paper becomes enabled.',
      allowSkip: true,
    },
    // ── Inline interpretation (v5, PR #178 commit 70a9290) ──────────────
    {
      id: 'writer_no_trailing_bob_blocks',
      route: '/reports/writer', target: null,
      title: 'No trailing [BOB] PRE-POPULATED BLOCKS after References',
      // v5 — PR #178 commit 70a9290. The Academic Writer prompt was
      // instructed to emit [BOB] PRE-POPULATED BLOCKS at the end of
      // the paper for a downstream merge step that never existed.
      // Fixed: interpretation goes inline within sections 1-4.
      instruction: 'Generate the midpoint paper and scroll to the '
        + 'bottom of the .docx after References.',
      expectedResult: 'The document ends cleanly at References. NO '
        + 'orphan [BOB] paragraphs, NO trailing "Our ..." sentences '
        + 'restating Section 2 content. Every analytical claim sits '
        + 'inline within sections 1-4.',
      allowSkip: true,
    },
    {
      id: 'writer_section3_personalization_callout',
      route: '/reports/writer', target: null,
      title: 'Section 3 carries the BOB personalization callout',
      // v5 — PR #178 + earlier. Section 3 (Roles and Division of
      // Labor) is pre-seeded from real team activity but Bob must
      // personalize it — the callout reminds him.
      instruction: 'Scroll to Section 3 of the generated paper.',
      expectedResult: 'Section 3 carries a boxed amber "BOB — '
        + 'PERSONALIZE THIS SECTION" callout above the AI-drafted '
        + 'roles summary. The AI draft uses real activity counts; '
        + 'Bob is reminded to put it in his own voice.',
      allowSkip: true,
    },
  ],
}

const molly: TestScript = {
  id: 'molly_murdock_v1',
  version: TEST_SCRIPT_VERSION,
  title: 'Section 4 — Molly Murdock (Presentation & Visualisation)',
  assignedTo: 'molly',
  steps: [
    // ── Chart quality and comprehension ────────────────────────────────
    {
      id: 'molly_hover_tooltips', route: '/analytics', target: null,
      title: 'Chart hover tooltips',
      instruction: 'Hover the ⓘ on each Analytics chart.',
      expectedResult: 'Each tooltip explains the chart clearly in plain '
        + 'English.',
      allowSkip: true,
    },
    {
      id: 'molly_rolling_corr', route: '/analytics', target: '[data-tour="rolling-correlation"]',
      title: 'Rolling Correlation explanation',
      instruction: 'Click the ⓘ on Rolling Correlation.',
      expectedResult: 'The explanation mentions the 2022 regime break.',
      allowSkip: true,
    },
    {
      id: 'molly_factor', route: '/analytics', target: '[data-tour="factor-loadings"]',
      title: 'Factor Loadings explanation',
      instruction: 'Click the ⓘ on Factor Loadings.',
      expectedResult: 'The explanation covers the Carhart four-factor '
        + 'model.',
      allowSkip: true,
    },
    {
      id: 'molly_frontier', route: '/', target: '[data-tour="efficient-frontier"]',
      title: 'Efficient Frontier explanation',
      instruction: 'Click the ⓘ on the Efficient Frontier.',
      expectedResult: 'The explanation covers a dynamic strategy plotting '
        + 'above the curve.',
      allowSkip: true,
    },
    {
      id: 'molly_marker_consistency', route: '/analytics', target: null,
      title: 'Regime-break marker consistency',
      instruction: 'Compare the 2022 marker across Rolling Correlation, '
        + 'Rolling Excess Return and Cumulative Returns.',
      expectedResult: 'The 2022 regime-break marker is consistent across '
        + 'all three.',
      allowSkip: true,
    },
    // ── Presentation View ──────────────────────────────────────────────
    {
      id: 'molly_present_open', route: '/reports', target: '[data-tour="team-activity"]',
      title: 'Open Presentation View',
      instruction: 'In Reports → Team Activity, click Presentation View.',
      expectedResult: 'Presentation View opens.',
      allowSkip: true,
    },
    {
      id: 'molly_present_charts', route: '/reports', target: null,
      title: 'Three presentation charts',
      instruction: 'Look at the Presentation View.',
      expectedResult: 'Three charts display: activity over time, team '
        + 'contribution split, agent engagement breakdown.',
      allowSkip: true,
    },
    {
      id: 'molly_present_readable', route: '/reports', target: null,
      title: 'Readable at 1920×1080',
      instruction: 'View Presentation View at a 1920×1080 viewport.',
      expectedResult: 'The charts are readable at projected-room scale.',
      allowSkip: true,
    },
    {
      id: 'molly_present_data', route: '/reports', target: null,
      title: 'Presentation charts show real data',
      instruction: 'Inspect the Presentation View charts.',
      expectedResult: 'The charts show real data — not empty.',
      allowSkip: true,
    },
    {
      id: 'molly_present_members', route: '/reports', target: null,
      title: 'All three team members shown',
      instruction: 'Look at the Platform Interaction Split donut.',
      expectedResult: 'All three team members appear.',
      allowSkip: true,
    },
    {
      id: 'molly_present_exit', route: '/reports', target: null,
      title: 'Exit Presentation View',
      instruction: 'Close Presentation View.',
      expectedResult: 'It exits cleanly back to the Reports screen.',
      allowSkip: true,
    },
    // ── Presentation deck ──────────────────────────────────────────────
    {
      id: 'molly_deck', route: '/reports', target: null,
      title: 'Generate the presentation deck',
      instruction: 'In Generate Documents, click Generate Presentation '
        + 'Deck, wait (30–60s), download the .pptx and open it.',
      expectedResult: 'The deck has 16 slides, a navy/white professional '
        + 'theme, a correct title slide, embedded charts (slide 5 rolling '
        + 'correlation, slide 8 cumulative returns), real activity counts '
        + 'on slide 15, readable text throughout, and no placeholder text.',
      allowSkip: true,
    },
    // ── Audit Warnings Banner — PR #264 (v9, June 3 2026) ──────────────
    // Five checks pinning the editor-side surface of the four
    // deterministic document audit checks. The banner reads from
    // editor_drafts.audit_warnings (migration 051), so its state
    // persists across re-opens — covered by the last step.
    {
      id: 'molly_audit_open', route: '/reports', target: null,
      title: 'Open freshly-generated draft in the editor',
      instruction: 'After Generate Presentation Deck completes, click '
        + 'Open in Editor on the job card (don\'t just Download).',
      expectedResult: 'The editor loads the just-generated draft at '
        + '/editor/<id>.',
      allowSkip: false,
    },
    {
      id: 'molly_audit_banner_renders',
      route: '/reports',
      target: '[data-testid="audit-warnings-banner"]',
      title: 'Audit warnings banner renders',
      instruction: 'Look at the top of the editor under the AI DRAFT '
        + 'banner.',
      expectedResult: 'An audit warnings banner is present — either '
        + 'showing flag rows or a "no warnings" state. Never absent.',
      allowSkip: false,
    },
    {
      id: 'molly_audit_banner_rows', route: '/reports', target: null,
      title: 'Each flag carries finding + suggested fix',
      instruction: 'If the banner shows flag rows, read at least one '
        + 'row.',
      expectedResult: 'Each row carries a specific finding (numeric '
        + 'mismatch, label direction error, cross-section '
        + 'inconsistency, or missing citation) and a suggested fix.',
      allowSkip: true,
    },
    {
      id: 'molly_audit_banner_toggle', route: '/reports', target: null,
      title: 'Banner expands and collapses',
      instruction: 'Click the expand / collapse control on the banner.',
      expectedResult: 'The banner toggles between collapsed (count '
        + 'only) and expanded (full flag list) states.',
      allowSkip: true,
    },
    {
      id: 'molly_audit_banner_persists', route: '/reports', target: null,
      title: 'Banner state persists across re-open',
      instruction: 'Close the editor tab. Return to /reports. Re-open '
        + 'the same draft.',
      expectedResult: 'The same audit warnings appear again — state is '
        + 'stored on the draft (editor_drafts.audit_warnings, '
        + 'migration 051), not in component state.',
      allowSkip: true,
    },
    // ── Export package ─────────────────────────────────────────────────
    {
      id: 'molly_export_zip', route: '/reports', target: null,
      title: 'Export Academic Package',
      instruction: 'Click Export Academic Package, let the progress steps '
        + 'complete, and download the ZIP.',
      expectedResult: 'The ZIP contains /charts/ PNGs, /tables/ CSVs and '
        + '/metadata/ (study_period.txt, README.txt); the charts are '
        + 'light mode and high resolution.',
      allowSkip: true,
    },
    {
      id: 'molly_export_chart', route: '/reports', target: null,
      title: 'Exported chart quality',
      instruction: 'Open one exported chart PNG in an image viewer.',
      expectedResult: 'It is suitable for embedding in a Word document.',
      allowSkip: true,
    },
    // ── Peer review preparation ────────────────────────────────────────
    // v4 (May 26 2026) — Academic Review moved from Council to QA Hub
    // (commit 9ff578b). molly_peer_review + molly_peer_readiness +
    // molly_peer_priority follow the Academic Review verdict on /qa.
    // molly_peer_questions and molly_peer_quality stay on /council
    // because they exercise the regular Council "ask a question" flow,
    // not the Academic Review verdict.
    {
      id: 'molly_peer_review', route: '/qa', target: '[data-tour="academic-review"]',
      title: 'Run an Academic Review',
      instruction: 'On the QA Audit screen, run an Academic Review '
        + 'session.',
      expectedResult: 'The verdict completes.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_readiness', route: '/qa', target: null,
      title: 'Read Overall Readiness',
      instruction: 'Read the Overall Readiness section of the verdict.',
      expectedResult: 'It reads as a clear, honest assessment.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_priority', route: '/qa', target: null,
      title: 'Identify the top priority area',
      instruction: 'Find the top Priority Area for Further Investigation.',
      expectedResult: 'A clear top priority area is identifiable.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_questions', route: '/council', target: null,
      title: 'Ask about peer-review questions',
      instruction: 'On the Council screen, ask: "What questions might '
        + 'a peer reviewer ask about our regime analysis methodology?"',
      expectedResult: 'The council answers.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_quality', route: '/council', target: null,
      title: 'Peer-prep response quality',
      instruction: 'Read the response.',
      expectedResult: 'It is specific and helpful for presentation '
        + 'preparation.',
      allowSkip: true,
    },
    // ── QA audit WARN/FAIL cards (v2) ──────────────────────────────────
    {
      id: 'molly_qa_open', route: '/qa', target: null,
      title: 'QA Audit screen loads',
      instruction: 'Navigate to the QA Audit tab.',
      expectedResult: 'The QA dashboard loads with the latest audit run.',
      allowSkip: true,
    },
    {
      id: 'molly_qa_warn_card', route: '/qa', target: null,
      title: 'WARN findings card',
      instruction: 'Look for any WARN cards in the QA findings list.',
      expectedResult: 'WARN cards (when present) show their detail and '
        + 'an acknowledge action.',
      allowSkip: true,
    },
    {
      id: 'molly_qa_fail_card', route: '/qa', target: null,
      title: 'FAIL findings card',
      instruction: 'Look for any FAIL cards in the QA findings list.',
      expectedResult: 'FAIL cards (when present) clearly stand out from '
        + 'PASS / WARN cards visually.',
      allowSkip: true,
    },
    // ── Failure reports resolution modal (v2) ──────────────────────────
    {
      id: 'molly_failures_open', route: '/settings', target: null,
      title: 'Failure Reports list',
      instruction: 'In Settings → Test Administration, scroll to Failure '
        + 'Reports.',
      expectedResult: 'The Failure Reports table loads with any '
        + 'recorded failures.',
      allowSkip: true,
    },
    {
      id: 'molly_failures_resolve', route: '/settings', target: null,
      title: 'Open the resolution modal',
      instruction: 'Click Resolve on a failure row.',
      expectedResult: 'The resolution modal opens with fields for '
        + 'resolution type, root cause, and remediation note.',
      allowSkip: true,
    },
    {
      id: 'molly_failures_resolve_cancel', route: '/settings', target: null,
      title: 'Cancel the resolution modal',
      instruction: 'Close the resolution modal without saving.',
      expectedResult: 'The modal closes; the failure remains Open.',
      allowSkip: true,
    },
    // ── Issue Tracker tab (v2) ─────────────────────────────────────────
    {
      id: 'molly_issue_tracker', route: '/settings', target: null,
      title: 'Issue Tracker tab',
      instruction: 'In Settings → Test Administration, find the Issue '
        + 'Tracker tab.',
      expectedResult: 'The Issue Tracker tab is visible and lists '
        + 'tracked items with their GitHub issue links where available.',
      allowSkip: true,
    },
    {
      id: 'molly_issue_tracker_filter', route: '/settings', target: null,
      title: 'Issue Tracker filters',
      instruction: 'Use the Issue Tracker filters.',
      expectedResult: 'Filters narrow the list correctly (by status, '
        + 'severity, or type).',
      allowSkip: true,
    },
    // ── Landing-page past/present/future arc (v6) ──────────────────────
    {
      id: 'molly_cio_card_renders', route: '/', target: null,
      title: 'CIO Live Recommendation card renders',
      instruction: 'On the landing page, find the CIO Live Recommendation '
        + 'card as the first component, above the fold.',
      expectedResult: 'The CIO Live Recommendation card is the first thing '
        + 'on the landing page, before the regime banner and charts.',
      allowSkip: false,
    },
    {
      id: 'molly_cio_card_regime', route: '/', target: null,
      title: 'CIO card regime and confidence',
      instruction: 'Read the regime label and confidence on the CIO card.',
      expectedResult: 'The regime label (BULL, BEAR, or TRANSITION) and a '
        + 'confidence percentage are both displayed.',
      allowSkip: false,
    },
    {
      id: 'molly_cio_card_fields', route: '/', target: null,
      title: 'CIO card four narrative fields',
      instruction: 'Read the CIO card body.',
      expectedResult: 'Signal, Recommendation, Dissenting view, and Key '
        + 'risk are all present, each one sentence.',
      allowSkip: false,
    },
    {
      id: 'molly_cio_card_limitations', route: '/', target: null,
      title: 'CIO card limitations collapse',
      instruction: 'Click the Limitations toggle on the CIO card.',
      expectedResult: 'The limitations panel is collapsible and expands to '
        + 'show the four mandatory limitations.',
      allowSkip: false,
    },
    {
      id: 'molly_cio_card_asof_cfa', route: '/', target: null,
      title: 'CIO card staleness and disclosure',
      instruction: 'Look at the top-right and bottom of the CIO card.',
      expectedResult: 'An "As of <timestamp>" indicator and the CFA-style '
        + 'disclosure statement are both visible.',
      allowSkip: false,
    },
    {
      id: 'molly_cio_card_empty', route: '/', target: null,
      title: 'CIO card empty state',
      instruction: 'If the recommendation has not been computed yet, note '
        + 'how the card behaves (otherwise skip).',
      expectedResult: 'When uncached, the card shows a graceful empty state '
        + 'rather than an error or a blank box.',
      allowSkip: true,
    },
    {
      id: 'molly_forward_chart_renders', route: '/', target: null,
      title: 'Forward confidence chart, three series',
      instruction: 'Find the Forward Confidence Projection chart (second '
        + 'landing component). Inspect the lines and legend.',
      expectedResult: 'Three colour-coded series each with a median line '
        + 'and 90% band are present and named in the legend: '
        + 'regime-conditional blend, benchmark (S&P 500), classic 60/40.',
      allowSkip: false,
    },
    {
      id: 'molly_forward_chart_bands', route: '/', target: null,
      title: 'Forward chart confidence bounds',
      instruction: 'Look at each series on the forward chart.',
      expectedResult: 'Upper (95th) and lower (5th) bounds are visible for '
        + 'each of the three series.',
      allowSkip: false,
    },
    {
      id: 'molly_forward_chart_prob', route: '/', target: null,
      title: 'Forward chart outperformance probabilities',
      instruction: 'Read the probability table below the forward chart.',
      expectedResult: 'P(blend outperforms benchmark) and P(blend '
        + 'outperforms 60/40) are shown at 1, 3, 6, and 12 months, every '
        + 'figure between 0% and 100%.',
      allowSkip: false,
    },
    {
      id: 'molly_forward_chart_meta', route: '/', target: null,
      title: 'Forward chart regime, limitation, timestamp',
      instruction: 'Read the forward chart header and footer.',
      expectedResult: 'The current regime + confidence, the "Not a '
        + 'forecast" limitation note, and an "As of <timestamp>" indicator '
        + 'are all present.',
      allowSkip: false,
    },
    {
      id: 'molly_forward_chart_empty', route: '/', target: null,
      title: 'Forward chart empty state',
      instruction: 'If the forward simulation has not been computed yet, '
        + 'note the chart behaviour (otherwise skip).',
      expectedResult: 'When uncomputed, the chart shows a graceful empty '
        + 'state rather than an error or a blank box.',
      allowSkip: true,
    },
    {
      id: 'molly_landing_arc_layout', route: '/', target: null,
      title: 'Landing arc layout and Performance Record link',
      instruction: 'View the landing page on both desktop and a narrow '
        + '(mobile) width. Then click the Council Performance Record '
        + 'preview card.',
      expectedResult: 'All three components (CIO card, forward chart, '
        + 'Performance Record preview) render together without layout '
        + 'breaks at both widths, and the preview links to '
        + '/performance-record.',
      allowSkip: false,
    },
    // ── Investment Outlook interaction patterns (v7) ───────────────────
    {
      id: 'molly_outlook_nav_order', route: '/', target: null,
      title: 'Nav front door is Investment Outlook',
      instruction: 'Open the site root and read the nav order.',
      expectedResult: 'The root / serves Investment Outlook (not the '
        + 'analytics dashboard), and the nav reads Investment Outlook, '
        + 'then Analytics, then Council Record.',
      allowSkip: false,
    },
    {
      id: 'molly_outlook_info_icons', route: '/', target: null,
      title: 'Info icons on Outlook technical terms',
      instruction: 'On the Investment Outlook page, find the ⓘ icons next '
        + 'to the regime label, confidence, the recommendation structure, '
        + 'key risk, the CFA disclosure, the forward chart title, and '
        + 'P(blend outperforms).',
      expectedResult: 'An ⓘ icon is present on each technical term.',
      allowSkip: false,
    },
    {
      id: 'molly_outlook_explainer', route: '/', target: null,
      title: 'Info icon opens context-aware explainer',
      instruction: 'Click an ⓘ icon (e.g. on the regime label), then click '
        + 'one on a different term (e.g. P(outperform)).',
      expectedResult: 'Clicking opens the explainer drawer, and the text is '
        + 'relevant to the specific term clicked (different per term).',
      allowSkip: false,
    },
    {
      id: 'molly_outlook_chart_tooltips', route: '/', target: null,
      title: 'Forward chart hover tooltips',
      instruction: 'Hover the forward confidence chart on desktop.',
      expectedResult: 'A tooltip appears at the hovered point showing the '
        + 'series values for that horizon.',
      allowSkip: true,
    },
    {
      id: 'molly_outlook_loading_error', route: '/', target: null,
      title: 'Outlook loading and empty/error states',
      instruction: 'Reload the Investment Outlook page and watch each tile '
        + 'load; note behaviour if a tile has no data.',
      expectedResult: 'Tiles show a loading state during fetch, then either '
        + 'the data or a graceful empty/error state, consistent with the '
        + 'rest of the platform (no blank boxes or raw errors).',
      allowSkip: true,
    },
    {
      id: 'molly_outlook_responsive', route: '/', target: null,
      title: 'Outlook responsive on mobile',
      instruction: 'View the Investment Outlook page at a narrow (mobile) '
        + 'width.',
      expectedResult: 'All three components reflow cleanly with no '
        + 'horizontal overflow or broken layout.',
      allowSkip: false,
    },

    // ── Rebalancing History — Section 1: Implied Asset Allocation ──────
    {
      id: 'molly_rebal_aa_renders', route: '/performance-record', target: null,
      title: 'Implied Asset Allocation table renders',
      instruction: 'On the Council Performance Record page, scroll to the '
        + '"Implied Asset Allocation" table — it sits directly below the '
        + '"Net of Switching Costs" table.',
      expectedResult: 'The Implied Asset Allocation table is present below '
        + 'Net of Switching Costs.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_aa_columns', route: '/performance-record', target: null,
      title: 'Three asset columns present',
      instruction: 'Read the Implied Asset Allocation column headers.',
      expectedResult: 'Equity, IG Bonds, and HY Bonds columns are all '
        + 'present (alongside Date, Regime, and Largest Change).',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_aa_sum100', route: '/performance-record', target: null,
      title: 'Asset rows sum to 100%',
      instruction: 'Spot-check at least three rows: add Equity % + IG Bonds '
        + '% + HY Bonds %.',
      expectedResult: 'Every checked row sums to 100% (allow ±0.1% for '
        + 'rounding).',
      allowSkip: false,
    },
    {
      id: 'molly_rebal_aa_nonzero', route: '/performance-record', target: null,
      title: 'No all-zero asset row',
      instruction: 'Scan the asset columns down every row.',
      expectedResult: 'No row shows 0% across all three asset columns.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_aa_regime_diff', route: '/performance-record', target: null,
      title: 'Allocation differs by regime',
      instruction: 'Compare the asset allocation on a BULL-regime row '
        + 'against a BEAR-regime row.',
      expectedResult: 'BULL rows show a materially different (more '
        + 'equity-tilted) allocation than BEAR rows.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_aa_largest_change', route: '/performance-record', target: null,
      title: 'Largest Change column',
      instruction: 'Read the Largest Change column on the rebalancing rows.',
      expectedResult: 'Largest Change is present and non-zero on rebalancing '
        + 'rows, naming an asset class and a percentage-point move.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_aa_dates', route: '/performance-record', target: null,
      title: 'Dates in MM/DD/YYYY',
      instruction: 'Read the Date column of the Implied Asset Allocation '
        + 'table.',
      expectedResult: 'Dates display in US MM/DD/YYYY format.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_aa_sort', route: '/performance-record', target: null,
      title: 'Sorted newest first',
      instruction: 'Read the dates top to bottom.',
      expectedResult: 'Rows are sorted newest first (most recent date at '
        + 'the top).',
      allowSkip: true,
    },

    // ── Rebalancing History — Section 2: Strategy Blend Weights ────────
    {
      id: 'molly_rebal_sw_all_strategies', route: '/performance-record', target: null,
      title: 'All strategies present as columns',
      instruction: 'Scroll the "Strategy Blend Weights" table horizontally '
        + 'and read every strategy column header.',
      expectedResult: 'All ten strategies appear as columns (Benchmark, '
        + '60/40, Equal Wt, Risk Parity, Min Var, Black-Litt, Momentum, '
        + 'Regime Sw, Vol Target, Max Sharpe).',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_sw_sum100', route: '/performance-record', target: null,
      title: 'Strategy rows sum to 100%',
      instruction: 'Spot-check at least three rows: add every strategy '
        + 'weight across the row.',
      expectedResult: 'Every checked row sums to 100% (allow ±1% for '
        + 'whole-number rounding across ten columns).',
      allowSkip: false,
    },
    {
      id: 'molly_rebal_sw_bull_low', route: '/performance-record', target: null,
      title: 'BULL: low defensive weights',
      instruction: 'On a BULL-regime row, read the Min Var and Risk Parity '
        + 'columns.',
      expectedResult: 'In BULL, Min Variance and Risk Parity show near-zero '
        + 'weights.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_sw_bear_high', route: '/performance-record', target: null,
      title: 'BEAR/TRANSITION: elevated defensive weights',
      instruction: 'On a BEAR or TRANSITION row, read the Min Var and Risk '
        + 'Parity columns.',
      expectedResult: 'In BEAR/TRANSITION, Min Variance and Risk Parity show '
        + 'elevated weights relative to BULL rows.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_sw_no_blank', route: '/performance-record', target: null,
      title: 'No blank strategy cell',
      instruction: 'Scan every cell of the Strategy Blend Weights table.',
      expectedResult: 'No strategy column is blank or missing on any row '
        + '(a 0% weight shows "0%", never empty).',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_sw_total_shift', route: '/performance-record', target: null,
      title: 'Total Shift is reasonable',
      instruction: 'Read the Total Shift column on each row.',
      expectedResult: 'Total Shift shows a reasonable value (roughly 1%–'
        + '100%). Flag any row showing 0% or greater than 100%.',
      allowSkip: true,
    },

    // ── Rebalancing History — both sections ────────────────────────────
    {
      id: 'molly_rebal_row_parity', route: '/performance-record', target: null,
      title: 'Row count matches between sections',
      instruction: 'Count the data rows in Implied Asset Allocation and in '
        + 'Strategy Blend Weights.',
      expectedResult: 'Both tables have the same number of rows (the same '
        + 'rebalancing events).',
      allowSkip: false,
    },
    {
      id: 'molly_rebal_date_parity', route: '/performance-record', target: null,
      title: 'Dates match between sections',
      instruction: 'Compare the Date column of the two tables row by row.',
      expectedResult: 'The dates match exactly between the two sections.',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_responsive', route: '/performance-record', target: null,
      title: 'No layout break (desktop + mobile)',
      instruction: 'View both rebalancing tables on a desktop width and a '
        + 'narrow (mobile) width.',
      expectedResult: 'Both tables scroll horizontally without breaking the '
        + 'page layout on either width (no overflow off-screen, no clipped '
        + 'content).',
      allowSkip: true,
    },
    {
      id: 'molly_rebal_tooltips', route: '/performance-record', target: null,
      title: 'Info-icon tooltips present',
      instruction: 'Hover the ⓘ next to each rebalancing table heading.',
      expectedResult: 'Both sections show a readable tooltip explaining what '
        + 'a rebalancing event is.',
      allowSkip: true,
    },
  ],
}

export const TEST_SCRIPTS: TestScript[] = [allTesters, michael, bob, molly]

/** Look up a script by id. */
export function getTestScript(scriptId: string): TestScript | undefined {
  return TEST_SCRIPTS.find((s) => s.id === scriptId)
}

/** The role-specific script for a tester's email, or undefined. */
export function scriptForEmail(email: string): TestScript | undefined {
  const e = email.toLowerCase()
  if (e.startsWith('ruurdsm@')) return michael
  if (e.startsWith('thaob@')) return bob
  if (e.startsWith('murdockm@')) return molly
  return undefined
}
