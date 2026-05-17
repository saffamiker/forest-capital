/**
 * team-gate.test.tsx — the TeamGate component and the two access tiers.
 *
 * TeamGate reads useIsTeamMember → useAuth, so each case renders inside
 * an AuthContext provider stubbed with a team or non-team email.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AuthContext } from '../App'
import TeamGate from '../components/TeamGate'

function withAuth(email: string | null, ui: ReactNode) {
  const value = {
    session: email ? { token: 'test-token', email } : null,
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>,
  )
}

const TEAM_EMAIL = 'thaob@queens.edu'
const GUEST_EMAIL = 'panttserk@queens.edu'

describe('TeamGate', () => {
  it('renders children normally for a team member', () => {
    const { container } = withAuth(TEAM_EMAIL, (
      <TeamGate><button>Generate report</button></TeamGate>
    ))
    expect(screen.getByRole('button', { name: 'Generate report' }))
      .toBeInTheDocument()
    // No gating wrapper for a team member.
    expect(container.querySelector('[aria-disabled="true"]')).toBeNull()
  })

  it('renders a disabled, gated state for a non-team user', () => {
    const { container } = withAuth(GUEST_EMAIL, (
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

  it('hides the element entirely for a non-team user when showDisabled is false', () => {
    withAuth(GUEST_EMAIL, (
      <TeamGate showDisabled={false}><button>Secret action</button></TeamGate>
    ))
    expect(screen.queryByText('Secret action')).not.toBeInTheDocument()
  })

  it('still renders for a team member when showDisabled is false', () => {
    withAuth(TEAM_EMAIL, (
      <TeamGate showDisabled={false}><button>Secret action</button></TeamGate>
    ))
    expect(screen.getByText('Secret action')).toBeInTheDocument()
  })

  it('treats an unauthenticated session as non-team', () => {
    withAuth(null, (
      <TeamGate showDisabled={false}><button>Gated</button></TeamGate>
    ))
    expect(screen.queryByText('Gated')).not.toBeInTheDocument()
  })
})
