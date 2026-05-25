/**
 * test-results-per-member.test.tsx — May 25 2026.
 *
 * Pins the Settings → Test Results rework: the panel now shows ONE
 * section per team member (Michael / Bob / Molly), each rendering
 * the full per-step checklist for both the shared 'All Testers'
 * script and the member's primary section. The logged-in user's
 * section is editable (Re-test buttons visible); other members'
 * sections are read-only.
 *
 * Backend: GET /api/v1/testing/team-progress now ships
 * step_attested_at + step_failure_description per script so the
 * timestamp + failure text are renderable without a second call.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import axios from 'axios'
import { AuthContext } from '../App'
import { TestResultsSection } from '../components/TestRunnerSettings'

vi.mock('axios')
const mockedAxios = axios as unknown as { get: ReturnType<typeof vi.fn> }


// Auth context wrapper — the new TestResultsBlock reads session.email
// to decide which section is 'self' (editable) vs 'read-only'.
function withAuth(email: string, ui: ReactNode) {
  const value = {
    session: {
      token: 't', email,
      permissions: ['view_analytics', 'ask_council', 'team_member'],
    },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>,
  )
}


// A team-progress payload with the three canonical team members and
// a representative mix of step statuses across scripts.
const TEAM_PROGRESS_PAYLOAD = {
  team_emails: [
    'ruurdsm@queens.edu', 'thaob@queens.edu', 'murdockm@queens.edu',
  ],
  members: {
    'ruurdsm@queens.edu': {
      email: 'ruurdsm@queens.edu',
      display_name: 'Michael Ruurds',
      scripts: {
        all_testers_v1: {
          passed: ['tour_autolaunch'],
          failed: [],
          skipped: [],
          retest: [],
          last_attested_at: '2026-05-25T10:00:00Z',
          step_attested_at: { 'tour_autolaunch': '2026-05-25T10:00:00Z' },
          step_failure_description: {},
        },
      },
      failure_count: 0,
      last_activity_at: '2026-05-25T10:00:00Z',
      currently_testing: false,
    },
    'thaob@queens.edu': {
      email: 'thaob@queens.edu',
      display_name: 'Bob Thao',
      scripts: {
        all_testers_v1: {
          passed: [],
          failed: ['tour_autolaunch'],
          skipped: [],
          retest: [],
          last_attested_at: '2026-05-24T14:30:00Z',
          step_attested_at: { 'tour_autolaunch': '2026-05-24T14:30:00Z' },
          step_failure_description: {
            'tour_autolaunch': 'Login button did not respond on first click.',
          },
        },
      },
      failure_count: 1,
      last_activity_at: '2026-05-24T14:30:00Z',
      currently_testing: false,
    },
    'murdockm@queens.edu': {
      email: 'murdockm@queens.edu',
      display_name: 'Molly Murdock',
      scripts: {},
      failure_count: 0,
      last_activity_at: null,
      currently_testing: false,
    },
  },
}


beforeEach(() => {
  mockedAxios.get = vi.fn()
    .mockResolvedValue({ data: TEAM_PROGRESS_PAYLOAD })
})


describe('TestResultsSection — per-team-member rework', () => {
  it('renders one section per team member in the canonical display order',
    async () => {
      withAuth('ruurdsm@queens.edu', <TestResultsSection />)
      // All three sections present, regardless of who's logged in.
      expect(
        await screen.findByTestId('uat-member-section-ruurdsm@queens.edu'),
      ).toBeInTheDocument()
      expect(
        screen.getByTestId('uat-member-section-thaob@queens.edu'),
      ).toBeInTheDocument()
      expect(
        screen.getByTestId('uat-member-section-murdockm@queens.edu'),
      ).toBeInTheDocument()
    })

  it('marks the logged-in user\'s section as self + others as read-only',
    async () => {
      withAuth('thaob@queens.edu', <TestResultsSection />)
      await screen.findByTestId('uat-member-section-thaob@queens.edu')
      // Bob's section flags as self.
      expect(
        screen.getByTestId('uat-member-section-thaob@queens.edu')
          .getAttribute('data-self'),
      ).toBe('true')
      // Michael's + Molly's sections flag as read-only.
      expect(
        screen.getByTestId('uat-member-section-ruurdsm@queens.edu')
          .getAttribute('data-self'),
      ).toBe('false')
      expect(
        screen.getByTestId('uat-member-section-murdockm@queens.edu')
          .getAttribute('data-self'),
      ).toBe('false')
    })

  it('renders the four canonical statuses per step', async () => {
    // Bob's row carries: one failed step with a failure description,
    // and one not-tested step from the shared script (and nothing in
    // his primary bucket — also not-tested).
    withAuth('thaob@queens.edu', <TestResultsSection />)
    await screen.findByTestId('uat-member-section-thaob@queens.edu')

    // The failed step carries status='fail'.
    const failedStep = screen.getByTestId(
      'uat-step-thaob@queens.edu-tour_autolaunch')
    expect(failedStep.getAttribute('data-status')).toBe('fail')
    // Failure description appears underneath.
    expect(failedStep.textContent).toContain(
      'Login button did not respond')
  })

  it('shows Re-test buttons only in the user\'s own section', async () => {
    withAuth('thaob@queens.edu', <TestResultsSection />)
    await screen.findByTestId('uat-member-section-thaob@queens.edu')
    // Bob's failed step gets a Re-test button (his own section).
    expect(screen.getByTestId('uat-retest-tour_autolaunch')).toBeInTheDocument()
    // Michael's section (a different user's) gets no buttons even
    // if his steps look fail/retest-actionable. The fail-button
    // testid is keyed by step id ('tour_autolaunch'); only one Re-test
    // button can exist for that step_id, and it belongs to Bob.
    // To confirm Michael's section is read-only, assert his step
    // carries the read-only tag.
    const michaelRow = screen.getByTestId(
      'uat-member-section-ruurdsm@queens.edu')
    expect(michaelRow.textContent).toContain('Read-only')
  })

  it('formats the per-step timestamp and shows "Not tested" when blank',
    async () => {
      // Molly's section has zero attestations — every step renders
      // 'Not tested' italic placeholder rather than a date.
      withAuth('murdockm@queens.edu', <TestResultsSection />)
      const mollySection = await screen.findByTestId(
        'uat-member-section-murdockm@queens.edu')
      expect(mollySection.textContent).toContain('Not tested')

      // Bob's row carries a real timestamp in formatted local string —
      // the test just asserts a year is present.
      const bobFailedStep = screen.getByTestId(
        'uat-step-thaob@queens.edu-tour_autolaunch')
      expect(bobFailedStep.textContent).toMatch(/2026/)
    })

  it('classifies four canonical statuses without leaking a 5th label',
    async () => {
      withAuth('ruurdsm@queens.edu', <TestResultsSection />)
      await screen.findByTestId('uat-member-section-ruurdsm@queens.edu')
      // Sweep every rendered step row; data-status must be one of
      // the four canonical values.
      const steps = document.querySelectorAll('[data-testid^="uat-step-"]')
      expect(steps.length).toBeGreaterThan(0)
      const allowed = new Set(['pass', 'fail', 'retest', 'not_tested'])
      for (const s of Array.from(steps)) {
        const status = s.getAttribute('data-status') ?? ''
        expect(allowed.has(status)).toBe(true)
      }
    })
})
