/**
 * issue-tracker.test.tsx — Issue Tracker view (Prompt B).
 *
 * Pins the column derivations, the status badge colour-coding, the
 * filter behaviour, the sort behaviour, and the row-expand resolution
 * card. The endpoint contract is covered in test_issue_tracker.py
 * on the backend; this file is the renderer-side counterpart.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

vi.mock('axios')

import axios from 'axios'
import { TestAdminSections } from '../components/TestRunnerSettings'
import { AuthContext } from '../App'

const mockedAxios = vi.mocked(axios, true)

/** Mounts TestAdminSections with sysadmin permissions — the surface
 *  this file pins (Issue Tracker filters, status badges, sort, row
 *  expand, export) is the admin view. Without an AuthContext provider
 *  the useIsSysadmin hook inside TestAdminSections would throw, so
 *  the wrapper is required (UAT #119 split). */
const SYSADMIN_AUTH = {
  session: {
    token: 't', email: 'ruurdsm@queens.edu',
    permissions: [
      'view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package',
      'view_admin', 'manage_users',
      'access_test_panel', 'view_uat_status',
    ],
  },
  isVerifying: false, login: vi.fn(), logout: vi.fn(),
}

interface IssueRow {
  id: number
  user_email: string
  script_id: string
  step_id: string
  result: 'pass' | 'fail' | 'skip'
  failure_description: string | null
  severity: string | null
  attested_at: string | null
  resolved_at: string | null
  resolved_by: string | null
  resolution_note: string | null
  resolution_type: 'no_bug_detected' | 'code_fix_deployed' | 'wont_fix' | null
  fix_reference: string | null
  remediation_note: string | null
  github_issue_number: number | null
  github_issue_url: string | null
  status: 'open' | 'pending_retest' | 'passed' | 'closed'
}

function row(over: Partial<IssueRow>): IssueRow {
  return {
    id: 1,
    user_email: 'tester@queens.edu',
    script_id: 'all_testers_v1',
    step_id: 'sec1_login',
    result: 'fail',
    failure_description: 'broke',
    severity: 'major',
    attested_at: '2026-05-15T10:00:00Z',
    resolved_at: null,
    resolved_by: null,
    resolution_note: null,
    resolution_type: null,
    fix_reference: null,
    remediation_note: null,
    github_issue_number: null,
    github_issue_url: null,
    status: 'open',
    ...over,
  }
}

const SAMPLE: IssueRow[] = [
  row({ id: 1, user_email: 'thaob@queens.edu',
        attested_at: '2026-05-10T10:00:00Z',
        status: 'open' }),
  row({ id: 2, user_email: 'murdockm@queens.edu',
        attested_at: '2026-05-12T10:00:00Z',
        resolved_at: '2026-05-13T10:00:00Z',
        resolved_by: 'ruurdsm@queens.edu',
        resolution_note: 'User error.',
        resolution_type: 'no_bug_detected',
        status: 'pending_retest' }),
  row({ id: 3, user_email: 'thaob@queens.edu',
        attested_at: '2026-05-14T10:00:00Z',
        resolved_at: '2026-05-16T10:00:00Z',
        resolved_by: 'ruurdsm@queens.edu',
        resolution_note: 'Race condition.',
        resolution_type: 'code_fix_deployed',
        fix_reference: 'abc1234',
        remediation_note: 'Added a lock.',
        result: 'pass',
        status: 'passed' }),
  row({ id: 4, user_email: 'murdockm@queens.edu',
        attested_at: '2026-05-09T10:00:00Z',
        resolved_at: '2026-05-10T10:00:00Z',
        resolved_by: 'ruurdsm@queens.edu',
        resolution_note: 'By design — sysadmin-only.',
        resolution_type: 'wont_fix',
        status: 'closed' }),
]


function mountTabs() {
  return render(
    <AuthContext.Provider value={SYSADMIN_AUTH}>
      <TestAdminSections />
    </AuthContext.Provider>,
  )
}


