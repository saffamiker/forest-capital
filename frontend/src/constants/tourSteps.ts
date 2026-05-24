/**
 * tourSteps.ts
 *
 * The site-tour step definitions. One tour serves three audiences —
 * Forest Capital (a professional investment audience), the McColl
 * School faculty (rubric compliance), and the project team — so every
 * step's second paragraph connects the feature to the grade, and
 * relevant steps name the team member they matter most to.
 *
 * The tour is multi-route: a step's `route` is the page the tour
 * navigates to before the step shows. SiteTour pauses, navigates, and
 * resumes once the new page's `target` has rendered.
 *
 * Adding or removing steps changes the walkthrough but NOT the trigger
 * cadence — bump TOUR_VERSION in backend/config.py (and ship a
 * changelog entry) when the tour should re-surface for users who have
 * already seen it.
 */

export interface TourStep {
  /** Optional stable identifier. The changelog table's tour_step_id
   *  column links a changelog entry to a step by this id. */
  id?: string
  /** CSS selector for the highlighted element, or "body" for a centred
   *  modal step with no target. */
  target: string
  title: string
  /** Two paragraphs: (1) what the feature does, (2) why it matters. */
  body: [string, string]
  placement?: 'top' | 'bottom' | 'left' | 'right' | 'center'
  /** Route to navigate to before this step shows, when the feature is
   *  not on the current page. */
  route?: string
  /** Optional "Most relevant for: …" line — names the team member. */
  relevantFor?: string
}

