/**
 * qa-running-pill.test.tsx
 *
 * The nav-ribbon "QA Running" pill in MainLayout. It shows whenever the
 * shared qaStore status is 'running' (a methodology or statistical audit
 * in progress — the same status QAStatusBadge polls), navigates to /qa
 * on click, and disappears automatically when the run completes.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import axios from 'axios'

import { AuthContext } from '../App'
import { BrandProvider } from '../context/BrandContext'
import { UIProvider } from '../context/UIContext'
import { SessionProvider } from '../context/SessionContext'
import MainLayout from '../layouts/MainLayout'
import { useQAStore } from '../stores/qaStore'

vi.mock('axios')
const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
  defaults: { headers: { common: Record<string, unknown> } }
}

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual =
    await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

const AUTH_VALUE = {
  session: {
    token: 't', email: 'ruurdsm@queens.edu',
    permissions: ['view_analytics', 'ask_council', 'team_member',
      'generate_documents', 'export_package', 'view_admin', 'manage_users'],
  },
  isVerifying: false,
  login: vi.fn(),
  logout: vi.fn().mockResolvedValue(undefined),
}

/** Mocks /api/v1/qa/status with the given `running` flag. */
function mockQaStatus(running: boolean) {
  mockedAxios.get = vi.fn().mockImplementation((url: string) => {
    if (typeof url === 'string' && url.includes('/qa/status')) {
      return Promise.resolve({ data: {
        verdict: 'WARN', tier: 2, age_hours: 2,
        strategy_hash: 'testhash00', present_mode_allowed: false,
        running,
      } })
    }
    return Promise.resolve({ data: {} })
  })
}

beforeEach(() => {
  mockQaStatus(false)
  mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
  mockedAxios.isAxiosError = ((() => false) as unknown) as typeof axios.isAxiosError
  mockedAxios.defaults = { headers: { common: {} } }
  useQAStore.setState({ tieredStatus: null, status: 'unknown' })
  mockNavigate.mockClear()
})

afterEach(() => { vi.restoreAllMocks() })

function renderLayout(ui: ReactNode = <div>home</div>) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AuthContext.Provider value={AUTH_VALUE}>
        <BrandProvider>
          <UIProvider>
            <SessionProvider>
              <Routes>
                <Route path="/" element={<MainLayout />}>
                  <Route index element={ui} />
                </Route>
              </Routes>
            </SessionProvider>
          </UIProvider>
        </BrandProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

describe('QA running pill', () => {
  it('renders in the nav ribbon while a QA audit is running', async () => {
    mockQaStatus(true)
    renderLayout()
    expect(await screen.findByText('QA Running')).toBeInTheDocument()
  })

  it('is absent when no QA audit is running', async () => {
    mockQaStatus(false)
    renderLayout()
    // Let the QAStatusBadge mount-poll settle, then assert no pill.
    await waitFor(() => expect(useQAStore.getState().status).not.toBe('unknown'))
    expect(screen.queryByText('QA Running')).not.toBeInTheDocument()
  })

  it('navigates to /qa when clicked', async () => {
    mockQaStatus(true)
    renderLayout()
    fireEvent.click(await screen.findByText('QA Running'))
    expect(mockNavigate).toHaveBeenCalledWith('/qa')
  })

  it('disappears automatically when the run completes', async () => {
    mockQaStatus(true)
    renderLayout()
    expect(await screen.findByText('QA Running')).toBeInTheDocument()
    // The status poll reports the run finished — the pill unmounts.
    act(() => { useQAStore.setState({ status: 'pass' }) })
    expect(screen.queryByText('QA Running')).not.toBeInTheDocument()
  })
})
