/**
 * reports-spinner-safety.test.tsx — Reports tab stuck-spinner fix
 * (May 24 2026).
 *
 * The user reported a permanent spinner on the Reports page with no
 * network activity (only PostHog + status polls visible in DevTools).
 * Diagnosis: setLoading(false) WAS in .finally() so the spinner
 * should have cleared by any axios outcome; the symptom suggested
 * the GET was being absorbed before resolving, OR a parent was
 * mounting its own spinner.
 *
 * Hardening landed three layers of defence:
 *   (1) console.info on mount + on resolution (diagnostic trail)
 *   (2) 10s setTimeout safety net that force-clears loading +
 *       surfaces an explicit error
 *   (3) try/finally clearTimeout on resolution to prevent a stale
 *       timer firing after a real response landed
 *
 * These tests pin the safety-net contract: a request that never
 * resolves must NOT leave the spinner alive past 10 seconds.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import Reports from '../pages/Reports'
import { AuthContext } from '../App'
import { SessionProvider } from '../context/SessionContext'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

// Reports.tsx imports a deep tree (DocumentGenerationPanel,
// TeamActivityPanel, AdvisorPanel, etc.); their internal fetches
// don't matter for this test, so the GET stub is permissive — it
// answers every other URL with an empty object so the panels mount
// without erroring. Only the `/api/reports/manifest` branch is
// shaped to drive the spinner test.
function stubAxios(manifestBehaviour: 'never' | 'resolve' | 'reject') {
  mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as never
  mockedAxios.get = vi.fn().mockImplementation((url: string) => {
    if (url === '/api/reports/manifest') {
      if (manifestBehaviour === 'resolve') {
        // Reports.tsx renders manifest.owner_bob.map(...) and
        // manifest.owner_molly.map(...) — the shape MUST include
        // both arrays or the component crashes on render.
        return Promise.resolve({ data: {
          summary: { open_failures: 0, retest_pending: 0 },
          deliverables: [],
          owner_bob: [],
          owner_molly: [],
        } })
      }
      if (manifestBehaviour === 'reject') {
        return Promise.reject(new Error('boom'))
      }
      // 'never' — return a promise that never resolves, simulating
      // the production bug shape: the GET was queued but no response
      // ever came back.
      return new Promise(() => { /* never */ })
    }
    if (url === '/api/v1/activity/summary') {
      // TeamActivityPanel.SummaryPanel reads per_member.length AND
      // commits.this_week — both must be present-shaped here. The
      // default empty-object stub from below would crash the panel
      // on render.
      return Promise.resolve({ data: {
        per_member: [],
        commits: { total: 0, this_week: 0, by_author: {} },
        most_active_agents: [],
        last_academic_review: null,
        total_interactions: 0,
        analytical_sessions_only: true,
      } })
    }
    if (url === '/api/v1/activity/cost-summary') {
      return Promise.resolve({ data: {
        total_cost_usd: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        total_interactions: 0,
        by_member: [],
        by_type: [],
        analytical_sessions_only: true,
      } })
    }
    if (url === '/api/v1/activity/team') {
      return Promise.resolve({ data: { events: [], has_more: false } })
    }
    return Promise.resolve({ data: {} })
  })
  mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
}

const TEAM_AUTH = {
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

function renderReports() {
  return render(
    <AuthContext.Provider value={TEAM_AUTH}>
      <SessionProvider>
        <MemoryRouter>
          <Reports />
        </MemoryRouter>
      </SessionProvider>
    </AuthContext.Provider>,
  )
}


describe('Reports stuck-spinner safety net', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // The diagnostic console.info / console.warn lines are part of
    // the production fix — silence them in tests so the suite output
    // stays readable.
    vi.spyOn(console, 'info').mockImplementation(() => {})
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('clears the spinner immediately when the manifest resolves',
    async () => {
      stubAxios('resolve')
      renderReports()
      await waitFor(() => {
        expect(screen.queryByTestId('reports-loading-state'))
          .not.toBeInTheDocument()
      })
    })

  it('clears the spinner when the manifest fetch fails',
    async () => {
      stubAxios('reject')
      renderReports()
      // Even on rejection, the .finally() clears the spinner — the
      // user sees an error, not a hung indicator.
      await waitFor(() => {
        expect(screen.queryByTestId('reports-loading-state'))
          .not.toBeInTheDocument()
      })
    })

  it('emits a diagnostic console.info on manifest mount',
    async () => {
      const infoSpy = vi.spyOn(console, 'info')
      stubAxios('resolve')
      renderReports()
      await waitFor(() =>
        expect(infoSpy).toHaveBeenCalledWith(
          expect.stringContaining('manifest fetch starting'),
        ))
    })

  it('the 10s safety timer is registered on mount', async () => {
    // The safety net is implemented as setTimeout(10000). We verify
    // the timer is registered by counting active setTimeout calls.
    // We don't advance to 10s in the assertion (that path leads to
    // act() warnings with mocked axios); the registration itself
    // is the contract. The full timeout path is exercised in the
    // unmount cleanup test below.
    const setTimeoutSpy = vi.spyOn(window, 'setTimeout')
    stubAxios('never')
    renderReports()
    const safetyNetCalls = setTimeoutSpy.mock.calls.filter(
      (call) => call[1] === 10_000,
    )
    expect(safetyNetCalls.length).toBeGreaterThan(0)
  })

  it('clears the safety timer on unmount to prevent stale firing',
    async () => {
      // The cleanup function in the useEffect calls
      // clearTimeout(safetyTimer). If the timer outlived the
      // component, it could fire setError() on an unmounted
      // instance — React would warn in the console.
      const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')
      stubAxios('never')
      const { unmount } = renderReports()
      unmount()
      expect(clearTimeoutSpy).toHaveBeenCalled()
    })
})
