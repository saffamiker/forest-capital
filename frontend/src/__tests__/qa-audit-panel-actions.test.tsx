/**
 * qa-audit-panel-actions.test.tsx — May 22 2026 QA overhaul (UI layer).
 *
 * Pins the QA Audit Panel's new schema rendering:
 *   - INCOMPLETE badge is a fourth verdict alongside PASS / WARN / FAIL
 *     with distinct slate styling (NOT the amber WARN colour).
 *   - The summary card surfaces checks_incomplete separately and shows
 *     a "re-run to complete analysis" notice when > 0.
 *   - The expanded WARN/FAIL row renders Finding / Implication / Action
 *     Required cards from the structured fields, and the action buttons
 *     match the action_type variant:
 *        code_fix              → Flag for Fix
 *        methodology_decision  → Flag for Fix + Mark as Intentional
 *        disclosure_required   → Copy Disclosure Text (real clipboard)
 *        rerun_required        → Re-run Audit (calls qaStore.reload)
 *   - Flag for Fix and Mark as Intentional are stubbed in this commit
 *     (TODO toast). The Re-run Audit and Copy Disclosure Text buttons
 *     are live.
 *
 * The qaStore is mocked so the panel renders against a synthetic audit
 * payload without hitting the network — the panel's own logic is what
 * is exercised.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react'
import axios from 'axios'
import { useQAStore } from '../stores/qaStore'
import { useGlossaryStore } from '../stores/glossaryStore'
import type { QAAuditResult, QACheck } from '../types/agents'
import QAAuditPanel from '../components/QAAuditPanel'

vi.mock('axios')
const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
}

const PASSING_CHECK: QACheck = {
  check_id: 'D01',
  check: 'Total returns verified',
  description: 'Total returns used (adjusted close)',
  category: 'DATA_INTEGRITY',
  status: 'PASS',
  evidence: 'Verified via auto_adjust=True on every yfinance call.',
}

const INCOMPLETE_CHECK: QACheck = {
  check_id: 'P04',
  check: 'No look-ahead in rebalancing',
  description: 'Signal at month t uses data through t-1',
  category: 'PORTFOLIO_MECHANICS',
  status: 'INCOMPLETE',
  evidence: 'Analysis not completed — re-run the QA audit to generate a full report.',
  action_type: 'rerun_required',
  remediation: 'Re-run the QA audit so the agent can examine this check.',
}

const METHODOLOGY_CHECK: QACheck = {
  check_id: 'P03',
  check: 'Transaction costs applied',
  description: 'Transaction costs applied both ways on every trade',
  category: 'PORTFOLIO_MECHANICS',
  status: 'WARN',
  evidence: 'Turnover sums |Δw| across all assets at each rebalance.',
  finding: 'Turnover sums |Δw|, capturing both the sell side and the buy side.',
  implication: 'Could be intentional double-sided capture (correct) or accidental double-counting (wrong).',
  remediation: 'Confirm design intent — both interpretations are plausible.',
  action_type: 'methodology_decision',
}

const CODE_FIX_CHECK: QACheck = {
  check_id: 'S08',
  check: 'Deflated Sharpe Ratio',
  description: 'DSR computed for n_trials=10',
  category: 'STATISTICAL_INTEGRITY',
  status: 'WARN',
  evidence: 'DSR not present on any strategy result row.',
  finding: 'deflated_sharpe_ratio field is null on every strategy.',
  implication: 'Without DSR the Tier 1 gate cannot be enforced.',
  remediation: 'Compute deflated_sharpe_ratio in the backtester and surface on each result.',
  action_type: 'code_fix',
}

const DISCLOSURE_CHECK: QACheck = {
  check_id: 'D02',
  check: 'No survivorship bias',
  description: 'All assets existed at backtest start',
  category: 'DATA_INTEGRITY',
  status: 'WARN',
  evidence: 'S&P 500 evolves through reconstitutions.',
  finding: 'Index reconstitution introduces a small upward bias (~0.1%/yr).',
  implication: 'Equity returns marginally overstated.',
  remediation: 'Disclose in the methodology section.',
  action_type: 'disclosure_required',
  disclosure_text: 'The S&P 500 series used in this analysis reflects post-reconstitution constituents and therefore carries a small survivorship bias; this is a known limit of the dataset and the magnitude is empirically small (roughly 0.1-0.2% per annum).',
}

function buildAudit(items: QACheck[]): QAAuditResult {
  const passed = items.filter((i) => i.status === 'PASS').length
  const warned = items.filter((i) => i.status === 'WARN').length
  const failed = items.filter((i) => i.status === 'FAIL').length
  const incomplete = items.filter((i) => i.status === 'INCOMPLETE').length
  return {
    verdict: failed > 0 ? 'FAIL' : warned > 0 ? 'WARN' : 'PASS',
    checks_passed: passed,
    checks_warned: warned,
    checks_failed: failed,
    checks_incomplete: incomplete,
    checks_total: items.length,
    items,
  }
}

// The store's load/reload signatures are async — we cast through
// unknown because the test stubs return a sync vi.fn() and the typeof
// approach trips the esbuild transformer on a method-call type
// expression.
type AnyAsync = (...args: unknown[]) => Promise<void>

beforeEach(() => {
  // Seed the QA store with a synthetic audit payload so the panel
  // renders without an axios round-trip.
  useQAStore.setState({
    result: buildAudit([PASSING_CHECK, METHODOLOGY_CHECK]),
    loading: false,
    load: vi.fn() as unknown as AnyAsync,
    reload: vi.fn() as unknown as AnyAsync,
  })
  // Glossary store — explanations are not exercised in these tests.
  useGlossaryStore.setState({
    qa: {}, loadQA: vi.fn() as unknown as AnyAsync,
  })
  // Default axios mocks. Override per-test for endpoint specifics.
  mockedAxios.get = vi.fn().mockResolvedValue({
    data: { overrides: {} },
  })
  mockedAxios.post = vi.fn().mockResolvedValue({
    data: { ok: true, triage_item_id: 42 },
  })
})


describe('QAAuditPanel — INCOMPLETE rendering', () => {
  it('renders the INCOMPLETE badge with slate styling, distinct from WARN', () => {
    useQAStore.setState({
      result: buildAudit([PASSING_CHECK, INCOMPLETE_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    // The summary's incomplete counter renders.
    expect(screen.getByTestId('qa-summary-incomplete-count')).toBeInTheDocument()
    // The row badge carries the badge-incomplete class — not badge-warn.
    const badges = screen.getAllByText('INCOMPLETE')
    const rowBadge = badges.find(
      (el) => el.classList.contains('badge-incomplete'))
    expect(rowBadge).toBeTruthy()
    // Critically, NOT styled as a WARN badge — distinct semantic.
    expect(rowBadge?.classList.contains('badge-warn')).toBe(false)
  })

  it('shows the re-run notice when checks are incomplete', () => {
    useQAStore.setState({
      result: buildAudit([PASSING_CHECK, INCOMPLETE_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    const notice = screen.getByTestId('qa-summary-incomplete-notice')
    expect(notice.textContent).toContain('1 check incomplete')
    expect(notice.textContent).toContain('re-run to complete')
  })

  it('omits the incomplete counter when zero', () => {
    useQAStore.setState({
      result: buildAudit([PASSING_CHECK, METHODOLOGY_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    // No incomplete checks → summary tile omits the counter entirely.
    expect(screen.queryByTestId('qa-summary-incomplete-count')).toBeNull()
    expect(screen.queryByTestId('qa-summary-incomplete-notice')).toBeNull()
  })

  it('legend includes the INCOMPLETE verdict definition', () => {
    render(<QAAuditPanel />)
    // The legend is the fourth row; the copy must NOT read like a
    // quality concern (no "concern was found" wording).
    expect(
      screen.getByText(/agent could not examine the data/i),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/audit-completeness signal/i),
    ).toBeInTheDocument()
  })
})


describe('QAAuditPanel — Action Required card variants', () => {
  it('methodology_decision shows BOTH Flag for Fix and Mark as Intentional', () => {
    useQAStore.setState({
      result: buildAudit([METHODOLOGY_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    // Expand the row by clicking its button.
    fireEvent.click(screen.getByText(METHODOLOGY_CHECK.description))
    const card = screen.getByTestId(`qa-action-card-${METHODOLOGY_CHECK.check_id}`)
    expect(within(card).getByText(/Flag for Fix/i)).toBeInTheDocument()
    expect(within(card).getByText(/Mark as Intentional/i)).toBeInTheDocument()
    // Finding + Implication render verbatim.
    expect(within(card).getByText(METHODOLOGY_CHECK.finding!)).toBeInTheDocument()
    expect(within(card).getByText(METHODOLOGY_CHECK.implication!)).toBeInTheDocument()
  })

  it('code_fix shows Flag for Fix only (no Mark as Intentional)', () => {
    useQAStore.setState({
      result: buildAudit([CODE_FIX_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(CODE_FIX_CHECK.description))
    const card = screen.getByTestId(`qa-action-card-${CODE_FIX_CHECK.check_id}`)
    expect(within(card).getByText(/Flag for Fix/i)).toBeInTheDocument()
    expect(within(card).queryByText(/Mark as Intentional/i)).toBeNull()
  })

  it('disclosure_required shows Copy Disclosure Text and the pre-drafted sentence', () => {
    useQAStore.setState({
      result: buildAudit([DISCLOSURE_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(DISCLOSURE_CHECK.description))
    const card = screen.getByTestId(`qa-action-card-${DISCLOSURE_CHECK.check_id}`)
    expect(within(card).getByText(/Copy Disclosure Text/i)).toBeInTheDocument()
    // The disclosure sentence renders verbatim so the user can verify
    // what's on the clipboard before pasting.
    expect(
      within(card).getByText(/post-reconstitution constituents/i),
    ).toBeInTheDocument()
  })

  it('rerun_required (INCOMPLETE) shows Re-run Audit and calls reload', () => {
    const reload = vi.fn()
    useQAStore.setState({
      result: buildAudit([INCOMPLETE_CHECK]),
      loading: false,
      reload: reload as unknown as AnyAsync,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(INCOMPLETE_CHECK.description))
    const card = screen.getByTestId(`qa-action-card-${INCOMPLETE_CHECK.check_id}`)
    const rerunBtn = within(card).getByText(/Re-run Audit/i)
    fireEvent.click(rerunBtn)
    expect(reload).toHaveBeenCalled()
  })

  it('PASS check renders no Action Required card', () => {
    useQAStore.setState({
      result: buildAudit([PASSING_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(PASSING_CHECK.description))
    // PASS sections have no structured fields → no action card.
    expect(
      screen.queryByTestId(`qa-action-card-${PASSING_CHECK.check_id}`),
    ).toBeNull()
  })

  it('Flag for Fix POSTs to the flag-for-fix endpoint with check context', async () => {
    useQAStore.setState({
      result: buildAudit([METHODOLOGY_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(METHODOLOGY_CHECK.description))
    fireEvent.click(screen.getByTestId(`qa-flag-${METHODOLOGY_CHECK.check_id}`))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        `/api/v1/qa/findings/${METHODOLOGY_CHECK.check_id}/flag-for-fix`,
        expect.objectContaining({
          check_title: METHODOLOGY_CHECK.check,
          finding: METHODOLOGY_CHECK.finding,
          implication: METHODOLOGY_CHECK.implication,
          remediation: METHODOLOGY_CHECK.remediation,
          // WARN status maps to 'major' severity per the backend
          // contract (FAIL would map to 'blocking').
          severity: 'major',
        }),
      )
    })
    // The success toast confirms the flag landed.
    await waitFor(() => {
      expect(
        screen.getByText(/Flagged P03 for fix/i),
      ).toBeInTheDocument()
    })
  })

  it('FAIL status flags as severity blocking', async () => {
    const failCheck: QACheck = { ...METHODOLOGY_CHECK, status: 'FAIL' }
    useQAStore.setState({
      result: buildAudit([failCheck]),
      loading: false,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(failCheck.description))
    fireEvent.click(screen.getByTestId(`qa-flag-${failCheck.check_id}`))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        expect.stringContaining('/flag-for-fix'),
        expect.objectContaining({ severity: 'blocking' }),
      )
    })
  })

  it('Mark as Intentional POSTs to the mark-intentional endpoint', async () => {
    useQAStore.setState({
      result: buildAudit([METHODOLOGY_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    fireEvent.click(screen.getByText(METHODOLOGY_CHECK.description))
    fireEvent.click(
      screen.getByTestId(`qa-intentional-${METHODOLOGY_CHECK.check_id}`))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        `/api/v1/qa/findings/${METHODOLOGY_CHECK.check_id}/mark-intentional`,
        expect.objectContaining({ note: METHODOLOGY_CHECK.finding }),
      )
    })
    await waitFor(() => {
      expect(
        screen.getByText(/marked as intentional/i),
      ).toBeInTheDocument()
    })
  })

  it('renders the Confirmed Intentional badge when an override exists', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: {
        overrides: {
          [METHODOLOGY_CHECK.check_id]: {
            marked_at: '2026-05-22T12:00:00Z',
            marked_by: 'ruurdsm@queens.edu',
            note: 'The double-sided cost capture is intentional.',
            audit_run_hash: null,
          },
        },
      },
    })
    useQAStore.setState({
      result: buildAudit([METHODOLOGY_CHECK]),
      loading: false,
    })
    render(<QAAuditPanel />)
    // The overrides fetch is async — wait for the badge to land.
    await waitFor(() => {
      // Expand the row first.
      fireEvent.click(screen.getByText(METHODOLOGY_CHECK.description))
      expect(
        screen.getByTestId(`qa-intentional-badge-${METHODOLOGY_CHECK.check_id}`),
      ).toBeInTheDocument()
    })
    // The badge surfaces the reviewer's email + the team note.
    expect(screen.getByText(/Confirmed Intentional/i)).toBeInTheDocument()
    expect(screen.getByText(/ruurdsm@queens\.edu/)).toBeInTheDocument()
    expect(
      screen.getByText(/The double-sided cost capture is intentional/),
    ).toBeInTheDocument()
    // The Action Required buttons must NOT render — the badge replaces them.
    expect(
      screen.queryByTestId(`qa-flag-${METHODOLOGY_CHECK.check_id}`),
    ).toBeNull()
    expect(
      screen.queryByTestId(`qa-intentional-${METHODOLOGY_CHECK.check_id}`),
    ).toBeNull()
  })
})