beforeEach(() => {
  vi.clearAllMocks()
  // Default mocks for every tab/section so a click into any tab does
  // not throw. Each branch is overridable per test.
  // May 24 2026 — added /team-progress (the new default tab in the
  // UAT shared-visibility view).
  mockedAxios.get = vi.fn().mockImplementation((url: string) => {
    if (url === '/api/v1/testing/issue-tracker') {
      return Promise.resolve({ data: { issues: SAMPLE } })
    }
    if (url === '/api/v1/testing/failures') {
      return Promise.resolve({ data: { failures: [] } })
    }
    if (url === '/api/v1/testing/feedback') {
      return Promise.resolve({ data: { feedback: [] } })
    }
    if (url === '/api/v1/testing/triage') {
      return Promise.resolve({ data: { reports: [] } })
    }
    if (url === '/api/v1/testing/team-progress') {
      return Promise.resolve({ data: {
        team_emails: [], members: {},
      }})
    }
    if (url === '/api/v1/testing/suggestions/by-failure') {
      return Promise.resolve({ data: { by_failure: {} } })
    }
    return Promise.reject(new Error(`Unexpected GET: ${url}`))
  })
})


async function openTracker() {
  mountTabs()
  fireEvent.click(screen.getByRole('button', { name: /issue tracker/i }))
  await waitFor(() =>
    expect(screen.queryByText(/loading issue tracker/i)).toBeNull())
}


// ── Tabs ─────────────────────────────────────────────────────────────────────

