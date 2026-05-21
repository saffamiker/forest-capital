/**
 * test-notifications-resolved-failure.test.tsx — Migration 025
 * three-variant resolved-failure card.
 *
 * Pins the rendering of TestNotifications when a resolved_failures
 * notification arrives with each of the three resolution types:
 *
 *   no_bug_detected    — title "✅ not a bug", Re-test CTA, helper
 *                        "No code change was required" copy.
 *   code_fix_deployed  — title "✅ has been fixed", Re-test CTA,
 *                        renders the remediation + fix reference
 *                        (linkified).
 *   wont_fix           — title "🔒 has been closed", NO Re-test
 *                        CTA (the Close button is the only action),
 *                        "No re-test is required" copy.
 *
 * A regression that drops the CTA gate (Won't fix accidentally
 * shows Re-test) or the linkified fix reference (the reviewer cannot
 * click through to the commit) would land here.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('axios')

import axios from 'axios'
import { SessionProvider } from '../context/SessionContext'
import { AuthContext } from '../App'
import { TEST_SCRIPTS } from '../constants/testScripts'
import TestNotifications from '../components/TestNotifications'

const mockedAxios = vi.mocked(axios, true)

const AUTH = {
  session: { token: 't', email: 'tester@queens.edu', permissions: [] },
  isVerifying: false,
  login: vi.fn(),
  logout: vi.fn(),
}

// Pre-attest every step in every script so the new_tests notification
// (which outranks resolved_failures in the queue) doesn't fire. Without
// this the test sees "🧪 New test cases available" first and the
// resolved_failure card never reaches the screen.
function _fullyAttestedUnseen() {
  const scripts: Record<string, { attested_step_ids: string[] }> = {}
  for (const sc of TEST_SCRIPTS) {
    scripts[sc.id] = {
      attested_step_ids: sc.steps.map((s) => s.id),
    }
  }
  return { scripts }
}


function renderWith(notifResponse: Record<string, unknown>) {
  mockedAxios.get = vi.fn().mockImplementation((url: string) => {
    if (url === '/api/v1/testing/unseen') {
      return Promise.resolve({ data: _fullyAttestedUnseen() })
    }
    if (url === '/api/v1/testing/notifications') {
      return Promise.resolve({ data: notifResponse })
    }
    // Triage and audit endpoints are sysadmin-gated — reject so the
    // try/catch arms in TestNotifications swallow them silently.
    return Promise.reject({ response: { status: 403 } })
  })
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={AUTH}>
        <SessionProvider>
          <TestNotifications />
        </SessionProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}


beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})


describe('TestNotifications — resolved_failures three-variant card', () => {
  // Use a script_id / step_id that won't resolve through getTestScript;
  // the renderer falls back to step_id text, which is what we assert.

  it('renders the no_bug_detected variant with helper copy and CTA', async () => {
    renderWith({
      resolved_failures: [{
        script_id: 'all_testers_v1', step_id: 'unknown_step_xyz',
        resolution_note: 'User clicked the wrong button.',
        resolved_at: '2026-05-20T11:00:00Z',
        resolution_type: 'no_bug_detected',
        fix_reference: null,
        remediation_note: null,
      }],
      responded_feedback: [],
      retest_requested: [],
    })
    await waitFor(() =>
      expect(screen.getByText(/not a bug/i)).toBeInTheDocument())
    expect(screen.getByText(/user clicked the wrong button/i))
      .toBeInTheDocument()
    expect(screen.getByText(/no code change was required/i))
      .toBeInTheDocument()
    expect(screen.getByRole('button', { name: /re-test this step/i }))
      .toBeInTheDocument()
  })

  it('renders the code_fix_deployed variant with remediation, fix link, and CTA',
    async () => {
      renderWith({
        resolved_failures: [{
          script_id: 'all_testers_v1', step_id: 'unknown_step_xyz',
          resolution_note: 'Race condition under load.',
          resolved_at: '2026-05-20T11:00:00Z',
          resolution_type: 'code_fix_deployed',
          fix_reference: 'abc1234',
          remediation_note: 'Added a lock around the contended path.',
        }],
        responded_feedback: [],
        retest_requested: [],
      })
      await waitFor(() =>
        expect(screen.getByText(/has been fixed/i)).toBeInTheDocument())
      expect(screen.getByText(/race condition under load/i))
        .toBeInTheDocument()
      expect(screen.getByText(/added a lock/i)).toBeInTheDocument()
      // Fix reference renders as a clickable GitHub link.
      const link = screen.getByText('abc1234')
      expect(link.closest('a')).toHaveAttribute('href',
        'https://github.com/saffamiker/forest-capital/commit/abc1234')
      expect(screen.getByRole('button', { name: /re-test this step/i }))
        .toBeInTheDocument()
    })

  it('renders the wont_fix variant WITHOUT a Re-test CTA', async () => {
    renderWith({
      resolved_failures: [{
        script_id: 'all_testers_v1', step_id: 'unknown_step_xyz',
        resolution_note: 'By design — sysadmin-only endpoint.',
        resolved_at: '2026-05-20T11:00:00Z',
        resolution_type: 'wont_fix',
        fix_reference: null,
        remediation_note: null,
      }],
      responded_feedback: [],
      retest_requested: [],
    })
    await waitFor(() =>
      expect(screen.getByText(/has been closed/i)).toBeInTheDocument())
    expect(screen.getByText(/by design — sysadmin/i)).toBeInTheDocument()
    expect(screen.getByText(/no re-test is required/i)).toBeInTheDocument()
    // The CRITICAL contract: no Re-test CTA for Won't fix.
    expect(screen.queryByRole('button', { name: /re-test this step/i }))
      .toBeNull()
    // The Close button replaces the usual Later button when there is
    // no action — informational dismissal.
    expect(screen.getByRole('button', { name: /close/i }))
      .toBeInTheDocument()
  })

  it('falls back to the legacy CTA for a row with null resolution_type',
    async () => {
      // Legacy: a row resolved BEFORE migration 025 carries null
      // resolution_type. The frontend defaults to the original
      // "please re-run" UX so the tester is not stranded.
      renderWith({
        resolved_failures: [{
          script_id: 'all_testers_v1', step_id: 'unknown_step_xyz',
          resolution_note: 'Legacy resolution.',
          resolved_at: '2026-05-20T11:00:00Z',
          resolution_type: null,
          fix_reference: null,
          remediation_note: null,
        }],
        responded_feedback: [],
        retest_requested: [],
      })
      await waitFor(() =>
        expect(screen.getByText(/has been resolved/i)).toBeInTheDocument())
      expect(screen.getByRole('button', { name: /re-test this step/i }))
        .toBeInTheDocument()
    })

  it('linkifies a PR-style fix reference to the GitHub PR URL', async () => {
    renderWith({
      resolved_failures: [{
        script_id: 'all_testers_v1', step_id: 'unknown_step_xyz',
        resolution_note: 'Stale cache.',
        resolved_at: '2026-05-20T11:00:00Z',
        resolution_type: 'code_fix_deployed',
        fix_reference: '#65',
        remediation_note: 'See PR.',
      }],
      responded_feedback: [],
      retest_requested: [],
    })
    await waitFor(() => {
      const link = screen.getByText('#65')
      expect(link.closest('a')).toHaveAttribute('href',
        'https://github.com/saffamiker/forest-capital/pull/65')
    })
  })
})
