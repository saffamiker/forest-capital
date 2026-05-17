/**
 * team-gate.test.tsx — the TeamGate component and the permission tiers.
 *
 * TeamGate reads useHasPermission → useAuth, gating on the session's
 * authoritative `permissions` array (populated from GET /api/auth/me).
 * Each case renders inside an AuthContext provider stubbed with a
 * permission set.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AuthContext } from '../App'
import TeamGate from '../components/TeamGate'

// The team_member role preset — everything a project member can do.
const TEAM_PERMS = [
  'view_analytics', 'ask_council', 'team_member',
  'generate_documents', 'export_package',
]
// The viewer role preset — a non-team authenticated user (e.g. Dr. Panttser).
const VIEWER_PERMS = ['view_analytics', 'ask_council']

function withPerms(permissions: string[] | null, ui: ReactNode) {
  const value = {
    session: permissions
      ? { token: 'test-token', email: 'user@queens.edu', permissions }
      : null,
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>,
  )
}

describe('TeamGate', () => {
  it('renders children normally for a user with the permission', () => {
    const { container } = withPerms(TEAM_PERMS, (
      <TeamGate><button>Generate report</button></TeamGate>
    ))
    expect(screen.getByRole('button', { name: 'Generate report' }))
      .toBeInTheDocument()
    // No gating wrapper when the permission is held.
    expect(container.querySelector('[aria-disabled="true"]')).toBeNull()
  })

  it('renders a disabled, gated state for a user without the permission', () => {
    const { container } = withPerms(VIEWER_PERMS, (
      <TeamGate tooltip="Team only"><button>Generate report</button></TeamGate>
    ))
    // The child still renders (muted) but the wrapper is marked disabled.
    expect(screen.getByText('Generate report')).toBeInTheDocument()
    const gated = container.querySelector('[aria-disabled="true"]')
    expect(gated).not.toBeNull()
    expect(gated).toHaveAttribute('title', 'Team only')
    // The muted layer is inert — pointer events are off.
    expect(container.querySelector('.pointer-events-none')).not.toBeNull()
  })

  it('hides the element entirely without the permission when showDisabled is false', () => {
    withPerms(VIEWER_PERMS, (
      <TeamGate showDisabled={false}><button>Secret action</button></TeamGate>
    ))
    expect(screen.queryByText('Secret action')).not.toBeInTheDocument()
  })

  it('still renders for a permitted user when showDisabled is false', () => {
    withPerms(TEAM_PERMS, (
      <TeamGate showDisabled={false}><button>Secret action</button></TeamGate>
    ))
    expect(screen.getByText('Secret action')).toBeInTheDocument()
  })

  it('honours a specific permission prop', () => {
    // A team member lacking manage_users is gated on the stricter check.
    withPerms(TEAM_PERMS, (
      <TeamGate permission="manage_users" showDisabled={false}>
        <button>Manage users</button>
      </TeamGate>
    ))
    expect(screen.queryByText('Manage users')).not.toBeInTheDocument()
  })

  it('treats an unauthenticated session as holding no permission', () => {
    withPerms(null, (
      <TeamGate showDisabled={false}><button>Gated</button></TeamGate>
    ))
    expect(screen.queryByText('Gated')).not.toBeInTheDocument()
  })
})
