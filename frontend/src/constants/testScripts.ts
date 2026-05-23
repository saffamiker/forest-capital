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
export const TEST_SCRIPT_VERSION = 2

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
      expectedResult: 'Items appear in order: Dashboard → Analytics → '
        + 'Statistical Evidence → Regime Analysis → Council → QA Audit → '
        + 'Reports.',
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
    // ── Dashboard ──────────────────────────────────────────────────────
    {
      id: 'dash_loads', route: '/', target: null,
      title: 'Dashboard loads',
      instruction: 'Open the Dashboard.',
      expectedResult: 'The page loads with no visible errors.',
      allowSkip: true,
    },
    {
      id: 'dash_cumulative', route: '/', target: null,
      title: 'Cumulative return chart renders',
      instruction: 'Look at the cumulative returns chart on the Dashboard.',
      expectedResult: 'The chart renders with real data — it is not empty.',
      allowSkip: true,
    },
    {
      id: 'dash_strategy_table', route: '/', target: '[data-tour="strategy-table"]',
      title: 'Strategy table shows 10 strategies',
      instruction: 'Look at the strategy comparison table.',
      expectedResult: 'All 10 strategies are listed.',
      allowSkip: true,
    },
    {
      id: 'dash_frontier', route: '/', target: '[data-tour="efficient-frontier"]',
      title: 'Efficient frontier curve',
      instruction: 'Look at the efficient frontier chart.',
      expectedResult: 'The frontier curve is smooth and hyperbolic.',
      allowSkip: true,
    },
    {
      id: 'dash_export_buttons', route: '/', target: null,
      title: 'Chart export buttons present',
      instruction: 'Look at the top-right of each Dashboard chart.',
      expectedResult: 'Every chart has an export button.',
      allowSkip: true,
    },
    {
      id: 'dash_info_hover', route: '/', target: '[data-tour="strategy-table"]',
      title: 'InfoIcon hover tooltip',
      instruction: 'Hover an ⓘ icon next to a strategy name or column '
        + 'header.',
      expectedResult: 'A tooltip appears with a plain-English description.',
      allowSkip: true,
    },
    {
      id: 'dash_info_click', route: '/', target: '[data-tour="strategy-table"]',
      title: 'InfoIcon click opens explainer',
      instruction: 'Click an ⓘ icon.',
      expectedResult: 'The ExplainerPanel drawer opens and streams an '
        + 'explanation.',
      allowSkip: true,
    },
    {
      id: 'dash_ask_council', route: '/', target: null,
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
      id: 'council_review_button', route: '/council',
      target: '[data-tour="academic-review"]',
      title: 'Academic Review button is prominent',
      instruction: 'Find the Academic Review trigger on the Council '
        + 'screen.',
      expectedResult: 'It is a visually prominent amber card.',
      allowSkip: true,
    },
    {
      id: 'council_review_start', route: '/council',
      target: '[data-tour="academic-review"]',
      title: 'Academic Review starts',
      instruction: 'Click the Academic Review button.',
      expectedResult: 'The review session begins.',
      allowSkip: true,
    },
    {
      id: 'council_review_loading', route: '/council', target: null,
      title: 'Academic Review loading state',
      instruction: 'Watch the screen immediately after starting the '
        + 'review.',
      expectedResult: 'A loading state shows "Consulting the council…".',
      allowSkip: true,
    },
    {
      id: 'council_verdict', route: '/council', target: null,
      title: 'Verdict renders with badges',
      instruction: 'Wait for the Academic Review verdict to finish.',
      expectedResult: 'The verdict renders with section headings and '
        + 'Strong / Developing / Needs Work rating badges.',
      allowSkip: true,
    },
    {
      id: 'council_peer_accordion', route: '/council', target: null,
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
      instruction: 'In Settings → Test Administration → Feedback Backlog, '
        + 'check the table columns.',
      expectedResult: 'There is an ID column showing the feedback row id.',
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
      title: 'All six analytics components render',
      instruction: 'Scroll through the Analytics page.',
      expectedResult: 'Cumulative returns, rolling correlation, rolling '
        + 'excess return, regime-conditional table, drawdown comparison '
        + 'and factor loadings all render.',
      allowSkip: true,
    },
    {
      id: 'an_study_period', route: '/analytics', target: null,
      title: 'Study period',
      instruction: 'Find the study-period line on the Analytics page.',
      expectedResult: 'It shows 2002-07-31 to 2025-12-31 (282 months).',
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
        + 'recognise.',
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
    {
      id: 'bob_review_start', route: '/council', target: '[data-tour="academic-review"]',
      title: 'Start an Academic Review',
      instruction: 'Click the amber Academic Review button.',
      expectedResult: 'The review session starts.',
      allowSkip: true,
    },
    {
      id: 'bob_review_wait', route: '/council', target: null,
      title: 'Wait for the verdict',
      instruction: 'Wait for the full verdict (30–45 seconds).',
      expectedResult: 'The verdict streams in and completes.',
      allowSkip: true,
    },
    {
      id: 'bob_review_sections', route: '/council', target: null,
      title: 'Verdict has five sections',
      instruction: 'Read the verdict.',
      expectedResult: 'It has all five sections: Data Sufficiency, '
        + 'Requirements Alignment, Deliverable Quality, Priority '
        + 'Investigation, Overall Readiness.',
      allowSkip: true,
    },
    {
      id: 'bob_review_badges', route: '/council', target: null,
      title: 'Section rating badges',
      instruction: 'Look at each verdict section.',
      expectedResult: 'Each section has a rating badge.',
      allowSkip: true,
    },
    {
      id: 'bob_review_priority', route: '/council', target: null,
      title: 'Priority areas are specific',
      instruction: 'Read the Priority Investigation section.',
      expectedResult: 'Priority areas are specific and numbered.',
      allowSkip: true,
    },
    {
      id: 'bob_review_readiness', route: '/council', target: null,
      title: 'Overall Readiness assessment',
      instruction: 'Read the Overall Readiness section.',
      expectedResult: 'It gives a clear, honest assessment.',
      allowSkip: true,
    },
    {
      id: 'bob_review_peers', route: '/council', target: null,
      title: 'Peer responses accordion',
      instruction: 'Expand the peer responses accordion.',
      expectedResult: 'It shows multiple agent perspectives.',
      allowSkip: true,
    },
    // ── Document generation ────────────────────────────────────────────
    {
      id: 'bob_doc_midpoint', route: '/reports', target: null,
      title: 'Generate the midpoint paper',
      instruction: 'In Generate Documents, click Generate Midpoint Paper, '
        + 'wait (30–60s), download the .docx and open it in Word.',
      expectedResult: 'The .docx is double-spaced 12 pt, has four headed '
        + 'sections, embedded data tables, team activity in Section 3, '
        + 'page numbers, runs to three pages or under, and (if a review '
        + 'has been run and caches are warm) has no [DATA PENDING].',
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
      title: '[BOB] callout blocks render',
      instruction: 'Scroll to the preview pane.',
      expectedResult: 'Any [BOB] / [DATA REQUIRED] / [CITATION REQUIRED] '
        + 'markers are highlighted as amber callout badges.',
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
      instruction: 'Look at the Word Counts sidebar.',
      expectedResult: 'Each section shows current words / budget; over '
        + 'budget renders amber, 10%+ over renders red.',
      allowSkip: true,
    },
    {
      id: 'bob_writer_final_check', route: '/reports/writer', target: null,
      title: 'Run Final Check',
      instruction: 'After resolving every [BOB] block, click Run Final '
        + 'Check.',
      expectedResult: 'Step 9 turns green; the flag count drops to 0; '
        + 'the Download Paper button becomes enabled.',
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
      instruction: 'Look at the team contribution split.',
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
    {
      id: 'molly_peer_review', route: '/council', target: '[data-tour="academic-review"]',
      title: 'Run an Academic Review',
      instruction: 'Run an Academic Review session.',
      expectedResult: 'The verdict completes.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_readiness', route: '/council', target: null,
      title: 'Read Overall Readiness',
      instruction: 'Read the Overall Readiness section of the verdict.',
      expectedResult: 'It reads as a clear, honest assessment.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_priority', route: '/council', target: null,
      title: 'Identify the top priority area',
      instruction: 'Find the top Priority Area for Further Investigation.',
      expectedResult: 'A clear top priority area is identifiable.',
      allowSkip: true,
    },
    {
      id: 'molly_peer_questions', route: '/council', target: null,
      title: 'Ask about peer-review questions',
      instruction: 'Ask the council: "What questions might a peer '
        + 'reviewer ask about our regime analysis methodology?"',
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
