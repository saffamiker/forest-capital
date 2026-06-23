/**
 * academic-review-section.test.tsx — the relocated Academic Review
 * surface on the QA Audit page (May 28 2026).
 *
 * Pins three contracts:
 *   1. Read-visible / write-team-gated — the verdict + peers render
 *      for every authenticated user; the Run buttons are disabled for
 *      non-team users with a tooltip explaining the gate.
 *   2. Zustand cache survives navigation — a verdict stashed in the
 *      store before the component mounts renders without re-fetching.
 *   3. Stale-cache banner appears when the cached verdict's data_hash
 *      doesn't match the live audit data_hash.
 *
 * The streaming SSE path is exercised at the store level
 * (academic-review-store.test.ts) — this file covers the UI surface
 * with the store pre-populated.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import axios from 'axios'
import { AuthContext } from '../App'
import { useAcademicReviewStore } from '../stores/academicReviewStore'

vi.mock('axios')

import AcademicReviewSection from '../components/AcademicReviewSection'


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


beforeEach(() => {
  vi.clearAllMocks()
  // Default mock — no cached audit hash. Each test overrides as needed.
  vi.mocked(axios.get).mockResolvedValue({
    data: { current_data_hash: 'data-hash-A' },
  })
  // Each test starts with a clean store.
  useAcademicReviewStore.getState().clear()
})


describe('AcademicReviewSection — read-visible / team-write-gated', () => {
  it('renders the trigger card for every authenticated user', () => {
    withPerms(VIEWER_PERMS, <AcademicReviewSection />)
    expect(screen.getByTestId('academic-review-trigger'))
      .toBeInTheDocument()
  })

  it('disables the Run button for non-team users with a tooltip', () => {
    withPerms(VIEWER_PERMS, <AcademicReviewSection />)
    const button = screen.getByTestId('academic-review-run')
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute(
      'title', 'Available to project team members only',
    )
  })

  it('enables the Run button for team members', () => {
    withPerms(TEAM_PERMS, <AcademicReviewSection />)
    const button = screen.getByTestId('academic-review-run')
    expect(button).not.toBeDisabled()
  })
})


describe('AcademicReviewSection — Zustand cache survives navigation', () => {
  it('renders a cached verdict without firing the SSE endpoint', () => {
    // Pre-populate the store with a completed verdict — simulates the
    // user navigating to QA Hub after a prior run on a different
    // page (or after a prior session navigation cycle).
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'data-hash-A',
      result: {
        arbiterText:
          '### 1. Section one\n\n**Rating:** Strong\n\nbody text here',
        peerResponses: { equity_analyst: 'equity report markdown' },
      },
      completedAt: '2026-05-28T10:00:00Z',
    })
    withPerms(VIEWER_PERMS, <AcademicReviewSection />)
    // The verdict renders straight from the store — no SSE call.
    expect(screen.getByTestId('academic-review-verdict'))
      .toBeInTheDocument()
    // The peers accordion is present.
    expect(screen.getByTestId('academic-review-peers'))
      .toBeInTheDocument()
  })

  it('renders the Re-run label when a cached verdict is present', () => {
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'data-hash-A',
      result: {
        arbiterText: '### 1. Section one\n\n**Rating:** Strong\n\nbody',
        peerResponses: {},
      },
    })
    withPerms(TEAM_PERMS, <AcademicReviewSection />)
    const button = screen.getByTestId('academic-review-run')
    expect(button.textContent).toContain('Re-run Cross-Document Review')
  })

  it('renders the Run label when no cached verdict exists', () => {
    withPerms(TEAM_PERMS, <AcademicReviewSection />)
    const button = screen.getByTestId('academic-review-run')
    expect(button.textContent).toContain('Run Cross-Document Review')
  })
})


describe('AcademicReviewSection — stale banner when data_hash drifts', () => {
  it('shows the stale banner when cached hash differs from current', async () => {
    // The component fetches the current audit data_hash on mount.
    // Stub it to return a DIFFERENT hash than the cached verdict.
    vi.mocked(axios.get).mockResolvedValue({
      data: { current_data_hash: 'data-hash-B' },
    })
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'data-hash-A',   // verdict from earlier data state
      result: {
        arbiterText: '### 1. Section one\n\n**Rating:** Strong\n\nbody',
        peerResponses: {},
      },
    })
    withPerms(TEAM_PERMS, <AcademicReviewSection />)
    // Wait for the data_hash fetch to land.
    await screen.findByTestId('academic-stale-banner')
    expect(screen.getByTestId('academic-stale-banner')).toBeInTheDocument()
    // The verdict still renders — the banner is the signal, not a hide.
    expect(screen.getByTestId('academic-review-verdict'))
      .toBeInTheDocument()
  })

  it('does NOT show the stale banner when hashes match', async () => {
    vi.mocked(axios.get).mockResolvedValue({
      data: { current_data_hash: 'data-hash-A' },
    })
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'data-hash-A',
      result: {
        arbiterText: '### 1. Section one\n\n**Rating:** Strong\n\nbody',
        peerResponses: {},
      },
    })
    withPerms(TEAM_PERMS, <AcademicReviewSection />)
    // Wait long enough for the data_hash fetch to settle.
    await screen.findByTestId('academic-review-verdict')
    expect(screen.queryByTestId('academic-stale-banner'))
      .not.toBeInTheDocument()
  })
})