export const TOUR_STEPS: TourStep[] = [
  {
    id: 'welcome',
    target: 'body',
    placement: 'center',
    route: '/',
    title: 'Welcome to the Portfolio Intelligence System',
    body: [
      'This platform was built to answer one question: does '
      + 'diversification across equities and fixed income improve '
      + 'risk-adjusted performance — and does that answer change after 2022?',
      'Every feature here exists to help you make that case rigorously to '
      + 'two audiences: Forest Capital and the McColl School of Business. '
      + 'This tour will show you how.',
    ],
  },
  {
    target: '[data-tour="nav-dashboard"]',
    placement: 'bottom',
    route: '/',
    title: 'Dashboard — Your Command Centre',
    body: [
      'Ten portfolio strategies ranked by risk-adjusted performance, all '
      + 'measured against the 100% equity benchmark.',
      "This is your at-a-glance answer to the project's core question. "
      + 'Every metric here — Sharpe ratio, drawdown, turnover, tier ranking '
      + '— maps directly to the performance metrics required by the project '
      + 'rubric.',
    ],
  },
  {
    target: '[data-tour="strategy-table"]',
    placement: 'top',
    route: '/',
    title: 'Strategy Rankings',
    body: [
      'Each row is a fully backtested portfolio strategy spanning 282 '
      + 'months of data. Sharpe ratios include 95% confidence intervals and '
      + 'are FDR-corrected for multiple comparisons.',
      'Hover any column header for a plain English explanation. Click for a '
      + 'live AI explanation of what the current values mean for your '
      + 'specific analysis.',
    ],
    relevantFor:
      'Molly — hover any metric you are presenting for a plain English '
      + 'explanation. Click for a live AI breakdown of what the current '
      + 'values mean.',
  },
  {
    target: '[data-tour="efficient-frontier"]',
    placement: 'top',
    route: '/',
    title: 'Efficient Frontier',
    body: [
      'The theoretical optimum — the maximum return achievable for each '
      + 'level of risk using a static mix of the three asset classes.',
      'Dynamic strategies that plot above this curve achieve returns no '
      + 'static allocation can match. That gap is your empirical argument '
      + 'for active management — and one of the most compelling visuals in '
      + 'your final presentation.',
    ],
  },
  {
    target: '[data-tour="analytics-header"]',
    placement: 'bottom',
    route: '/analytics',
    title: 'Academic Analytics — The Evidence Base',
    body: [
      'Six analytical components built specifically to support the '
      + "project's academic deliverables. Every table exports to CSV for "
      + 'the Analytical Appendix.',
      'This page is the quantitative backbone of your midpoint paper and '
      + 'executive brief. Faculty will expect everything here — asset class '
      + 'statistics, correlation analysis, factor loadings, and regime '
      + 'performance — to be present and interpreted.',
    ],
  },
  {
    target: '[data-tour="cumulative-return"]',
    placement: 'top',
    route: '/analytics',
    title: 'Cumulative Total Return',
    body: [
      'Growth of $1 invested at inception across all ten strategies and '
      + 'the benchmark — the foundational portfolio comparison visual.',
      'This is slide one of your final presentation. The divergence '
      + 'between strategies and benchmark across the full study period is the visual '
      + 'evidence that allocation decisions matter.',
    ],
    relevantFor: 'Molly — this is slide one of the final presentation.',
  },
  {
    target: '[data-tour="rolling-correlation"]',
    placement: 'top',
    route: '/analytics',
    title: 'The 2022 Correlation Regime Break',
    body: [
      'The equity-IG bond correlation shifted from -0.05 (diversifying) to '
      + '+0.61 (positively correlated) after the 2022 Federal Reserve '
      + 'hiking cycle.',
      'This is your central finding. The chart makes the thesis visible — '
      + 'the diversification assumption that underpins classic asset '
      + 'allocation broke down decisively in 2022. Every other analytical '
      + 'result should be interpreted in light of this.',
    ],
    relevantFor:
      'Molly — this is the centrepiece of the presentation narrative. '
      + 'Know this chart inside out.',
  },
  {
    target: '[data-tour="regime-conditional"]',
    placement: 'top',
    route: '/analytics',
    title: 'Which Strategies Survived 2022?',
    body: [
      "Every strategy's performance split at the correlation regime break "
      + '— pre-2022 versus post-2022 Sharpe and CAGR.',
      "This directly addresses Part I's secondary objective: identifying "
      + 'and explaining periods of outperformance and underperformance. '
      + 'Strategies with positive post-2022 Sharpe are your strongest '
      + 'candidates.',
    ],
    relevantFor:
      "Bob — this table directly answers Part I's secondary objective. "
      + 'Cite it in the Preliminary Results section.',
  },
  {
    target: '[data-tour="factor-loadings"]',
    placement: 'top',
    route: '/analytics',
    title: 'Carhart Four-Factor Analysis',
    body: [
      "OLS regression of each strategy's monthly excess returns on the "
      + 'market, size, value, and momentum factors — graduate-level factor '
      + "analysis that shows what drives each strategy's returns.",
      'Alpha is the return unexplained by factor exposure. Significant '
      + 'positive alpha means a strategy adds value beyond passive factor '
      + 'harvesting. This is the methodological rigour that separates a '
      + 'strong Analytical Appendix from a basic one.',
    ],
    relevantFor:
      'Bob — cite the four-factor regression in the methodology section '
      + 'of the executive brief. It demonstrates analytical rigour.',
  },
  {
    target: '[data-tour="council"]',
    placement: 'center',
    route: '/council',
    title: 'AI Council of Experts',
    body: [
      'Seven specialist AI agents — each with a distinct expert lens — '
      + 'available to interrogate your analysis, challenge your '
      + 'assumptions, and stress-test your conclusions.',
      'Ask the council anything about your methodology, findings, or '
      + 'presentation. Every response is scored and refined by a '
      + 'generator-evaluator harness before it reaches you — raising the '
      + 'quality floor on every insight.',
    ],
    relevantFor:
      'Bob — use the council to stress-test analytical claims before '
      + 'writing them up. Ask it to review a specific finding or challenge '
      + 'an assumption.',
  },
  {
    target: '[data-tour="academic-review"]',
    placement: 'bottom',
    route: '/council',
    title: 'Academic Review — Your Quality Gate',
    body: [
      'The most important feature on this platform. One click has the full '
      + 'council evaluate your analytics, methodology, and deliverables '
      + 'against the project rubric — before faculty do.',
      'The academic advisor synthesises all peer assessments into a '
      + 'five-section rubric-mapped verdict with explicit Strong / '
      + 'Developing / Needs Work ratings. Run this after every major '
      + 'analytical update. The sooner you find gaps, the more time you '
      + 'have to close them.',
    ],
    relevantFor:
      'All team members — run this before every major submission',
  },
  {
    target: '[data-tour="team-activity"]',
    placement: 'top',
    route: '/reports',
    title: 'Team Activity — Your AI Use Narrative',
    body: [
      'Every council interaction, Academic Review session, document '
      + 'upload, and git commit — logged, attributed, and visualized as a '
      + 'timeline of the project from day one.',
      'The professor requires the team to discuss how AI was leveraged at '
      + 'the final presentation. This is your objective evidence. Switch to '
      + 'Presentation View for a clean, full-screen version ready to show '
      + 'on screen during that discussion.',
    ],
    relevantFor:
      'Molly — switch to Presentation View before the final presentation. '
      + 'This is the visual you show faculty during the AI use narrative.',
  },
  {
    id: 'document-editor',
    target: '[data-tour="generate-documents"]',
    placement: 'top',
    route: '/reports',
    title: 'Your draft, ready to refine',
    body: [
      'Everything you generate opens directly in the in-platform editor. '
      + 'Work through your draft here — every edit, every resolved marker, '
      + 'every version save is tracked and part of your submission record. '
      + 'Nothing you do is lost, and nothing is untracked.',
      'The AI wrote the first draft. The grader reads what you do next.',
    ],
    relevantFor:
      'Bob and Molly — the midpoint paper and the presentation deck both '
      + 'open in the editor; refine them here, not in Word.',
  },
  {
    target: '#academic-documents',
    placement: 'top',
    route: '/settings',
    title: 'Academic Documents — Agent Context',
    body: [
      'Upload your project requirements, rubric, midpoint draft, and '
      + 'presentation materials here. These documents are injected into '
      + 'every AI agent session automatically.',
      'The council cannot evaluate your work against the grading criteria '
      + 'until these are uploaded. The midpoint draft is especially '
      + 'important — once uploaded, the Academic Review shifts from '
      + 'evaluating methodology in the abstract to giving direct feedback '
      + 'on your actual submission.',
    ],
    relevantFor:
      'Bob — upload your midpoint draft here once written. The council '
      + 'cannot evaluate your submission until it is uploaded.',
  },
  {
    target: '[data-tour="testing-mode"]',
    placement: 'bottom',
    route: '/settings',
    title: 'Testing Mode',
    body: [
      'Enable this before any QA or testing activity. All interactions in '
      + 'a testing session are logged separately and excluded from the '
      + 'Team Activity analytical view by default.',
      'Testing Mode resets automatically on your next login — you never '
      + 'need to remember to turn it off.',
    ],
    relevantFor:
      'All team members — enable this before any QA or testing activity '
      + 'so your test clicks do not appear in the analytical activity log.',
  },
  {
    id: 'finish',
    target: 'body',
    placement: 'center',
    route: '/',
    title: "You're Ready",
    body: [
      'The platform is built. The data is loaded. Your project '
      + 'requirements are uploaded.',
      'Your first step: navigate to the Council screen and run an '
      + 'Academic Review. The verdict will tell you exactly where you '
      + 'stand against the rubric and what to prioritize before the '
      + 'May 27th submission. Every insight this platform gives you has '
      + 'been scored, refined, and evaluated before it reaches you. Use it.',
    ],
  },
]
