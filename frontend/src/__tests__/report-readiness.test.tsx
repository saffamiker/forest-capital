/**
 * report-readiness.test.tsx — Workstream C (May 28 2026).
 *
 * Frontend coverage for the report-readiness UX:
 *   - reportReadinessStore — TTL + reload semantics, label rendering.
 *   - ReportReadinessBanner — three states (ready / blocked / unknown).
 *   - ReportBlockingModal — open/close behaviour and blocker list
 *     rendering.
 *   - DocumentGenerationPanel client-side gate — clicking Generate
 *     while blocked opens the modal without firing POST; the
 *     defence-in-depth path catches a 422 from the server when a
 *     stale store let the click through.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('axios', () => {
  const mod = {
    post: vi.fn(), get: vi.fn(), delete: vi.fn(),
    isAxiosError: vi.fn(() => false),
  }
  return { default: mod }
})

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import type { AxiosError } from 'axios'
import type { ReactNode } from 'react'

import { AuthContext } from '../App'
import {
  readinessBlockerLabels, useReportReadinessStore,
} from '../stores/reportReadinessStore'
import type { ReportReadiness } from '../stores/reportReadinessStore'
import {
  ReportBlockingModal, ReportReadinessBanner,
} from '../components/ReportReadinessIndicator'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'
import { __resetGenerationJobs } from '../lib/generationJobs'


const TEAM_PERMS = [
  'view_analytics', 'ask_council', 'team_member',
  'generate_documents', 'export_package',
]


function renderWithAuth(ui: ReactNode) {
  const value = {
    session: {
      token: 't', email: 'thaob@queens.edu', permissions: TEAM_PERMS,
    },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={['/reports']}>{ui}</MemoryRouter>
    </AuthContext.Provider>)
}

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  isAxiosError: typeof axios.isAxiosError
}


const READY: ReportReadiness = {
  is_ready: true,
  blocking_count: 0,
  statistical: { unreviewed_warnings: [], unreviewed_failures: [] },
  methodology: { unresolved_warnings: [], unresolved_failures: [] },
  checked_at: '2026-05-28T00:00:00+00:00',
}


const BLOCKED: ReportReadiness = {
  is_ready: false,
  blocking_count: 2,
  statistical: {
    unreviewed_warnings: [{
      finding_id: 7, layer: 2, check_name: 'STATCHECKLABEL',
      metric: 'sharpe_ratio', strategy: 'REGIME_SWITCHING',
      status: 'warning', discrepancy: '0.4%',
    }],
    unreviewed_failures: [],
  },
  methodology: {
    unresolved_warnings: [{
      check_id: 'P03', check: 'METHCHECKLABEL',
      description: '...', category: 'PORTFOLIO_MECHANICS',
      status: 'WARN',
    }],
    unresolved_failures: [],
  },
  checked_at: '2026-05-28T00:00:00+00:00',
}


beforeEach(() => {
  // Reset the store between tests so a stale verdict from one test
  // does not leak into another.
  useReportReadinessStore.setState({
    readiness: null, loading: false, fetchedAt: null,
  })
  __resetGenerationJobs()
  mockedAxios.get = vi.fn().mockResolvedValue({ data: READY })
  mockedAxios.post = vi.fn().mockResolvedValue({
    data: { job_id: 'JOB_TOKEN_42', status: 'pending' },
  })
  // Stub axios.isAxiosError so the panel's 422 branch can match.
  const isAxiosErrorStub = (value: unknown): boolean => (
    value !== null && typeof value === 'object'
    && (value as { isAxiosError?: boolean }).isAxiosError === true
  )
  mockedAxios.isAxiosError = isAxiosErrorStub as typeof axios.isAxiosError
})


describe('reportReadinessStore', () => {
  it('renders the four blocker categories with the failures-first ordering',
    () => {
      const r: ReportReadiness = {
        is_ready: false, blocking_count: 4,
        statistical: {
          unreviewed_warnings: [{
            finding_id: 1, layer: 2, check_name: 'stat-warn',
            metric: 'm', strategy: null, status: 'warning',
            discrepancy: null }],
          unreviewed_failures: [{
            finding_id: 2, layer: 3, check_name: 'stat-fail',
            metric: 'm', strategy: null, status: 'fail',
            discrepancy: null }],
        },
        methodology: {
          unresolved_warnings: [{ check_id: 'P03', check: 'meth-warn',
            description: '...', category: 'PORTFOLIO_MECHANICS',
            status: 'WARN' }],
          unresolved_failures: [{ check_id: 'S08', check: 'meth-fail',
            description: '...', category: 'STATISTICAL_INTEGRITY',
            status: 'FAIL' }],
        },
        checked_at: '2026-05-28T00:00:00+00:00',
      }
      const labels = readinessBlockerLabels(r)
      expect(labels).toHaveLength(4)
      expect(labels[0]).toMatch(/^Statistical FAIL/)
      expect(labels[1]).toMatch(/^Statistical WARN unreviewed/)
      expect(labels[2]).toMatch(/^Methodology FAIL/)
      expect(labels[3]).toMatch(/^Methodology WARN unreviewed/)
    })

  it('an empty readiness renders no labels', () => {
    expect(readinessBlockerLabels(null)).toEqual([])
    expect(readinessBlockerLabels(READY)).toEqual([])
  })

  it('load() respects the TTL — fresh state does not re-fetch', async () => {
    const fetched: string[] = []
    mockedAxios.get = vi.fn().mockImplementation(() => {
      fetched.push('fetch')
      return Promise.resolve({ data: READY })
    })
    const { load } = useReportReadinessStore.getState()
    await load()
    expect(fetched.length).toBe(1)
    await load()    // within TTL — no second call.
    expect(fetched.length).toBe(1)
  })

  it('reload() always fetches', async () => {
    const fetched: string[] = []
    mockedAxios.get = vi.fn().mockImplementation(() => {
      fetched.push('fetch')
      return Promise.resolve({ data: READY })
    })
    const { load, reload } = useReportReadinessStore.getState()
    await load()
    await reload()
    expect(fetched.length).toBe(2)
  })
})


describe('ReportReadinessBanner', () => {
  it('renders the ready banner when is_ready is true', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({ data: READY })
    render(<ReportReadinessBanner />)
    await waitFor(() => {
      expect(screen.getByTestId('report-readiness-banner-ready'))
        .toBeInTheDocument()
    })
  })

  it('renders the blocked banner with the blocking count when is_ready is false',
    async () => {
      mockedAxios.get = vi.fn().mockResolvedValue({ data: BLOCKED })
      render(<ReportReadinessBanner />)
      await waitFor(() => {
        const blocked = screen.getByTestId('report-readiness-banner-blocked')
        expect(blocked).toBeInTheDocument()
        expect(blocked.textContent).toMatch(/2 audit items/)
      })
    })

  it('shows the unknown banner when readiness fails to load', async () => {
    mockedAxios.get = vi.fn().mockRejectedValue(new Error('network'))
    render(<ReportReadinessBanner />)
    await waitFor(() => {
      expect(screen.getByTestId('report-readiness-banner-unknown'))
        .toBeInTheDocument()
    })
  })
})


describe('ReportBlockingModal', () => {
  it('renders nothing when open is false', () => {
    const { container } = render(
      <ReportBlockingModal open={false} onClose={() => undefined}
        blockers={['x', 'y']} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders every blocker as a list item when open', () => {
    render(<ReportBlockingModal open={true} onClose={() => undefined}
      blockers={['Statistical FAIL — L2 · Aaa', 'Methodology WARN — P03']} />)
    expect(screen.getByTestId('report-blocking-modal')).toBeInTheDocument()
    expect(screen.getByTestId('report-blocker-0').textContent)
      .toContain('Statistical FAIL — L2 · Aaa')
    expect(screen.getByTestId('report-blocker-1').textContent)
      .toContain('Methodology WARN — P03')
  })

  it('Close button fires onClose', () => {
    const onClose = vi.fn()
    render(<ReportBlockingModal open={true} onClose={onClose}
      blockers={['anything']} />)
    fireEvent.click(screen.getByTestId('report-blocking-modal-dismiss'))
    expect(onClose).toHaveBeenCalled()
  })

  it('renders each cold cache by name when coldCaches is supplied',
    () => {
      // The 422 detail for caches_not_warm carries a cold_caches list.
      // The modal must surface each entry by name so the user knows
      // exactly which warm to trigger (a generic "warm the caches"
      // instruction was previously the only signal).
      render(<ReportBlockingModal open={true} onClose={() => undefined}
        blockers={[]}
        coldCaches={[
          'performance_chart',
          'oos_cost_sensitivity',
          'oos_summary',
        ]} />)
      const wrapper = screen.getByTestId('report-blocking-cold-caches')
      expect(wrapper).toBeInTheDocument()
      expect(wrapper.textContent).toMatch(
        /Brief generation requires the following caches/i)
      // Each cache rendered as a list item with a stable testid.
      expect(screen.getByTestId('report-cold-cache-0').textContent)
        .toContain('performance_chart')
      expect(screen.getByTestId('report-cold-cache-1').textContent)
        .toContain('oos_cost_sensitivity')
      expect(screen.getByTestId('report-cold-cache-2').textContent)
        .toContain('oos_summary')
      // The actionable instruction is present.
      expect(wrapper.textContent).toMatch(/Trigger a warm and retry/i)
    })

  it('hides the cold-caches block when coldCaches is empty or absent',
    () => {
      const { rerender } = render(
        <ReportBlockingModal open={true} onClose={() => undefined}
          blockers={['some blocker']} />)
      expect(screen.queryByTestId('report-blocking-cold-caches'))
        .not.toBeInTheDocument()
      // Empty array also hides.
      rerender(<ReportBlockingModal open={true} onClose={() => undefined}
        blockers={['some blocker']} coldCaches={[]} />)
      expect(screen.queryByTestId('report-blocking-cold-caches'))
        .not.toBeInTheDocument()
    })
})


describe('DocumentGenerationPanel — readiness gate', () => {
  // The panel renders three cards with the same gating behaviour.
  // These tests target the Generate button on the midpoint card —
  // scoped to that specific card so the section heading "Generate
  // Documents" and the trailing paragraph don't confuse the
  // selector.

  function midpointCard(): HTMLElement {
    return screen.getByText('Midpoint Submission Paper')
      .closest('.card') as HTMLElement
  }

  it('clicking Generate while blocked opens the modal without firing POST',
    async () => {
      useReportReadinessStore.setState({
        readiness: BLOCKED, loading: false, fetchedAt: new Date(),
      })
      mockedAxios.get = vi.fn().mockResolvedValue({ data: BLOCKED })
      renderWithAuth(<DocumentGenerationPanel />)
      const generateBtn = await waitFor(() =>
        within(midpointCard())
          .getByRole('button', { name: /^Generate$/ }))
      fireEvent.click(generateBtn)
      expect(await screen.findByTestId('report-blocking-modal'))
        .toBeInTheDocument()
      expect(mockedAxios.post).not.toHaveBeenCalled()
    })

  it('clicking Generate while ready fires the POST and no modal',
    async () => {
      useReportReadinessStore.setState({
        readiness: READY, loading: false, fetchedAt: new Date(),
      })
      mockedAxios.get = vi.fn().mockResolvedValue({ data: READY })
      renderWithAuth(<DocumentGenerationPanel />)
      const generateBtn = await waitFor(() =>
        within(midpointCard())
          .getByRole('button', { name: /^Generate$/ }))
      fireEvent.click(generateBtn)
      await waitFor(() => {
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/api/v1/export/midpoint-paper',
        )
      })
      expect(screen.queryByTestId('report-blocking-modal')).toBeNull()
    })

  it('a server 422 with report_not_ready opens the modal from the response',
    async () => {
      useReportReadinessStore.setState({
        readiness: READY, loading: false, fetchedAt: new Date(),
      })
      // The panel uses axios.isAxiosError to detect — return true for
      // the rejected object so the 422 branch runs.
      mockedAxios.isAxiosError = ((value: unknown): boolean => (
        value !== null && typeof value === 'object'
        && (value as { isAxiosError?: boolean }).isAxiosError === true
      )) as typeof axios.isAxiosError
      const axiosError = {
        isAxiosError: true,
        response: {
          status: 422,
          data: {
            detail: {
              error: 'report_not_ready',
              message: 'SERVERMESSAGETOKEN — items must be reviewed.',
              blocking_count: 1,
              blockers: ['SERVERBLOCKERTOKEN — example blocker line.'],
            },
          },
        },
      } as unknown as AxiosError
      mockedAxios.post = vi.fn().mockRejectedValue(axiosError)

      renderWithAuth(<DocumentGenerationPanel />)
      const generateBtn = await waitFor(() =>
        within(midpointCard())
          .getByRole('button', { name: /^Generate$/ }))
      fireEvent.click(generateBtn)

      expect(await screen.findByTestId('report-blocking-modal'))
        .toBeInTheDocument()
      expect(screen.getByTestId('report-blocker-0').textContent)
        .toContain('SERVERBLOCKERTOKEN')
      expect(screen.getByText(/SERVERMESSAGETOKEN/)).toBeInTheDocument()
    })
})
