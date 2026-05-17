import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

// SessionProvider reads useAuth() from ../App; mock it with a logged-in
// session so the provider mints a session_id and exposes the context.
vi.mock('../App', () => ({
  useAuth: () => ({ session: { token: 'tok', email: 'ruurdsm@queens.edu' } }),
}))

import { SessionProvider, useSession } from '../context/SessionContext'

function SessionDisplay() {
  const { sessionId, sessionType, setTestingMode } = useSession()
  return (
    <div>
      <span data-testid="type">{sessionType}</span>
      <span data-testid="has-id">{sessionId ? 'yes' : 'no'}</span>
      <button onClick={() => setTestingMode(true)}>Enable Testing</button>
      <button onClick={() => setTestingMode(false)}>Disable Testing</button>
    </div>
  )
}

describe('SessionContext', () => {
  it('defaults sessionType to analytical on session creation', () => {
    render(<SessionProvider><SessionDisplay /></SessionProvider>)
    expect(screen.getByTestId('type')).toHaveTextContent('analytical')
  })

  it('mints a session_id when an authenticated session exists', () => {
    render(<SessionProvider><SessionDisplay /></SessionProvider>)
    expect(screen.getByTestId('has-id')).toHaveTextContent('yes')
  })

  it('setTestingMode toggles the band and back to analytical', () => {
    render(<SessionProvider><SessionDisplay /></SessionProvider>)
    fireEvent.click(screen.getByText('Enable Testing'))
    expect(screen.getByTestId('type')).toHaveTextContent('testing')
    fireEvent.click(screen.getByText('Disable Testing'))
    expect(screen.getByTestId('type')).toHaveTextContent('analytical')
  })
})
