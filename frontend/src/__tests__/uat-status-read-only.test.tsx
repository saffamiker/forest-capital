/**
 * uat-status-read-only.test.tsx — UAT #119.
 *
 * Closes UAT issue #119: the Test Administration panel is now visible
 * to every team member (Bob, Molly, Michael) but the mutation surfaces
 * (Mark Resolved, Suggested Resolutions banner / row badges, Triage
 * Reports, Feedback status select + resolution note) remain
 * sysadmin-only — a team_member sees the data, never the controls.
 *
 * These tests assert the read-only render contract for the two roles
 * that actually open the section: sysadmin (sees data + actions) and
 * non-sysadmin team_member (sees data, no actions).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import { TestAdminSections } from '../components/TestRunnerSettings'
import { AuthContext } from '../App'

// Two failure rows — one Open, one Resolved — so we can assert both
// surfaces. The Open row carries a Mark Resolved button only for
// sysadmin; the Resolved row carries an expand chevron for both.
const FAILURE_FIXTURE = [
  {
    id: 101, user_email: 'thaob@queens.edu',
    script_id: 'bob_thao_v1', step_id: 'b_step_1',
    failure_description: 'Open failure for assertion',
    expected_result: 'works', actual_result: 'does not work',
    severity: 'major', screenshot_paths: [], low_quality: false,
    attested_at: '2026-05-20T10:00:00Z',
    resolved_at: null, resolved_by: null, resolution_note: null,
  },
  {
    id: 202, user_email: 'thaob@queens.edu',
    script_id: 'bob_thao_v1', step_id: 'b_step_2',
    failure_description: 'Resolved failure for assertion',
    expected_result: 'works', actual_result: 'fixed',
    severity: 'minor', screenshot_paths: [], low_quality: false,
    attested_at: '2026-05-19T10:00:00Z',
    resolved_at: '2026-05-22T10:00:00Z', resolved_by: 'ruurdsm@queens.edu',
    resolution_note: 'Shipped in PR #99',
    resolution_type: 'code_fix_deployed', fix_reference: '#99',
  },
]

const FEEDBACK_FIXTURE = [
  {
    id: 301, user_email: 'murdockm@queens.edu',
    script_id: null, step_id: null, source_route: '/dashboard',
    feedback_type: 'idea', title: 'Suggested tweak',
    description: 'Some long description of a tweak.',
    ai_category: 'enhancement', ai_severity: 'minor',
    ai_effort_estimate: 'small', ai_tags: ['ui'],
    ai_confidence: 0.92, ai_summary: 'AI summary line.',
    status: 'new', low_quality: false,
  },
]

function mockTestingApis() {
  vi.spyOn(axios, 'get').mockImplementation((url: string) => {
    if (url === '/api/v1/testing/failures') {
      return Promise.resolve({ data: { failures: FAILURE_FIXTURE } })
    }
    if (url === '/api/v1/testing/suggestions/by-failure') {
      return Promise.resolve({ data: { by_failure: {} } })
    }
    if (url === '/api/v1/testing/feedback') {
      return Promise.resolve({ data: { feedback: FEEDBACK_FIXTURE } })
    }
    if (url === '/api/v1/testing/triage') {
      return Promise.resolve({ data: { reports: [] } })
    }
    // May 24 2026 — Team Progress is the new default tab.
    if (url === '/api/v1/testing/team-progress') {
      return Promise.resolve({ data: {
        team_emails: [], members: {},
      } })
    }
    return Promise.resolve({ data: {} })
  })
}

function renderAdminPanel(permissions: string[]) {
  const authValue = {
    session: {
      token: 't', email: 'tester@queens.edu', permissions,
    },
    isVerifying: false, login: vi.fn(), logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <TestAdminSections />
      </MemoryRouter>
    </AuthContext.Provider>,
  )
}

// Team Progress is the default tab now (May 24 2026). Tests that
// assert against the Failure Reports content need to click into that
// tab first. The Failure Reports tab button is labeled exactly
// "Failure Reports".
async function openFailureReports() {
  const tab = await screen.findByRole('button',
    { name: /^Failure Reports$/i })
  tab.click()
}

describe('UAT #119 — Test Administration visibility split', () => {
  beforeEach(() => { mockTestingApis() })
  afterEach(() => { vi.restoreAllMocks() })

  describe('non-sysadmin team_member (Bob / Molly)', () => {
    const teamPerms = [
      'view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package',
      'access_test_panel', 'view_uat_status',
    ]

    it('sees the failure rows', async () => {
      renderAdminPanel(teamPerms)
      await openFailureReports()
      await waitFor(() => {
        expect(screen.getByText('Open failure for assertion')).toBeInTheDocument()
        expect(screen.getByText('Resolved failure for assertion')).toBeInTheDocument()
      })
    })

    it('does NOT see the Mark Resolved button on an open failure', async () => {
      renderAdminPanel(teamPerms)
      await openFailureReports()
      await screen.findByText('Open failure for assertion')
      // The Mark Resolved button is hidden for a non-sysadmin team_member.
      expect(screen.queryByRole('button', { name: /Mark Resolved/i }))
        .not.toBeInTheDocument()
    })

    it('does NOT see the Triage Reports admin block', async () => {
      renderAdminPanel(teamPerms)
      await openFailureReports()
      await screen.findByText('Open failure for assertion')
      expect(screen.queryByText('Triage Reports')).not.toBeInTheDocument()
    })

    it('sees the feedback row but NOT the row status select', async () => {
      renderAdminPanel(teamPerms)
      // Switch to the Feedback Backlog tab. It's labeled "Feedback".
      const feedbackTab = await screen.findByRole('button', { name: /Feedback/i })
      feedbackTab.click()
      await screen.findByText('Suggested tweak')
      // The FeedbackBacklog renders a top-of-list status FILTER select
      // (visible to everyone — it's a read query parameter, not a
      // mutation). The PER-ROW status select is the mutation surface
      // and is hidden for non-sysadmin. So exactly one combobox renders
      // for a non-sysadmin team_member.
      expect(screen.getAllByRole('combobox')).toHaveLength(1)
      // The read-only badge shows the current status verbatim.
      expect(screen.getByText('Status:')).toBeInTheDocument()
    })
  })

  describe('sysadmin (Michael)', () => {
    const sysadminPerms = [
      'view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package',
      'view_admin', 'manage_users',
      'access_test_panel', 'view_uat_status',
    ]

    it('sees the Mark Resolved button on an open failure', async () => {
      renderAdminPanel(sysadminPerms)
      await openFailureReports()
      await screen.findByText('Open failure for assertion')
      expect(screen.getByRole('button', { name: /Mark Resolved/i }))
        .toBeInTheDocument()
    })

    it('sees the Triage Reports admin block', async () => {
      renderAdminPanel(sysadminPerms)
      await openFailureReports()
      await screen.findByText('Open failure for assertion')
      expect(screen.getByText('Triage Reports')).toBeInTheDocument()
    })

    it('sees the editable per-row status select on a feedback row', async () => {
      renderAdminPanel(sysadminPerms)
      const feedbackTab = await screen.findByRole('button', { name: /Feedback/i })
      feedbackTab.click()
      await screen.findByText('Suggested tweak')
      // Two comboboxes for sysadmin: the top filter (read query) +
      // the per-row status select (the mutation surface). A
      // non-sysadmin only sees the first.
      expect(screen.getAllByRole('combobox')).toHaveLength(2)
    })
  })
})

// ── usePermissions wrapper for the new hook ───────────────────────────────────

describe('UAT #119 — useCanViewUatStatus', () => {
  it('returns true for a team_member carrying view_uat_status', async () => {
    const { useCanViewUatStatus } = await import('../hooks/usePermissions')
    function Probe() {
      return <div data-testid="v">{String(useCanViewUatStatus())}</div>
    }
    const teamPerms = [
      'view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package',
      'access_test_panel', 'view_uat_status',
    ]
    render(
      <AuthContext.Provider value={{
        session: { token: 't', email: 'x@queens.edu', permissions: teamPerms },
        isVerifying: false, login: vi.fn(), logout: vi.fn(),
      }}>
        <Probe />
      </AuthContext.Provider>,
    )
    expect(screen.getByTestId('v').textContent).toBe('true')
  })

  it('returns false for a viewer (no view_uat_status)', async () => {
    const { useCanViewUatStatus } = await import('../hooks/usePermissions')
    function Probe() {
      return <div data-testid="v">{String(useCanViewUatStatus())}</div>
    }
    render(
      <AuthContext.Provider value={{
        session: {
          token: 't', email: 'x@queens.edu',
          permissions: ['view_analytics', 'ask_council'],
        },
        isVerifying: false, login: vi.fn(), logout: vi.fn(),
      }}>
        <Probe />
      </AuthContext.Provider>,
    )
    expect(screen.getByTestId('v').textContent).toBe('false')
  })
})
