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
}

describe('UserManagementPanel', () => {
  beforeEach(() => {
    mockedAxios.get = vi.fn().mockResolvedValue({ data: { users: [USER_FIXTURE] } })
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
})
