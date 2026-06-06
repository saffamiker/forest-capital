/**
 * audit-panel.test.tsx — the statistical-audit findings panel.
 *
 * Focused on the WARN acknowledge/resolve workflow on a finding row.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

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
  resolved_by?: string | null
  resolved_at?: string | null
  auto_acknowledged?: boolean
  // Bridge #75 -- migration 055 adds the locked disclosure column.
  // Mirrors the AuditFinding interface in components/AuditPanel.tsx.
  locked_disclosure_text?: string | null
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
      // Bridge #75 -- the resolve POST now also carries an optional
      // disclosure_text (null when the team did not lock a separate
      // disclosure for the report).
      await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/audit/findings/5/resolve',
        {
          resolution_note: 'Accepted as a documented limitation.',
          disclosure_text: null,
        }))
      // The green "Acknowledged" badge appears after a successful save.
      expect(await screen.findAllByText('Acknowledged'))
        .not.toHaveLength(0)
    })

  it('a finding that is already resolved renders the Acknowledged badge', () => {
    render(<FindingRow f={warnFinding({
      resolved: true, resolution_note: 'Reviewed and accepted.' })} />)
    expect(screen.getAllByText('Acknowledged').length).toBeGreaterThan(0)
  })

  it('renders the reviewer + timestamp under an Acknowledged WARN', () => {
    // The migration-044 columns carry the WHO and WHEN of an
    // acknowledgement; the row must surface both so the team can
    // see what was reviewed and when without having to read the
    // PDF disclosures appendix.
    render(<FindingRow f={warnFinding({
      resolved: true,
      resolution_note: 'Reviewed and accepted.',
      resolved_by: 'ruurdsm@queens.edu',
      resolved_at: '2026-05-25T17:30:00Z',
    })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    const meta = screen.getByTestId('audit-ack-meta-5')
    expect(meta.textContent).toMatch(/Reviewed/)
    expect(meta.textContent).toMatch(/ruurdsm@queens.edu/)
    // Date is rendered through toLocaleDateString — assert the
    // year (locale-stable across CI runners) is present.
    expect(meta.textContent).toMatch(/2026/)
  })

  it('renders the timestamp returned by the resolve endpoint after a fresh save',
    async () => {
      // The endpoint returns the full updated finding; the panel reads
      // resolved_by + resolved_at off the response so the meta line
      // appears immediately, without waiting for a parent reload.
      mockedAxios.post.mockResolvedValueOnce({
        data: {
          ...warnFinding({ resolved: true,
            resolution_note: 'Accepted as a documented limitation.' }),
          resolved_by: 'ruurdsm@queens.edu',
          resolved_at: '2026-05-25T17:30:00Z',
        },
      })
      render(<FindingRow f={warnFinding()} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }))
      fireEvent.change(
        screen.getByPlaceholderText(/Describe how you have addressed/),
        { target: { value: 'Accepted as a documented limitation.' } })
      fireEvent.click(
        screen.getByRole('button', { name: 'Save acknowledgement' }))
      const meta = await screen.findByTestId('audit-ack-meta-5')
      expect(meta.textContent).toMatch(/Reviewed/)
      expect(meta.textContent).toMatch(/ruurdsm@queens.edu/)
    })

  it('does not offer Acknowledge on a non-WARN finding', () => {
    render(<FindingRow f={warnFinding({ status: 'pass' })} />)
    // A passing finding with no detail is not expandable / has no action.
    expect(screen.queryByRole('button', { name: 'Acknowledge' })).toBeNull()
  })
})


describe('AuditPanel — Edit Disclosure (Workstream E)', () => {
  // After a WARN is acknowledged the team needs to refine the note
  // without losing the existing entry. The inline editor is reopened
  // pre-populated with the current note; saving POSTs the same
  // /resolve endpoint, which UPDATEs the row in place — no new
  // route is required because the endpoint already upserts.

  it('an acknowledged finding exposes an Edit disclosure button', () => {
    render(<FindingRow f={warnFinding({
      resolved: true, resolution_note: 'Initial disclosure entry.',
    })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    const edit = screen.getByTestId('audit-edit-disclosure-5')
    expect(edit).toBeInTheDocument()
    expect(edit.textContent).toMatch(/Edit disclosure/i)
  })

  it('clicking Edit disclosure pre-populates the editor with the existing note',
    () => {
      const existing = 'PRESEEDTOKEN — original recorded disclosure.'
      render(<FindingRow f={warnFinding({
        resolved: true, resolution_note: existing,
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByTestId('audit-edit-disclosure-5'))
      // The inline editor opens with the textarea pre-populated.
      const textarea = screen.getByPlaceholderText(
        /Describe how you have addressed/,
      ) as HTMLTextAreaElement
      expect(textarea.value).toBe(existing)
    })

  it('Save after Edit POSTs the upsert endpoint with the refined note',
    async () => {
      render(<FindingRow f={warnFinding({
        resolved: true, resolution_note: 'Original note.',
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByTestId('audit-edit-disclosure-5'))
      const refined = 'EDITEDTOKEN — refined after team review.'
      fireEvent.change(
        screen.getByPlaceholderText(/Describe how you have addressed/),
        { target: { value: refined } })
      fireEvent.click(
        screen.getByRole('button', { name: 'Save acknowledgement' }))
      await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/audit/findings/5/resolve',
        { resolution_note: refined, disclosure_text: null }))
    })

  it('Cancel from Edit dismisses without firing a POST', () => {
    render(<FindingRow f={warnFinding({
      resolved: true, resolution_note: 'Existing note.',
    })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    fireEvent.click(screen.getByTestId('audit-edit-disclosure-5'))
    // Cancel returns to the read-only acknowledged view.
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(mockedAxios.post).not.toHaveBeenCalled()
    // The Edit disclosure button is visible again.
    expect(screen.getByTestId('audit-edit-disclosure-5')).toBeInTheDocument()
  })
})


describe('AuditPanel — Auto-acknowledged badge (Workstream A)', () => {
  // The carry pass marks audit_findings.auto_acknowledged=true when
  // it carried a prior ack forward against a value-matched finding.
  // The badge label must distinguish that carried state from a
  // freshly-typed acknowledgement so the team can see at a glance
  // which warnings need re-confirmation.

  it('an auto-acknowledged finding shows the Auto-acknowledged badge',
    () => {
      render(<FindingRow f={warnFinding({
        resolved: true,
        resolution_note: 'Carried disclosure from prior review.',
        auto_acknowledged: true,
      })} />)
      // Two surfaces — the header chip and the in-panel line. Both
      // must reflect the carried state.
      expect(screen.getByTestId('audit-auto-ack-badge-5')).toBeInTheDocument()
      expect(screen.getAllByText('Auto-acknowledged').length)
        .toBeGreaterThan(0)
      // The plain Acknowledged label must NOT appear — it would
      // suggest the team had re-confirmed the carry.
      expect(screen.queryByText(/^Acknowledged$/)).toBeNull()
    })

  it('a manually-acknowledged finding shows the plain Acknowledged badge',
    () => {
      render(<FindingRow f={warnFinding({
        resolved: true,
        resolution_note: 'Bob reviewed this.',
        auto_acknowledged: false,
      })} />)
      expect(screen.getByTestId('audit-ack-badge-5')).toBeInTheDocument()
      expect(screen.queryByTestId('audit-auto-ack-badge-5')).toBeNull()
    })

  it('expanding an auto-acknowledged finding shows the carry explanation',
    () => {
      render(<FindingRow f={warnFinding({
        resolved: true,
        resolution_note: 'Carried from prior.',
        auto_acknowledged: true,
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      // The italic explanation tells the user the value has not
      // materially changed since the original ack.
      expect(screen.getByText(/Carried from a prior review/i))
        .toBeInTheDocument()
    })

  it('Save after Edit endorses the ack and clears the auto-acknowledged label',
    async () => {
      render(<FindingRow f={warnFinding({
        resolved: true,
        resolution_note: 'Auto-carried disclosure.',
        auto_acknowledged: true,
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      // The header initially shows the Auto-acknowledged badge.
      expect(screen.getByTestId('audit-auto-ack-badge-5')).toBeInTheDocument()
      fireEvent.click(screen.getByTestId('audit-edit-disclosure-5'))
      const refined = 'Bob — re-confirmed after team review.'
      fireEvent.change(
        screen.getByPlaceholderText(/Describe how you have addressed/),
        { target: { value: refined } })
      fireEvent.click(
        screen.getByRole('button', { name: 'Save acknowledgement' }))
      // After Save the badge flips — the team has now endorsed
      // the ack and it is no longer a carry.
      await waitFor(() => {
        expect(screen.getByTestId('audit-ack-badge-5')).toBeInTheDocument()
      })
      expect(screen.queryByTestId('audit-auto-ack-badge-5')).toBeNull()
    })
})


describe('AuditPanel — Revoke Disclosure (Workstream F)', () => {
  // After a WARN is acknowledged the team may later determine the
  // disclosure was premature. Revoke deletes the recorded note via
  // /unresolve, after a confirmation step so a single click cannot
  // drop a recorded disclosure with no undo. The report-readiness
  // gate (workstream C) re-evaluates the finding as unreviewed
  // again because the server-side resolved flag is now false.

  it('an acknowledged finding exposes a Revoke disclosure button', () => {
    render(<FindingRow f={warnFinding({
      resolved: true, resolution_note: 'Recorded disclosure entry.',
    })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    const revoke = screen.getByTestId('audit-revoke-disclosure-5')
    expect(revoke).toBeInTheDocument()
    expect(revoke.textContent).toMatch(/Revoke disclosure/i)
  })

  it('clicking Revoke opens a confirmation modal showing the current note',
    () => {
      const existing = 'SHOWNINMODALTOKEN — recorded disclosure note.'
      render(<FindingRow f={warnFinding({
        resolved: true, resolution_note: existing,
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByTestId('audit-revoke-disclosure-5'))
      // Modal mounts and surfaces the current note for reference so
      // the user is not revoking blind. The note also still shows in
      // the row beneath the modal — scope the assertion to the modal
      // so the two renders don't collide.
      const modal = screen.getByTestId('audit-revoke-modal-5')
      expect(modal).toBeInTheDocument()
      expect(within(modal).getByText(/SHOWNINMODALTOKEN/))
        .toBeInTheDocument()
      // No POST has fired — confirmation is required.
      expect(mockedAxios.post).not.toHaveBeenCalled()
    })

  it('Cancel from the Revoke modal dismisses without firing /unresolve',
    () => {
      render(<FindingRow f={warnFinding({
        resolved: true, resolution_note: 'Existing.',
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByTestId('audit-revoke-disclosure-5'))
      fireEvent.click(screen.getByTestId('audit-revoke-cancel-5'))
      expect(mockedAxios.post).not.toHaveBeenCalled()
      // Acknowledged state intact — the Revoke control is still there.
      expect(screen.getByTestId('audit-revoke-disclosure-5')).toBeInTheDocument()
      expect(screen.getAllByText('Acknowledged').length).toBeGreaterThan(0)
    })

  it('Confirm POSTs /unresolve and reverts the row to unreviewed',
    async () => {
      render(<FindingRow f={warnFinding({
        resolved: true, resolution_note: 'About to be revoked.',
      })} />)
      fireEvent.click(screen.getByText(/Turnover direction/))
      fireEvent.click(screen.getByTestId('audit-revoke-disclosure-5'))
      fireEvent.click(screen.getByTestId('audit-revoke-confirm-5'))
      await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/audit/findings/5/unresolve'))
      // After Revoke the Acknowledge action becomes available again —
      // the finding is back to its unreviewed state.
      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Acknowledge' }))
          .toBeInTheDocument()
      })
      // The Revoke control is gone — there is nothing left to revoke.
      expect(screen.queryByTestId('audit-revoke-disclosure-5')).toBeNull()
    })
})


describe('AuditPanel — locked disclosure (bridge #75)', () => {
  it('exposes a disclosure-for-report textarea when editing', async () => {
    render(<FindingRow f={warnFinding({ id: 5 })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }))
    expect(screen.getByTestId('audit-disclosure-input-5'))
      .toBeInTheDocument()
  })

  it('POSTs the disclosure text alongside the resolution note', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: { id: 5, resolved: true,
              resolution_note: 'Internal review.',
              resolved_by: 'reviewer@queens.edu',
              resolved_at: '2026-06-06T18:00:00Z',
              locked_disclosure_text: 'Disclosed verbatim in the brief.' }})
    render(<FindingRow f={warnFinding({ id: 5 })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }))
    fireEvent.change(
      screen.getByPlaceholderText(/Describe how you have addressed/),
      { target: { value: 'Internal review.' }})
    fireEvent.change(screen.getByTestId('audit-disclosure-input-5'),
      { target: { value: 'Disclosed verbatim in the brief.' }})
    fireEvent.click(
      screen.getByRole('button', { name: 'Save acknowledgement' }))
    await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/v1/audit/findings/5/resolve',
      {
        resolution_note: 'Internal review.',
        disclosure_text: 'Disclosed verbatim in the brief.',
      }))
  })

  it('renders the locked disclosure copy box on a finding that has one', () => {
    render(<FindingRow f={warnFinding({
      id: 5, resolved: true,
      resolution_note: 'Internal review.',
      locked_disclosure_text: 'Bootstrap CI brackets the discrepancy.',
    })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    const box = screen.getByTestId('audit-locked-disclosure-5')
    expect(box).toBeInTheDocument()
    expect(box.textContent).toContain('Bootstrap CI brackets the discrepancy.')
    expect(screen.getByTestId('audit-copy-disclosure-5'))
      .toBeInTheDocument()
  })

  it('omits the copy box when no disclosure was locked', () => {
    render(<FindingRow f={warnFinding({
      id: 5, resolved: true,
      resolution_note: 'Internal review only.',
      // locked_disclosure_text intentionally absent.
    })} />)
    fireEvent.click(screen.getByText(/Turnover direction/))
    expect(screen.queryByTestId('audit-locked-disclosure-5'))
      .not.toBeInTheDocument()
  })
})
