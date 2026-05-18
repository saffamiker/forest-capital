/**
 * qa-hub.test.tsx — the QA tab restructured as a two-section hub.
 *
 * QAHub renders the Methodology Review (every user) and the Statistical
 * Audit (full panel team-only, read-only summary for viewers), a Run
 * Full QA button and a Presentation View certificate. The two heavy
 * child panels are stubbed; axios and the QA store are controlled so
 * the hub's own logic is what is exercised.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { ReactNode } from 'react'
import axios from 'axios'
import { AuthContext } from '../App'
import { useQAStore } from '../stores/qaStore'
import type { QAAuditResult } from '../types/agents'

vi.mock('axios')
vi.mock('../components/QAAuditPanel', () => ({
  default: () => <div data-testid="methodology-panel">methodology</div>,
}))
vi.mock('../components/AuditPanel', () => ({
  default: () => <div data-testid="audit-panel">audit findings</div>,
}))

import QAHub from '../pages/QAHub'

const TEAM_PERMS = [
  'view_analytics', 'ask_council', 'team_member',
  'generate_documents', 'export_package',
]
const VIEWER_PERMS = ['view_analytics', 'ask_council']

function withPerms(permissions: string[], ui: ReactNode) {
  const value = {
    session: { token: 't', email: 'u@queens.edu', permissions },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>,
  )
}

function qaResult(verdict: 'PASS' | 'WARN' | 'FAIL'): QAAuditResult {
  return {
    verdict,
    checks_passed: verdict === 'PASS' ? 39 : 35,
    checks_warned: verdict === 'WARN' ? 4 : 0,
    checks_failed: verdict === 'FAIL' ? 2 : 0,
    checks_total: 39,
    items: [],
  }
}

function auditRun(failed: number, warnings: number) {
  return {
    status: 'complete',
    triggered_at: '2026-05-17T10:00:00Z',
    completed_at: '2026-05-17T10:05:00Z',
    total_checks: 68,
    passed: 68 - failed - warnings,
    failed,
    warnings,
    layer_1_status: 'pass',
    layer_2_status: 'pass',
    layer_3_status: 'pass',
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  // Reset the QA store to a known PASS result with a stubbed reload.
  useQAStore.setState({
    result: qaResult('PASS'),
    reload: vi.fn().mockResolvedValue(undefined),
    error: null,
  })
  vi.mocked(axios.get).mockResolvedValue({ data: { run: auditRun(0, 0) } })
  vi.mocked(axios.post).mockResolvedValue({ data: { status: 'started' } })
})

describe('QAHub — two-section hub', () => {
  it('renders both the Methodology Review and Statistical Audit sections', () => {
    withPerms(TEAM_PERMS, <QAHub />)
    expect(screen.getByRole('heading', { name: 'Methodology Review' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Statistical Audit' })).toBeInTheDocument()
    expect(screen.getByTestId('methodology-panel')).toBeInTheDocument()
  })

  it('shows the full audit panel to a team member', () => {
    withPerms(TEAM_PERMS, <QAHub />)
    expect(screen.getByTestId('audit-panel')).toBeInTheDocument()
  })

  it('hides the full audit panel from a viewer — summary only', async () => {
    withPerms(VIEWER_PERMS, <QAHub />)
    expect(screen.queryByTestId('audit-panel')).not.toBeInTheDocument()
    expect(
      await screen.findByText(/Full results available to project team members/),
    ).toBeInTheDocument()
  })
})

describe('QAHub — Run Full QA', () => {
  it('triggers both the methodology reload and the statistical audit endpoint', () => {
    withPerms(TEAM_PERMS, <QAHub />)
    fireEvent.click(screen.getByRole('button', { name: /Run Full QA/ }))
    expect(useQAStore.getState().reload).toHaveBeenCalled()
    expect(vi.mocked(axios.post)).toHaveBeenCalledWith(
      '/api/v1/audit/run', { triggered_by: 'manual' },
    )
  })

  it('gates the Run Full QA button for a viewer', async () => {
    const { container } = withPerms(VIEWER_PERMS, <QAHub />)
    // TeamGate wraps the button — a viewer gets the inert, disabled state.
    expect(container.querySelector('[aria-disabled="true"]')).not.toBeNull()
    // Let the viewer-summary fetch settle so no state update escapes act().
    await screen.findByText(/Full results available to project team members/)
  })
})

describe('QAHub — Presentation View certificate', () => {
  it('renders the certificate with all three status boxes', async () => {
    withPerms(TEAM_PERMS, <QAHub />)
    fireEvent.click(screen.getByRole('button', { name: /Presentation View/ }))
    expect(await screen.findByText('Quality Assurance Certificate')).toBeInTheDocument()
    expect(screen.getByText('Methodology Review')).toBeInTheDocument()
    expect(screen.getByText('Statistical Audit')).toBeInTheDocument()
    expect(await screen.findByText(/OVERALL:/)).toBeInTheDocument()
  })

  it('shows OVERALL PASS (green) when both audits pass', async () => {
    useQAStore.setState({ result: qaResult('PASS') })
    vi.mocked(axios.get).mockResolvedValue({ data: { run: auditRun(0, 0) } })
    withPerms(TEAM_PERMS, <QAHub />)
    fireEvent.click(screen.getByRole('button', { name: /Presentation View/ }))
    expect(await screen.findByText('OVERALL: PASS')).toBeInTheDocument()
  })

  it('shows OVERALL WARN (amber) when warnings are present but no failures', async () => {
    useQAStore.setState({ result: qaResult('PASS') })
    vi.mocked(axios.get).mockResolvedValue({ data: { run: auditRun(0, 3) } })
    withPerms(TEAM_PERMS, <QAHub />)
    fireEvent.click(screen.getByRole('button', { name: /Presentation View/ }))
    expect(await screen.findByText('OVERALL: WARN')).toBeInTheDocument()
  })

  it('shows OVERALL FAIL (red) when any check fails', async () => {
    useQAStore.setState({ result: qaResult('PASS') })
    vi.mocked(axios.get).mockResolvedValue({ data: { run: auditRun(2, 0) } })
    withPerms(TEAM_PERMS, <QAHub />)
    fireEvent.click(screen.getByRole('button', { name: /Presentation View/ }))
    expect(await screen.findByText('OVERALL: FAIL')).toBeInTheDocument()
  })
})
