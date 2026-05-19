/**
 * audit-panel.test.tsx — the statistical-audit findings panel.
 *
 * Focused on the WARN acknowledge/resolve workflow on a finding row.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('axios', () => ({ default: { post: vi.fn() } }))

import axios from 'axios'
import { FindingRow } from '../components/AuditPanel'

const mockedAxios = axios as unknown as { post: ReturnType<typeof vi.fn> }

interface RowFinding {
  id: number
  layer: number
  check_name: string
  metric: string
  strategy: string | null
  severity: string
  status: string
  platform_value: string | null
  auditor_value: string | null
  discrepancy: string | null
  auditor_reasoning: string | null
  resolved?: boolean
  resolution_note?: string | null
}

function warnFinding(over: Partial<RowFinding> = {}): RowFinding {
  return {
    id: 5, layer: 3, check_name: 'Turnover direction', metric: 'true_turnover',
    strategy: null, severity: 'warning', status: 'warning',
    platform_value: null, auditor_value: null, discrepancy: null,
    auditor_reasoning: null, resolved: false, resolution_note: null, ...over,
  }
}

beforeEach(() => {
  mockedAxios.post.mockReset()
  mockedAxios.post.mockResolvedValue({ data: {} })
})

describe('AuditPanel — WARN acknowledge workflow', () => {
  it('a WARN finding exposes an Acknowledge action when expanded', () => {
    render(<FindingRow f={warnFinding()} />)
    // The row is expandable purely because it is a WARN finding.
    fireEvent.click(screen.getByText(/Turnover direction/))
    expect(screen.getByRole('button', { name: 'Acknowledge' }))
      .toBeInTheDocument()
  })

  it('saving an acknowledgement POSTs the resolve endpoint and shows the badge',
    async () => {
      render(<FindingRow f={warnFinding()} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }))
      fireEvent.change(
        screen.getByPlaceholderText(/Describe how you have addressed/),
        { target: { value: 'Accepted as a documented limitation.' } })
      fireEvent.click(
        screen.getByRole('button', { name: 'Save acknowledgement' }))
      await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/audit/findings/5/resolve',
        { resolution_note: 'Accepted as a documented limitation.' }))
      // The green "Acknowledged" badge appears after a successful save.
      expect(await screen.findAllByText('Acknowledged'))
        .not.toHaveLength(0)
    })

  it('a finding that is already resolved renders the Acknowledged badge', () => {
    render(<FindingRow f={warnFinding({
      resolved: true, resolution_note: 'Reviewed and accepted.' })} />)
    expect(screen.getAllByText('Acknowledged').length).toBeGreaterThan(0)
  })

  it('does not offer Acknowledge on a non-WARN finding', () => {
    render(<FindingRow f={warnFinding({ status: 'pass' })} />)
    // A passing finding with no detail is not expandable / has no action.
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull()
  })
})
