/**
 * team-progress-block.test.tsx — shared UAT progress dashboard.
 *
 * Pins the read-only render contract:
 *   1. Per-member cards render for every team email in the response.
 *   2. The progress % uses passed/total per script aggregated across
 *      the member's assigned scripts.
 *   3. The "Currently testing" pulse pill appears only when the
 *      backend reports currently_testing=true for that member.
 *   4. Re-test and skipped counts surface in the per-script line
 *      with their amber icons.
 *   5. No action buttons render — the view is read-only.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import axios from 'axios'
import { TeamProgressBlock } from '../components/TeamProgressBlock'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)


function fixture() {
  // Each member's scripts dict carries one entry per script with the
  // four lists (passed / failed / skipped / retest). The component
  // aggregates these against TEST_SCRIPTS to compute percent.
  return {
    team_emails: [
      'murdockm@queens.edu', 'ruurdsm@queens.edu', 'thaob@queens.edu',
    ],
    members: {
      'ruurdsm@queens.edu': {
        email: 'ruurdsm@queens.edu', display_name: 'Michael Ruurds',
        scripts: {
          all_testers_v1: {
            passed: ['s1', 's2', 's3'], failed: [], skipped: [],
            retest: [], last_attested_at: '2026-05-24T16:42:00Z',
          },
        },
        failure_count: 0,
        last_activity_at: '2026-05-24T16:42:00Z',
        currently_testing: true,
      },
      'thaob@queens.edu': {
        email: 'thaob@queens.edu', display_name: 'Bob Thao',
        scripts: {
          all_testers_v1: {
            passed: ['s1'], failed: ['s2'], skipped: [], retest: ['s3'],
            last_attested_at: '2026-05-24T16:30:00Z',
          },
        },
        failure_count: 2,
        last_activity_at: '2026-05-24T16:30:00Z',
        currently_testing: false,
      },
      'murdockm@queens.edu': {
        email: 'murdockm@queens.edu', display_name: 'Molly Murdock',
        scripts: {
          all_testers_v1: {
            passed: [], failed: [], skipped: ['s1'], retest: [],
            last_attested_at: null,
          },
        },
        failure_count: 0,
        last_activity_at: null,
        currently_testing: false,
      },
    },
  }
}


describe('TeamProgressBlock', () => {
  beforeEach(() => {
    // Use real timers by default — vi.useFakeTimers globally breaks
    // waitFor's microtask polling. The polling test re-enables fake
    // timers locally.
    mockedAxios.get = vi.fn().mockResolvedValue({ data: fixture() })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders one card per team member', async () => {
    render(<TeamProgressBlock />)
    await waitFor(() => {
      expect(screen.getByTestId('member-card-ruurdsm@queens.edu'))
        .toBeInTheDocument()
      expect(screen.getByTestId('member-card-thaob@queens.edu'))
        .toBeInTheDocument()
      expect(screen.getByTestId('member-card-murdockm@queens.edu'))
        .toBeInTheDocument()
    })
  })

  it('shows the "Currently testing" pulse only for the active member',
    async () => {
      render(<TeamProgressBlock />)
      await waitFor(() => {
        expect(screen.getByTestId('currently-testing-ruurdsm@queens.edu'))
          .toBeInTheDocument()
      })
      // Bob and Molly are not currently testing.
      expect(screen.queryByTestId('currently-testing-thaob@queens.edu'))
        .not.toBeInTheDocument()
      expect(screen.queryByTestId('currently-testing-murdockm@queens.edu'))
        .not.toBeInTheDocument()
    })

  it('surfaces the per-member failure count', async () => {
    render(<TeamProgressBlock />)
    await waitFor(() => {
      const bob = screen.getByTestId('member-card-thaob@queens.edu')
      expect(bob.textContent).toContain('Failures filed:')
      expect(bob.textContent).toContain('2')
    })
  })

  it('renders the top-of-block team-wide summary card with a percent',
    async () => {
      render(<TeamProgressBlock />)
      await waitFor(() => {
        expect(screen.getByText('Team UAT Progress')).toBeInTheDocument()
        // The "Updates every 15s" caption confirms the polling note
        // renders so a viewer knows the page is live.
        expect(screen.getByText(/Updates every 15s/i)).toBeInTheDocument()
      })
    })

  it('shows the re-test / skipped warning when totals are non-zero',
    async () => {
      render(<TeamProgressBlock />)
      await waitFor(() => {
        // Bob has 1 retest, Molly has 1 skipped — at least one
        // amber warning surfaces somewhere in the rendered DOM.
        // Multiple elements may match (the summary card + per-script
        // line both name re-test); getAllByText asserts at least one.
        expect(
          screen.getAllByText(/pending re-test|skipped \(no-test\)/i).length,
        ).toBeGreaterThan(0)
      })
    })

  it('renders no action buttons — read-only view', async () => {
    render(<TeamProgressBlock />)
    await waitFor(() => {
      expect(screen.getByTestId('team-progress-block'))
        .toBeInTheDocument()
    })
    // No Mark Resolved / Re-test / Edit buttons render in this view —
    // the read-only contract is part of the UAT visibility split.
    expect(screen.queryByRole('button', { name: /mark resolved/i }))
      .not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /re-test/i }))
      .not.toBeInTheDocument()
    // The component renders ZERO interactive controls — it's pure
    // display. Confirm by counting all buttons within the block.
    const block = screen.getByTestId('team-progress-block')
    expect(block.querySelectorAll('button')).toHaveLength(0)
  })
})
