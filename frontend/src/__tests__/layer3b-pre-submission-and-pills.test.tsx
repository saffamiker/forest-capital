/**
 * layer3b-pre-submission-and-pills.test.tsx -- Layer 3b (June 21 2026).
 *
 * Frontend coverage for the Layer 3b UI surfaces:
 *
 *   PreSubmissionCheckPanel
 *     - The "Verify All for Submission" button renders.
 *     - Clicking it fires POST /api/v1/export/verify-all and renders
 *       the inline verdict tile:
 *         overall=ready          -> green panel
 *         overall=needs_attention -> amber panel + per-doc warnings
 *         overall=blocked        -> red panel + per-doc errors
 *
 *   DocumentGenerationPanel export-verification pills
 *     - The pill per card reflects the readiness store's
 *       export_verification[document_type] value.
 *
 *   BriefWorkflowModal checklist item 1
 *     - Item 1 was rewritten to "Pre-Submission Check shows green ...".
 *
 *   Reports page
 *     - The PreSubmissionCheckPanel renders above DocumentGenerationPanel.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('axios', () => {
  const mod = {
    post: vi.fn(), get: vi.fn(), delete: vi.fn(),
    isAxiosError: vi.fn(() => false),
  }
  return { default: mod }
})

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import type { ReactNode } from 'react'

import { AuthContext } from '../App'
import {
  useReportReadinessStore, type ReportReadiness,
} from '../stores/reportReadinessStore'
import { PreSubmissionCheckPanel } from '../components/PreSubmissionCheckPanel'
import DocumentGenerationPanel from '../components/DocumentGenerationPanel'
import { BriefWorkflowModal } from '../components/BriefWorkflowModal'
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


const READINESS_WITH_VERIFICATION: ReportReadiness = {
  is_ready: true,
  blocking_count: 0,
  statistical: { unreviewed_warnings: [], unreviewed_failures: [] },
  methodology: { unresolved_warnings: [], unresolved_failures: [] },
  export_verification: {
    executive_brief: 'verified',
    presentation_deck: 'warned',
    analytical_appendix: 'failed',
  },
  checked_at: '2026-06-21T00:00:00+00:00',
}


beforeEach(() => {
  useReportReadinessStore.setState({
    readiness: null, loading: false, fetchedAt: null,
  })
  __resetGenerationJobs()
  mockedAxios.get = vi.fn().mockResolvedValue({
    data: READINESS_WITH_VERIFICATION,
  })
  mockedAxios.post = vi.fn()
  const isAxiosErrorStub = (value: unknown): boolean => (
    value !== null && typeof value === 'object'
    && (value as { isAxiosError?: boolean }).isAxiosError === true
  )
  mockedAxios.isAxiosError = isAxiosErrorStub as typeof axios.isAxiosError
})


// ── PreSubmissionCheckPanel ───────────────────────────────────────────────


describe('PreSubmissionCheckPanel', () => {

  it('renders the Verify All for Submission button', () => {
    render(<PreSubmissionCheckPanel />)
    const btn = screen.getByTestId('verify-all-for-submission-button')
    expect(btn).toBeInTheDocument()
    expect(btn.textContent).toMatch(/Verify All for Submission/i)
  })

  it('renders the green verdict tile when overall=ready', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: {
        overall: 'ready',
        submission_recommendation:
          'All three deliverables verified against cache abc12345.',
        brief: { status: 'verified', errors: [], warnings: [] },
        deck: { status: 'verified', errors: [], warnings: [] },
        appendix: { status: 'verified', errors: [], warnings: [] },
        cross_deliverable: { passed: true, flags: [] },
      },
    })
    render(<PreSubmissionCheckPanel />)
    fireEvent.click(
      screen.getByTestId('verify-all-for-submission-button'))
    await waitFor(() => {
      expect(screen.getByTestId('verify-all-verdict-ready'))
        .toBeInTheDocument()
    })
    expect(screen.getByTestId('verify-all-verdict-ready').textContent)
      .toMatch(/All deliverables verified/i)
    expect(screen.getByTestId('verify-all-verdict-ready').textContent)
      .toMatch(/abc12345/)
  })

  it('renders the amber verdict tile + per-doc warnings when '
    + 'overall=needs_attention', async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: {
          overall: 'needs_attention',
          submission_recommendation:
            'All deliverables generated; warnings present.',
          brief: {
            status: 'warned',
            errors: [],
            warnings: [{ message: 'stale data_hash on brief draft' }],
          },
          deck: { status: 'verified', errors: [], warnings: [] },
          appendix: { status: 'verified', errors: [], warnings: [] },
          cross_deliverable: { passed: true, flags: [] },
        },
      })
      render(<PreSubmissionCheckPanel />)
      fireEvent.click(
        screen.getByTestId('verify-all-for-submission-button'))
      await waitFor(() => {
        expect(screen.getByTestId('verify-all-verdict-needs-attention'))
          .toBeInTheDocument()
      })
      expect(
        screen.getByTestId('verify-all-verdict-needs-attention').textContent,
      ).toMatch(/Review recommended/i)
      // Per-doc detail block carries the warning text.
      expect(screen.getByTestId('verify-all-doc-brief-detail').textContent)
        .toMatch(/stale data_hash/)
    })

  it('renders the red verdict tile + per-doc errors when overall=blocked',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: {
          overall: 'blocked',
          submission_recommendation:
            'Verification errors found in brief.',
          brief: {
            status: 'failed',
            errors: [{ message: 'value 0.86 missing from export' }],
            warnings: [],
          },
          deck: { status: 'verified', errors: [], warnings: [] },
          appendix: { status: 'verified', errors: [], warnings: [] },
          cross_deliverable: { passed: true, flags: [] },
        },
      })
      render(<PreSubmissionCheckPanel />)
      fireEvent.click(
        screen.getByTestId('verify-all-for-submission-button'))
      await waitFor(() => {
        expect(screen.getByTestId('verify-all-verdict-blocked'))
          .toBeInTheDocument()
      })
      expect(screen.getByTestId('verify-all-verdict-blocked').textContent)
        .toMatch(/Issues found -- do not submit yet/i)
      expect(screen.getByTestId('verify-all-doc-brief-detail').textContent)
        .toMatch(/value 0\.86 missing/)
    })

  it('surfaces not-generated notices when any document is missing',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: {
          overall: 'blocked',
          submission_recommendation:
            'Generate brief before submitting.',
          brief: { status: 'not_generated', errors: [], warnings: [] },
          deck: { status: 'verified', errors: [], warnings: [] },
          appendix: { status: 'verified', errors: [], warnings: [] },
          cross_deliverable: { passed: true, flags: [] },
        },
      })
      render(<PreSubmissionCheckPanel />)
      fireEvent.click(
        screen.getByTestId('verify-all-for-submission-button'))
      await waitFor(() => {
        expect(screen.getByTestId('verify-all-verdict-blocked'))
          .toBeInTheDocument()
      })
      expect(screen.getByTestId('verify-all-not-generated-brief'))
        .toBeInTheDocument()
      expect(screen.getByTestId('verify-all-not-generated-brief').textContent)
        .toMatch(/Executive Brief has not been generated/)
    })

  it('shows the loading state while the POST is in flight', async () => {
    let resolve: (v: unknown) => void = () => undefined
    mockedAxios.post = vi.fn().mockReturnValue(
      new Promise((res) => { resolve = res }))
    render(<PreSubmissionCheckPanel />)
    fireEvent.click(
      screen.getByTestId('verify-all-for-submission-button'))
    // Mid-flight: button label flips to the loading copy.
    expect(
      screen.getByTestId('verify-all-for-submission-button').textContent,
    ).toMatch(/Verifying all deliverables against cache/i)
    // Resolve so cleanup doesn't leak.
    resolve({
      data: {
        overall: 'ready', submission_recommendation: 'ok',
        brief: { status: 'verified' },
        deck: { status: 'verified' },
        appendix: { status: 'verified' },
      },
    })
    await waitFor(() => {
      expect(screen.getByTestId('verify-all-verdict-ready'))
        .toBeInTheDocument()
    })
  })
})


// ── DocumentGenerationPanel: export verification pills ────────────────────


describe('DocumentGenerationPanel -- export verification pills', () => {

  it('renders the verified pill on a card whose export_verification '
    + 'status is verified', async () => {
      renderWithAuth(<DocumentGenerationPanel />)
      // Wait for the readiness fetch to land so the pill mounts.
      await waitFor(() => {
        expect(
          useReportReadinessStore.getState().readiness?.export_verification,
        ).toBeTruthy()
      })
      await waitFor(() => {
        expect(
          screen.getByTestId('export-verification-brief')
            .querySelector(
              '[data-testid="export-verification-pill-verified"]'),
        ).toBeInTheDocument()
      })
    })

  it('renders the warned pill for presentation_deck', async () => {
    renderWithAuth(<DocumentGenerationPanel />)
    await waitFor(() => {
      expect(
        useReportReadinessStore.getState().readiness?.export_verification,
      ).toBeTruthy()
    })
    await waitFor(() => {
      expect(
        screen.getByTestId('export-verification-deck')
          .querySelector(
            '[data-testid="export-verification-pill-warned"]'),
      ).toBeInTheDocument()
    })
  })

  it('renders the failed pill for analytical_appendix', async () => {
    renderWithAuth(<DocumentGenerationPanel />)
    await waitFor(() => {
      expect(
        useReportReadinessStore.getState().readiness?.export_verification,
      ).toBeTruthy()
    })
    await waitFor(() => {
      expect(
        screen.getByTestId('export-verification-appendix')
          .querySelector(
            '[data-testid="export-verification-pill-failed"]'),
      ).toBeInTheDocument()
    })
  })

  it('renders the not-yet-exported state when status is not_exported',
    async () => {
      // Override readiness so all three documents show not_exported.
      useReportReadinessStore.setState({
        readiness: {
          ...READINESS_WITH_VERIFICATION,
          export_verification: {
            executive_brief: 'not_exported',
            presentation_deck: 'not_exported',
            analytical_appendix: 'not_exported',
          },
        },
        loading: false,
        fetchedAt: new Date(),
      })
      // Make the fetch a no-op so the store state survives.
      mockedAxios.get = vi.fn().mockResolvedValue({
        data: useReportReadinessStore.getState().readiness })
      renderWithAuth(<DocumentGenerationPanel />)
      await waitFor(() => {
        expect(
          screen.getByTestId('export-verification-brief')
            .querySelector(
              '[data-testid="export-verification-pill-not-exported"]'),
        ).toBeInTheDocument()
      })
    })
})


// ── BriefWorkflowModal checklist item 1 ───────────────────────────────────


describe('BriefWorkflowModal -- Layer 3b checklist item 1', () => {

  it('item 1 is the Pre-Submission Check copy', () => {
    render(<BriefWorkflowModal open={true} onClose={() => undefined} />)
    const item0 = screen.getByTestId('brief-checklist-item-0')
    expect(item0.textContent).toMatch(
      /Pre-Submission Check shows green/i)
    // Defence against a future regression that revives the old item.
    expect(item0.textContent).not.toMatch(/Audit banner shows clean/i)
  })

  it('helper Step 12 names the Verify All button', () => {
    render(<BriefWorkflowModal open={true} onClose={() => undefined} />)
    // The new Step 12 added at the bottom of the Exporting section
    // points at the Reports-page Verify All button.
    const body = document.body.textContent ?? ''
    expect(body).toMatch(/Verify All for Submission/)
  })
})