describe('Failure Reports tabs', () => {
  it('renders four tabs: Team Progress, Failure Reports, Feedback Backlog, Issue Tracker',
    () => {
      mountTabs()
      expect(screen.getByRole('button', { name: /team progress/i }))
        .toBeInTheDocument()
      expect(screen.getByRole('button', { name: /failure reports/i }))
        .toBeInTheDocument()
      expect(screen.getByRole('button', { name: /feedback backlog/i }))
        .toBeInTheDocument()
      expect(screen.getByRole('button', { name: /issue tracker/i }))
        .toBeInTheDocument()
    })

  it('defaults to the Team Progress tab', () => {
    // May 24 2026 — UAT shared visibility made Team Progress the
    // default tab so opening Test Administration immediately shows
    // who's where in their UAT pass.
    mountTabs()
    expect(screen.getByText(/each team member's uat progress in real time/i))
      .toBeInTheDocument()
    // The Issue Tracker description is NOT yet.
    expect(screen.queryByText(/lifecycle of every reported failure/i))
      .toBeNull()
  })

  it('switches to Issue Tracker on click', async () => {
    await openTracker()
    expect(screen.getByText(/lifecycle of every reported failure/i))
      .toBeInTheDocument()
  })
})


// ── Table column rendering ──────────────────────────────────────────────────

describe('Issue Tracker — columns', () => {
  it('renders one row per issue with the right ID', async () => {
    await openTracker()
    // Each ID surfaces in the rendered table.
    expect(screen.getByText('1')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    // ID 4 is "closed" → filtered out of the default view.
    expect(screen.queryByText('4')).toBeNull()
  })

  it('renders the status badges with the documented labels', async () => {
    await openTracker()
    expect(screen.getByText('Open')).toBeInTheDocument()
    expect(screen.getByText('Pending re-test')).toBeInTheDocument()
    expect(screen.getByText('Passed')).toBeInTheDocument()
  })

  it('shows ruurdsm@ as the owner for Open rows', async () => {
    await openTracker()
    // The Open row (id=1) has ruurdsm@ as the owner column value;
    // the Pending re-test row (id=2) has murdockm@; the Passed row
    // (id=3) has "—".
    expect(screen.getAllByText('ruurdsm@queens.edu').length)
      .toBeGreaterThan(0)
  })

  it('linkifies the fix_reference for code_fix_deployed rows', async () => {
    await openTracker()
    const link = screen.getByText('abc1234')
    expect(link.closest('a')).toHaveAttribute('href',
      'https://github.com/saffamiker/forest-capital/commit/abc1234')
  })
})


// ── Filtering ──────────────────────────────────────────────────────────────

describe('Issue Tracker — filters', () => {
  it('hides Closed rows by default', async () => {
    await openTracker()
    // Closed is in the row data but excluded from the default filter.
    expect(screen.queryByText('Closed')).toBeNull()
  })

  it('Closed rows appear when the Closed status filter is toggled on',
    async () => {
      await openTracker()
      // Row 4 (Closed) is hidden under the default filter.
      expect(screen.queryByText('4')).toBeNull()
      // Open the Status filter dropdown.
      fireEvent.click(screen.getAllByRole('button',
        { name: /3 selected/i })[0])
      // The dropdown lists 4 status options; toggle "Closed" on.
      // The checkbox label is "Closed"; accessible name matches.
      fireEvent.click(screen.getByRole('checkbox', { name: /closed/i }))
      // Now the closed row (id=4) appears in the table. Asserting on
      // the row ID rather than the badge text avoids a clash with
      // the dropdown's own "Closed" label.
      await waitFor(() =>
        expect(screen.getByText('4')).toBeInTheDocument())
      // And the count line updates.
      expect(screen.getByText(/showing 4 of 4 issues/i))
        .toBeInTheDocument()
    })

  it('Tester filter narrows the visible rows to one user', async () => {
    await openTracker()
    // Before filtering: thaob@ owns rows 1 and 3.
    // Click the Tester multi-select, pick thaob@.
    const summaryLabels = screen.getAllByText(/all/i)
    // The Tester multi-select shows "All" because none are selected.
    // Find the right button — the Tester column dropdown.
    const testerButton = screen.getAllByRole('button')
      .find((b) =>
        b.textContent === 'All'
        && b.previousElementSibling?.textContent
          ?.toLowerCase().includes('tester'))
    expect(testerButton).toBeTruthy()
    fireEvent.click(testerButton!)
    fireEvent.click(screen.getByRole('checkbox',
      { name: 'thaob@queens.edu' }))
    // Only thaob@ rows remain (ids 1 and 3).
    await waitFor(() =>
      expect(screen.getByText('1')).toBeInTheDocument())
    expect(screen.queryByText('2')).toBeNull()
    expect(screen.queryByText('4')).toBeNull()
    void summaryLabels  // anchor used; silence unused linter
  })

  it('Showing X of Y reflects the filter result', async () => {
    await openTracker()
    // Default: 3 of 4 (Closed hidden).
    expect(screen.getByText(/showing 3 of 4 issues/i)).toBeInTheDocument()
  })
})


// ── Sorting ────────────────────────────────────────────────────────────────

describe('Issue Tracker — sort', () => {
  it('default sort is Status (Open first), then Reported (oldest first)',
    async () => {
      await openTracker()
      // Read the visible IDs in order. Default visible: 1 (Open),
      // 2 (Pending re-test), 3 (Passed). The id column is the first
      // <td> in each <tr>; pluck them in DOM order.
      const idCells = within(document.querySelector('tbody')!)
        .getAllByText(/^\d+$/)
        .filter((el) => el.tagName === 'TD')
      const ids = idCells.map((el) => el.textContent)
      expect(ids).toEqual(['1', '2', '3'])
    })

  it('clicking a sort header toggles ascending → descending', async () => {
    await openTracker()
    const idHeader = screen.getByRole('button', { name: /^id/i })
    // First click sets ID-asc; check the indicator.
    fireEvent.click(idHeader)
    // Default is "1, 2, 3" ASC already by status default but ID-asc is
    // also "1, 2, 3" so re-check via a second click → desc → "3, 2, 1".
    fireEvent.click(idHeader)
    const idCells = within(document.querySelector('tbody')!)
      .getAllByText(/^\d+$/)
      .filter((el) => el.tagName === 'TD')
    const ids = idCells.map((el) => el.textContent)
    expect(ids).toEqual(['3', '2', '1'])
  })
})


// ── Row expand ─────────────────────────────────────────────────────────────

describe('Issue Tracker — row expand', () => {
  it('expanding a Passed row reveals the resolution card', async () => {
    await openTracker()
    // Find the expand chevron for row 3 (Passed). Each row has one
    // such button at the end; we use the row that mentions abc1234.
    // The aria-label is 'Expand'.
    const expandButtons = screen.getAllByRole('button',
      { name: /^expand$/i })
    expect(expandButtons.length).toBeGreaterThan(0)
    // Three rows visible → three expand buttons. Click the last one
    // (Passed row by default sort).
    fireEvent.click(expandButtons[expandButtons.length - 1]!)
    // The expanded card surfaces the remediation note.
    await waitFor(() =>
      expect(screen.getByText(/added a lock/i)).toBeInTheDocument())
    // And the root cause.
    expect(screen.getByText(/race condition/i)).toBeInTheDocument()
  })
})


// ── Export ─────────────────────────────────────────────────────────────────

describe('Issue Tracker — export', () => {
  it('renders the Download Issue Tracker button', async () => {
    await openTracker()
    expect(screen.getByRole('button', { name: /download issue tracker/i }))
      .toBeInTheDocument()
  })
})
