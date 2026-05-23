/**
 * peer-review.test.tsx — Item 7 (May 23 2026) frontend contract.
 *
 * Pins:
 *   1. The page renders the two tabs and switches between them.
 *   2. Tab A — file input + display-name input + run button.
 *      Run button disabled until a file is picked.
 *   3. Tab B — has its own run button (no file upload).
 *   4. Verdict / loading / error sub-components render off the
 *      Zustand store so a remount picks up the previous state.
 *   5. The store's start/append/finish/fail mutators sequence
 *      a verdict the way the SSE consumer expects.
 *
 * NOTE: end-to-end SSE wire decoding is exercised in the backend
 * tests; the frontend tests mock the store state and assert the
 * rendering / interaction contract.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import PeerReview from '../pages/PeerReview'
import { usePeerReviewStore } from '../stores/peerReviewStore'


beforeEach(() => {
  usePeerReviewStore.getState().resetPeerReview()
  usePeerReviewStore.getState().resetDefensePrep()
})

afterEach(() => {
  vi.clearAllMocks()
  usePeerReviewStore.getState().resetPeerReview()
  usePeerReviewStore.getState().resetDefensePrep()
})


// ── Page shell + tab switching ─────────────────────────────────────────────


describe('PeerReview page', () => {

  it('renders the two tabs', () => {
    render(<PeerReview />)
    expect(screen.getByTestId('peer-review-tab')).toBeTruthy()
    expect(screen.getByTestId('defense-prep-tab')).toBeTruthy()
  })

  it('opens the Peer Review Assistant tab by default', () => {
    render(<PeerReview />)
    // File input only renders on Tab A.
    expect(screen.getByTestId('peer-review-file-input')).toBeTruthy()
    // Tab B's run button is not in the DOM until the tab opens.
    expect(screen.queryByTestId('defense-prep-run')).toBeNull()
  })

  it('switches to the Thesis Defense Prep tab on click', () => {
    render(<PeerReview />)
    fireEvent.click(screen.getByTestId('defense-prep-tab'))
    expect(screen.getByTestId('defense-prep-run')).toBeTruthy()
    // The file input from Tab A is now off-DOM.
    expect(screen.queryByTestId('peer-review-file-input')).toBeNull()
  })
})


// ── Tab A — Peer Review Assistant ──────────────────────────────────────────


describe('Peer Review Assistant tab', () => {

  it('disables the run button until a file is picked', () => {
    render(<PeerReview />)
    const run = screen.getByTestId('peer-review-run') as HTMLButtonElement
    expect(run.disabled).toBe(true)
  })

  it('enables the run button once a file is picked', () => {
    render(<PeerReview />)
    const file = new File(["# Hello"], "paper.md",
                           { type: "text/markdown" })
    const input = screen.getByTestId(
      'peer-review-file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    const run = screen.getByTestId('peer-review-run') as HTMLButtonElement
    expect(run.disabled).toBe(false)
  })

  it('defaults the display name to the filename stem on file pick', () => {
    render(<PeerReview />)
    const file = new File(["x"], "OtherTeamMidpoint.pdf",
                           { type: "application/pdf" })
    const input = screen.getByTestId(
      'peer-review-file-input') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })
    const nameInput = screen.getByTestId(
      'peer-review-name-input') as HTMLInputElement
    expect(nameInput.value).toBe('OtherTeamMidpoint')
  })

  it('renders the verdict card when the store has a verdict', () => {
    // Seed the store as if a verdict streamed in already.
    usePeerReviewStore.setState((s) => ({
      peerReview: {
        ...s.peerReview,
        verdict: '## Review\nLooks good.',
        submissionMeta: { name: 'Other Team', char_count: 1234 },
        loading: false,
        startedAt: Date.now(),
        completedAt: Date.now(),
      },
    }))
    render(<PeerReview />)
    expect(screen.getByTestId('peer-review-verdict')).toBeTruthy()
    expect(screen.getByText(/Review of Other Team/)).toBeTruthy()
    expect(screen.getByText(/1,234 characters extracted/)).toBeTruthy()
  })

  it('renders the loading card mid-stream', () => {
    usePeerReviewStore.getState().startPeerReview()
    render(<PeerReview />)
    expect(screen.getByTestId('peer-review-loading')).toBeTruthy()
  })

  it('renders the error card on failure', () => {
    usePeerReviewStore.getState().failPeerReview(
      'Uploaded file exceeds 2 MB.')
    render(<PeerReview />)
    const err = screen.getByTestId('peer-review-error')
    expect(err).toBeTruthy()
    expect(err.textContent).toContain('Uploaded file exceeds 2 MB.')
  })
})


// ── Tab B — Thesis Defense Prep ────────────────────────────────────────────


describe('Thesis Defense Prep tab', () => {

  it('renders a run button (no file upload required)', () => {
    render(<PeerReview />)
    fireEvent.click(screen.getByTestId('defense-prep-tab'))
    expect(screen.getByTestId('defense-prep-run')).toBeTruthy()
    expect(screen.queryByTestId('peer-review-file-input')).toBeNull()
  })

  it('renders the verdict card when the defense-prep slot has content', () => {
    usePeerReviewStore.setState((s) => ({
      defensePrep: {
        ...s.defensePrep,
        verdict: '## Q&A\nTechnical questions go here.',
        draftMeta: {
          title: 'Midpoint draft v3',
          word_count: 1200,
          updated_at: '2026-05-23T10:00:00Z',
        },
        loading: false,
        startedAt: Date.now(),
        completedAt: Date.now(),
      },
    }))
    render(<PeerReview />)
    fireEvent.click(screen.getByTestId('defense-prep-tab'))
    expect(screen.getByTestId('peer-review-verdict')).toBeTruthy()
    expect(screen.getByText(/Q&A prep — Midpoint draft v3/)).toBeTruthy()
    expect(screen.getByText(/1,200 words/)).toBeTruthy()
  })

  it('renders the loading card while running', () => {
    usePeerReviewStore.getState().startDefensePrep()
    render(<PeerReview />)
    fireEvent.click(screen.getByTestId('defense-prep-tab'))
    expect(screen.getByTestId('peer-review-loading')).toBeTruthy()
  })

  it('renders the error card on failure', () => {
    usePeerReviewStore.getState().failDefensePrep(
      'No midpoint paper draft found.')
    render(<PeerReview />)
    fireEvent.click(screen.getByTestId('defense-prep-tab'))
    const err = screen.getByTestId('peer-review-error')
    expect(err.textContent).toContain('No midpoint paper draft found.')
  })
})


// ── Store sequencing ───────────────────────────────────────────────────────


describe('peerReviewStore mutator sequence', () => {

  it('threads a complete peer-review run through the store', () => {
    const s = usePeerReviewStore.getState()
    s.startPeerReview()
    s.setPeerReviewMeta({ name: 'Other Team', char_count: 100 })
    s.appendPeerReviewChunk('chunk one ')
    s.appendPeerReviewChunk('chunk two')
    s.finishPeerReview()
    const slot = usePeerReviewStore.getState().peerReview
    expect(slot.loading).toBe(false)
    expect(slot.verdict).toBe('chunk one chunk two')
    expect(slot.submissionMeta?.name).toBe('Other Team')
    expect(slot.completedAt).not.toBeNull()
  })

  it('preserves chunks accumulated before a failure', () => {
    const s = usePeerReviewStore.getState()
    s.startPeerReview()
    s.appendPeerReviewChunk('partial output before error')
    s.failPeerReview('something broke')
    const slot = usePeerReviewStore.getState().peerReview
    expect(slot.verdict).toBe('partial output before error')
    expect(slot.error).toBe('something broke')
    expect(slot.loading).toBe(false)
  })

  it('threads a complete defense-prep run through the store', () => {
    const s = usePeerReviewStore.getState()
    s.startDefensePrep()
    s.setDefensePrepMeta({
      title: 'Midpoint v3', word_count: 1000,
      updated_at: '2026-05-23',
    })
    s.appendDefensePrepChunk('Q1: ')
    s.appendDefensePrepChunk('test.')
    s.finishDefensePrep()
    const slot = usePeerReviewStore.getState().defensePrep
    expect(slot.verdict).toBe('Q1: test.')
    expect(slot.draftMeta?.title).toBe('Midpoint v3')
    expect(slot.loading).toBe(false)
  })

  it('resets cleanly via the reset mutators', () => {
    const s = usePeerReviewStore.getState()
    s.startPeerReview()
    s.appendPeerReviewChunk('x')
    s.finishPeerReview()
    s.resetPeerReview()
    expect(
      usePeerReviewStore.getState().peerReview.verdict).toBe('')
    expect(
      usePeerReviewStore.getState().peerReview.startedAt).toBeNull()
  })
})
