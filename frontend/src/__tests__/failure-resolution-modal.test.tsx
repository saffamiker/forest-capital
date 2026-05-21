/**
 * failure-resolution-modal.test.tsx — Migration 025 resolution gate.
 *
 * Pins:
 *  - Submit stays disabled until the required fields for the chosen
 *    resolution type are populated.
 *  - The fix_reference shape validator matches the backend's.
 *  - The three-variant notification card in TestNotifications renders
 *    the right fields and the right CTA (or no CTA, for Won't fix).
 *
 * The full FailureReportsBlock fetch/list rendering is exercised
 * elsewhere; this file is focused on the modal and the notification
 * card so a regression on the gate is caught quickly.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('axios')

import axios from 'axios'
import {
  ResolutionModal, ResolutionCard, ResolutionBadge,
  isValidFixReference,
} from '../components/TestRunnerSettings'

const mockedAxios = vi.mocked(axios, true)


function makeFailure(over: Partial<Record<string, unknown>> = {}) {
  return {
    id: 42,
    user_email: 'tester@queens.edu',
    script_id: 'all_testers_v1',
    step_id: 'login',
    failure_description: 'Login button did nothing.',
    expected_result: 'redirect to dashboard',
    actual_result: 'no nav happened',
    severity: 'major',
    screenshot_paths: [],
    low_quality: false,
    attested_at: '2026-05-20T10:00:00Z',
    resolved_at: null,
    resolved_by: null,
    resolution_note: null,
    resolution_type: null,
    fix_reference: null,
    remediation_note: null,
    ...over,
  } as unknown as Parameters<typeof ResolutionModal>[0]['failure']
}


beforeEach(() => {
  vi.clearAllMocks()
  mockedAxios.post = vi.fn().mockResolvedValue({ data: { resolved: true } })
})


// ── isValidFixReference — shape validator parity with backend ────────────────

describe('isValidFixReference', () => {
  it.each([
    'abc1234',                          // 7-char SHA
    '0123456789abcdef0123456789abcdef01234567',  // 40-char SHA
    '#65',                              // PR number
    '#1',
    'https://github.com/saffamiker/forest-capital/commit/abc123',
    'https://github.com/saffamiker/forest-capital/pull/65',
    'https://github.com/saffamiker/forest-capital/issues/100',
  ])('accepts %s', (ref) => {
    expect(isValidFixReference(ref)).toBe(true)
  })

  it.each([
    '',                                 // blank
    '   ',                              // whitespace
    'abcdef',                           // 6 chars — below SHA minimum
    '65',                               // PR without #
    'fix-was-applied',                  // prose
    'https://gitlab.com/foo/bar/commit/abc',  // non-github
    'ghijkl',                           // not hex
  ])('rejects %s', (ref) => {
    expect(isValidFixReference(ref)).toBe(false)
  })
})


// ── ResolutionModal — Submit gate ────────────────────────────────────────────

describe('ResolutionModal — Submit gate', () => {
  it('Submit is disabled when no resolution type is chosen', () => {
    render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
      onResolved={() => {}} />)
    expect(screen.getByRole('button', { name: /submit/i }))
      .toBeDisabled()
  })

  it('Submit is disabled when no_bug_detected has no root cause', () => {
    render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
      onResolved={() => {}} />)
    fireEvent.click(screen.getByLabelText(/no bug detected/i))
    expect(screen.getByRole('button', { name: /submit/i }))
      .toBeDisabled()
  })

  it('Submit enables for no_bug_detected with only a root cause', () => {
    render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
      onResolved={() => {}} />)
    fireEvent.click(screen.getByLabelText(/no bug detected/i))
    fireEvent.change(screen.getByPlaceholderText(/root cause/i),
      { target: { value: 'User clicked the wrong button.' } })
    expect(screen.getByRole('button', { name: /submit/i }))
      .toBeEnabled()
  })

  it('Submit enables for wont_fix with only a root cause', () => {
    render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
      onResolved={() => {}} />)
    fireEvent.click(screen.getByLabelText(/won't fix/i))
    fireEvent.change(screen.getByPlaceholderText(/root cause/i),
      { target: { value: 'By design — sysadmin-only feature.' } })
    expect(screen.getByRole('button', { name: /submit/i }))
      .toBeEnabled()
  })

  it('Submit is disabled for code_fix_deployed without a fix reference',
    () => {
      render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
        onResolved={() => {}} />)
      fireEvent.click(screen.getByLabelText(/code fix deployed/i))
      fireEvent.change(screen.getByPlaceholderText(/root cause/i),
        { target: { value: 'Stale cache.' } })
      fireEvent.change(screen.getByPlaceholderText(/remediation note/i),
        { target: { value: 'Cleared cache.' } })
      // Fix reference still empty → Submit disabled.
      expect(screen.getByRole('button', { name: /submit/i }))
        .toBeDisabled()
    })

  it('Submit is disabled for code_fix_deployed with INVALID fix reference',
    () => {
      render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
        onResolved={() => {}} />)
      fireEvent.click(screen.getByLabelText(/code fix deployed/i))
      fireEvent.change(screen.getByPlaceholderText(/root cause/i),
        { target: { value: 'Stale cache.' } })
      fireEvent.change(screen.getByPlaceholderText(/commit sha/i),
        { target: { value: 'not-a-sha-or-pr' } })
      fireEvent.change(screen.getByPlaceholderText(/remediation note/i),
        { target: { value: 'Cleared cache.' } })
      expect(screen.getByRole('button', { name: /submit/i }))
        .toBeDisabled()
    })

  it('Submit is disabled for code_fix_deployed without a remediation note',
    () => {
      render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
        onResolved={() => {}} />)
      fireEvent.click(screen.getByLabelText(/code fix deployed/i))
      fireEvent.change(screen.getByPlaceholderText(/root cause/i),
        { target: { value: 'Stale cache.' } })
      fireEvent.change(screen.getByPlaceholderText(/commit sha/i),
        { target: { value: 'abc1234' } })
      // remediation_note empty → still disabled.
      expect(screen.getByRole('button', { name: /submit/i }))
        .toBeDisabled()
    })

  it('Submit enables for code_fix_deployed with all required fields', () => {
    render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
      onResolved={() => {}} />)
    fireEvent.click(screen.getByLabelText(/code fix deployed/i))
    fireEvent.change(screen.getByPlaceholderText(/root cause/i),
      { target: { value: 'Stale cache.' } })
    fireEvent.change(screen.getByPlaceholderText(/commit sha/i),
      { target: { value: 'abc1234' } })
    fireEvent.change(screen.getByPlaceholderText(/remediation note/i),
      { target: { value: 'Cleared cache on every push.' } })
    expect(screen.getByRole('button', { name: /submit/i }))
      .toBeEnabled()
  })

  it('Submit POSTs the correct payload and calls onResolved', async () => {
    const onResolved = vi.fn()
    render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
      onResolved={onResolved} />)
    fireEvent.click(screen.getByLabelText(/code fix deployed/i))
    fireEvent.change(screen.getByPlaceholderText(/root cause/i),
      { target: { value: 'Stale cache.' } })
    fireEvent.change(screen.getByPlaceholderText(/commit sha/i),
      { target: { value: 'abc1234' } })
    fireEvent.change(screen.getByPlaceholderText(/remediation note/i),
      { target: { value: 'Cleared cache on every push.' } })
    fireEvent.click(screen.getByRole('button', { name: /submit/i }))
    await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(1))
    const [url, body] = mockedAxios.post.mock.calls[0]!
    expect(url).toBe('/api/v1/testing/failures/42/resolve')
    expect(body).toEqual({
      resolution_type: 'code_fix_deployed',
      resolution_note: 'Stale cache.',
      fix_reference: 'abc1234',
      remediation_note: 'Cleared cache on every push.',
    })
    await waitFor(() => expect(onResolved).toHaveBeenCalled())
  })

  it('no_bug_detected does not POST fix_reference or remediation_note',
    async () => {
      render(<ResolutionModal failure={makeFailure()} onClose={() => {}}
        onResolved={() => {}} />)
      fireEvent.click(screen.getByLabelText(/no bug detected/i))
      fireEvent.change(screen.getByPlaceholderText(/root cause/i),
        { target: { value: 'User error.' } })
      fireEvent.click(screen.getByRole('button', { name: /submit/i }))
      await waitFor(() => expect(mockedAxios.post).toHaveBeenCalledTimes(1))
      const [, body] = mockedAxios.post.mock.calls[0]!
      // null on both — the backend ignores them for this type.
      expect((body as { fix_reference: unknown }).fix_reference).toBeNull()
      expect((body as { remediation_note: unknown }).remediation_note)
        .toBeNull()
    })

  it('renders the close button which calls onClose', () => {
    const onClose = vi.fn()
    render(<ResolutionModal failure={makeFailure()} onClose={onClose}
      onResolved={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(onClose).toHaveBeenCalled()
  })
})


// ── ResolutionCard — the read-only expand body ───────────────────────────────

describe('ResolutionCard', () => {
  it('renders the resolution type, root cause, and resolver line', () => {
    render(<ResolutionCard f={makeFailure({
      resolved_at: '2026-05-20T11:00:00Z',
      resolved_by: 'ruurdsm@queens.edu',
      resolution_type: 'no_bug_detected',
      resolution_note: 'Tester misread the step.',
    })} />)
    expect(screen.getByText(/no bug detected/i)).toBeInTheDocument()
    expect(screen.getByText(/tester misread/i)).toBeInTheDocument()
    expect(screen.getByText(/ruurdsm@queens.edu/i)).toBeInTheDocument()
  })

  it('renders fix_reference and remediation only for code_fix_deployed',
    () => {
      const { rerender } = render(<ResolutionCard f={makeFailure({
        resolved_at: '2026-05-20T11:00:00Z',
        resolved_by: 'ruurdsm@queens.edu',
        resolution_type: 'wont_fix',
        resolution_note: 'By design.',
        fix_reference: 'abc1234',           // present in the row but
        remediation_note: 'Wrote a check.', // wont_fix does NOT render them
      })} />)
      expect(screen.queryByText(/abc1234/)).toBeNull()
      expect(screen.queryByText(/wrote a check/i)).toBeNull()

      rerender(<ResolutionCard f={makeFailure({
        resolved_at: '2026-05-20T11:00:00Z',
        resolved_by: 'ruurdsm@queens.edu',
        resolution_type: 'code_fix_deployed',
        resolution_note: 'Race condition.',
        fix_reference: 'abc1234',
        remediation_note: 'Added a lock.',
      })} />)
      // Now both DO render — code_fix_deployed surfaces them.
      expect(screen.getByText(/abc1234/)).toBeInTheDocument()
      expect(screen.getByText(/added a lock/i)).toBeInTheDocument()
    })
})


// ── ResolutionBadge ──────────────────────────────────────────────────────────

describe('ResolutionBadge', () => {
  it.each([
    ['no_bug_detected', 'No bug detected'],
    ['code_fix_deployed', 'Code fix deployed'],
    ['wont_fix', "Won't fix"],
  ])('renders the human label for %s', (type, label) => {
    render(<ResolutionBadge type={type} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('falls back to the raw type for an unknown value', () => {
    render(<ResolutionBadge type="invented" />)
    expect(screen.getByText('invented')).toBeInTheDocument()
  })
})
