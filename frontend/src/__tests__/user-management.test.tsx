/**
 * user-management.test.tsx
 *
 * Tests for the database-managed access-control frontend:
 *   1. the permission hooks (useHasPermission / useIsTeamMember /
 *      useIsSysadmin) read the session's authoritative permissions array
 *   2. the permissions constants — ASSIGNABLE_ROLES omits sysadmin,
 *      matchesPreset detects a Custom permission set
 *   3. UserManagementPanel renders the user table and gates Add-User on
 *      a valid email; the role-preset dropdown never offers sysadmin
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, renderHook, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import axios from 'axios'

import { AuthContext } from '../App'
import {
  useHasPermission, useIsTeamMember, useIsSysadmin,
} from '../hooks/usePermissions'
import {
  PERMISSIONS, ROLE_PRESETS, ASSIGNABLE_ROLES, matchesPreset,
} from '../constants/permissions'
import UserManagementPanel from '../components/UserManagementPanel'

// ── Permission hooks ──────────────────────────────────────────────────────────

function authWith(permissions: string[] | null) {
  return ({ children }: { children: ReactNode }) => (
    <AuthContext.Provider value={{
      session: permissions
        ? { token: 't', email: 'u@queens.edu', permissions }
        : null,
      isVerifying: false,
      login: vi.fn(),
      logout: vi.fn(),
    }}>{children}</AuthContext.Provider>
  )
}

describe('permission hooks', () => {
  it('useHasPermission is true only when the permission is present', () => {
    const { result } = renderHook(() => useHasPermission('manage_users'), {
      wrapper: authWith(['view_analytics', 'manage_users']),
    })
    expect(result.current).toBe(true)
  })

  it('useHasPermission is false when the permission is absent', () => {
    const { result } = renderHook(() => useHasPermission('manage_users'), {
      wrapper: authWith(['view_analytics', 'ask_council']),
    })
    expect(result.current).toBe(false)
  })

  it('useHasPermission is false before /api/auth/me has populated the session', () => {
    // permissions undefined → the hook reads false (a brief, safe window).
    const { result } = renderHook(() => useHasPermission('team_member'), {
      wrapper: ({ children }) => (
        <AuthContext.Provider value={{
          session: { token: 't', email: 'u@queens.edu' },
          isVerifying: false, login: vi.fn(), logout: vi.fn(),
        }}>{children}</AuthContext.Provider>
      ),
    })
    expect(result.current).toBe(false)
  })

  it('useIsTeamMember tracks the team_member permission', () => {
    const team = renderHook(() => useIsTeamMember(), {
      wrapper: authWith(ROLE_PRESETS.team_member),
    })
    expect(team.result.current).toBe(true)
    const viewer = renderHook(() => useIsTeamMember(), {
      wrapper: authWith(ROLE_PRESETS.viewer),
    })
    expect(viewer.result.current).toBe(false)
  })

  it('useIsSysadmin is true only for a manage_users holder', () => {
    const admin = renderHook(() => useIsSysadmin(), {
      wrapper: authWith(ROLE_PRESETS.sysadmin),
    })
    expect(admin.result.current).toBe(true)
    // A team member is NOT a sysadmin.
    const team = renderHook(() => useIsSysadmin(), {
      wrapper: authWith(ROLE_PRESETS.team_member),
    })
    expect(team.result.current).toBe(false)
  })
})

// ── Permissions constants ─────────────────────────────────────────────────────

describe('permissions constants', () => {
  it('ASSIGNABLE_ROLES offers viewer and team_member but not sysadmin', () => {
    const values = ASSIGNABLE_ROLES.map((r) => r.value)
    expect(values).toContain('viewer')
    expect(values).toContain('team_member')
    expect(values).not.toContain('sysadmin')
  })

  it('manage_users is marked sysadmin-only', () => {
    const manage = PERMISSIONS.find((p) => p.key === 'manage_users')
    expect(manage?.sysadminOnly).toBe(true)
  })

  it('matchesPreset is true for a permission set equal to its role preset', () => {
    expect(matchesPreset('viewer', [...ROLE_PRESETS.viewer])).toBe(true)
    // Order does not matter.
    expect(matchesPreset('team_member',
      [...ROLE_PRESETS.team_member].reverse())).toBe(true)
  })

  it('matchesPreset is false for a diverged (Custom) permission set', () => {
    expect(matchesPreset('viewer',
      [...ROLE_PRESETS.viewer, 'generate_documents'])).toBe(false)
  })
})

// ── UserManagementPanel ───────────────────────────────────────────────────────

vi.mock('axios')
const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
}

const USER_FIXTURE = {
  id: 1,
  email: 'molly@queens.edu',
  display_name: 'Molly',
  role: 'team_member',
  permissions: [...ROLE_PRESETS.team_member],
  is_active: true,
  created_at: '2026-05-01T00:00:00Z',
  created_by: 'ruurdsm@queens.edu',
  last_login_at: '2026-05-16T12:00:00Z',
  notes: null,
  activity_count: 42,
  ai_cost_usd: 0.0312,
}

// Default activity-breakdown payload — empty users list keeps the
// section quiet for the existing user-table tests. Tests that exercise
// ActivityBreakdownPanel itself override this in their own beforeEach.
const EMPTY_BREAKDOWN = {
  users: [], period_days: 30,
  generated_at: '2026-05-19T00:00:00+00:00',
}

describe('UserManagementPanel', () => {
  beforeEach(() => {
    // URL-routed get mock — the panel makes two endpoint calls (the
    // user list AND the activity breakdown) and each needs its own
    // payload shape. A single mockResolvedValue would feed the user
    // list to the breakdown panel and crash it.
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/activity-breakdown')) {
        return Promise.resolve({ data: EMPTY_BREAKDOWN })
      }
      return Promise.resolve({ data: { users: [USER_FIXTURE] } })
    })
    mockedAxios.post = vi.fn()
    mockedAxios.patch = vi.fn()
    mockedAxios.delete = vi.fn()
    mockedAxios.isAxiosError = ((() => false) as unknown) as typeof axios.isAxiosError
  })

  it('renders the user table from GET /api/v1/admin/users', async () => {
    render(<UserManagementPanel />)
    await waitFor(() =>
      expect(screen.getByText('Molly')).toBeInTheDocument())
    expect(screen.getByText('molly@queens.edu')).toBeInTheDocument()
    // Activity count rendered.
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('opens the Add User modal and gates the action on a valid email', async () => {
    const user = userEvent.setup()
    render(<UserManagementPanel />)
    await waitFor(() => expect(screen.getByText('Molly')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /add user/i }))
    const dialog = screen.getByRole('dialog', { name: /add user/i })

    // The submit button is disabled while the email is empty/invalid.
    const submit = within(dialog).getByRole('button', { name: 'Add User' })
    expect(submit).toBeDisabled()

    const emailInput = within(dialog).getAllByRole('textbox')[0]
    await user.type(emailInput, 'not-an-email')
    expect(submit).toBeDisabled()

    await user.clear(emailInput)
    await user.type(emailInput, 'newuser@queens.edu')
    expect(submit).toBeEnabled()
  })

  it('never offers sysadmin as an assignable role preset', async () => {
    const user = userEvent.setup()
    render(<UserManagementPanel />)
    await waitFor(() => expect(screen.getByText('Molly')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /add user/i }))
    const dialog = screen.getByRole('dialog', { name: /add user/i })
    const roleSelect = within(dialog).getByRole('combobox')
    const options = within(roleSelect).getAllByRole('option')
                                      .map((o) => o.getAttribute('value'))
    expect(options).toEqual(['viewer', 'team_member'])
  })

  it('confirms the welcome email was sent after adding a user', async () => {
    const user = userEvent.setup()
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: { id: 2, welcome_email_sent: true },
    })
    render(<UserManagementPanel />)
    await waitFor(() => expect(screen.getByText('Molly')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /add user/i }))
    const dialog = screen.getByRole('dialog', { name: /add user/i })
    await user.type(within(dialog).getAllByRole('textbox')[0],
                    'newuser@queens.edu')
    await user.click(within(dialog).getByRole('button', { name: 'Add User' }))

    expect(await screen.findByText(
      /welcome email sent to newuser@queens.edu/i)).toBeInTheDocument()
  })

  it('warns when the welcome email could not be sent', async () => {
    const user = userEvent.setup()
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: { id: 2, welcome_email_sent: false },
    })
    render(<UserManagementPanel />)
    await waitFor(() => expect(screen.getByText('Molly')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /add user/i }))
    const dialog = screen.getByRole('dialog', { name: /add user/i })
    await user.type(within(dialog).getAllByRole('textbox')[0],
                    'newuser@queens.edu')
    await user.click(within(dialog).getByRole('button', { name: 'Add User' }))

    expect(await screen.findByText(
      /Welcome email could not be sent/i)).toBeInTheDocument()
  })
})


// ── ActivityBreakdownPanel ────────────────────────────────────────────────────

import ActivityBreakdownPanel from '../components/ActivityBreakdownPanel'

const BREAKDOWN_FIXTURE = {
  users: [
    {
      email: 'thaob@queens.edu',
      display_name: 'Bob Thao',
      role: 'team_member',
      breakdown: {
        council: 12,
        academic_review: 4,
        writing_assistant: 6,
        explain: 8,
        qa: 2,
      },
      session_breakdown: { analytical: 280, testing: 45 },
      total_interactions: 32,
      total_cost_usd: 0.6886,
      first_seen: '2026-05-01T10:00:00+00:00',
      last_seen:  '2026-05-19T17:00:00+00:00',
    },
    {
      email: 'murdockm@queens.edu',
      display_name: 'Molly Murdock',
      role: 'team_member',
      breakdown: {},
      session_breakdown: {},
      total_interactions: 0,
      total_cost_usd: 0,
      first_seen: null,
      last_seen:  null,
    },
    {
      email: 'ruurdsm@queens.edu',
      display_name: 'Michael',
      role: 'sysadmin',
      breakdown: { council: 5 },
      session_breakdown: { analytical: 120, testing: 0 },
      total_interactions: 5,
      total_cost_usd: 0,
      first_seen: '2026-05-18T10:00:00+00:00',
      last_seen:  '2026-05-19T11:00:00+00:00',
    },
  ],
  period_days: 30,
  generated_at: '2026-05-19T12:00:00+00:00',
}

describe('ActivityBreakdownPanel', () => {
  beforeEach(() => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/activity-breakdown')) {
        return Promise.resolve({ data: BREAKDOWN_FIXTURE })
      }
      return Promise.resolve({ data: { users: [] } })
    })
    mockedAxios.isAxiosError = ((() => false) as unknown) as typeof axios.isAxiosError
  })

  it('renders the section header and subtitle', async () => {
    render(<ActivityBreakdownPanel />)
    expect(await screen.findByTestId('activity-breakdown-header'))
      .toBeInTheDocument()
    // The subtitle is in the header block — locate it via the testid
    // and then assert on its sibling. "Last 30 days" also appears on
    // user cards once they render, hence the scoped lookup.
    expect(screen.getByText(/Last 30 days — analytical sessions only/i))
      .toBeInTheDocument()
  })

  it('renders one card per user', async () => {
    render(<ActivityBreakdownPanel />)
    // Each card has a stable data-testid keyed on the email.
    await waitFor(() => {
      expect(screen.getByTestId(
        'activity-breakdown-thaob@queens.edu')).toBeInTheDocument()
    })
    expect(screen.getByTestId(
      'activity-breakdown-murdockm@queens.edu')).toBeInTheDocument()
    expect(screen.getByTestId(
      'activity-breakdown-ruurdsm@queens.edu')).toBeInTheDocument()
  })

  it('shows the empty state for a zero-interaction user', async () => {
    render(<ActivityBreakdownPanel />)
    expect(await screen.findByTestId(
      'activity-zero-murdockm@queens.edu')).toBeInTheDocument()
    expect(screen.getByText(/No activity in the last 30 days/i))
      .toBeInTheDocument()
  })

  it('shows the per-type breakdown counts on an active user', async () => {
    render(<ActivityBreakdownPanel />)
    const card = await screen.findByTestId(
      'activity-breakdown-thaob@queens.edu')
    // Bob has 12 council, 4 academic_review, 8 explain, 6
    // writing_assistant, 2 qa — every label appears in the per-type list.
    expect(within(card).getByText('Council')).toBeInTheDocument()
    expect(within(card).getByText('Academic Review')).toBeInTheDocument()
    expect(within(card).getByText('Explain')).toBeInTheDocument()
    expect(within(card).getByText('Writing Assistant')).toBeInTheDocument()
    expect(within(card).getByText('QA')).toBeInTheDocument()
    // The count appears alongside each label.
    expect(within(card).getByText('12')).toBeInTheDocument()
  })

  it('shows the session-type breakdown counts', async () => {
    render(<ActivityBreakdownPanel />)
    const card = await screen.findByTestId(
      'activity-breakdown-thaob@queens.edu')
    expect(within(card).getByText('280 page views')).toBeInTheDocument()
    expect(within(card).getByText('45 page views')).toBeInTheDocument()
  })

  it('shows the AI spend line only when total cost > 0', async () => {
    render(<ActivityBreakdownPanel />)
    // Bob: $0.6886 → cost line rendered ("$0.69" — two decimal places).
    const bob = await screen.findByTestId(
      'activity-breakdown-thaob@queens.edu')
    expect(within(bob).getByText(/AI spend:/i)).toBeInTheDocument()
    expect(within(bob).getByText(/\$0\.69/)).toBeInTheDocument()
    // Michael: $0.00 → cost line hidden.
    const michael = screen.getByTestId(
      'activity-breakdown-ruurdsm@queens.edu')
    expect(within(michael).queryByText(/AI spend:/i)).toBeNull()
  })
})
