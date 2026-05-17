/**
 * tourSteps.ts
 *
 * The site-tour step definitions. One tour serves three audiences —
 * Forest Capital (a professional investment audience), the McColl
 * School faculty (rubric compliance), and the project team — so every
 * step's second paragraph connects the feature to the grade, and
 * relevant steps name the team member they matter most to.
 *
 * Commit 1 ships the two centred bookend steps; commit 2 fills the full
 * fifteen and adds the data-tour target attributes the steps reference.
 */

export interface TourStep {
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
    target: 'body',
    placement: 'center',
    route: '/',
    title: "You're Ready",
    body: [
      'The platform is built. The data is loaded. Your project '
      + 'requirements are uploaded.',
      'Your first step: navigate to the Council screen and run an '
      + 'Academic Review. The verdict will tell you exactly where you '
      + 'stand against the rubric and what to prioritise before the '
      + 'May 27th submission. Every insight this platform gives you has '
      + 'been scored, refined, and evaluated before it reaches you. Use it.',
    ],
  },
]
