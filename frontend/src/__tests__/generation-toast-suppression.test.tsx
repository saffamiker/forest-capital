/**
 * generation-toast-suppression.test.tsx -- June 27 2026.
 *
 * BUG 2 pin: when a job poll returns "Generation job is no longer
 * available" (a 404/410 from the backend) but a current draft
 * exists for the same document_type, the GenerationToast must
 * suppress the red error chrome and show a neutral
 *   "Previous {label} generation attempt unavailable -- your
 *    current draft is still available below."
 * with an Open in Editor button targeting the current draft.
 *
 * Reserve the red error for cases where there is genuinely no
 * usable draft (cold caches / first-ever generation failure).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import GenerationToast from '../components/GenerationToast'
import {
  trackJob, __resetGenerationJobs,
} from '../lib/generationJobs'
import {
  setCurrentDraftPresence, __resetCurrentDraftPresence,
} from '../lib/currentDraftPresence'

// MemoryRouter at /some-page so the toast doesn't auto-suppress
// (it suppresses on /reports because the Reports panel shows the
// inline UX there).
function _renderToast() {
  return render(
    <MemoryRouter initialEntries={['/dashboard']}>
      <GenerationToast />
    </MemoryRouter>)
}

// Avoid the lazy refresh-from-drafts fetch hitting the network.
vi.mock('axios', () => ({
  default: { get: vi.fn().mockResolvedValue({ data: { drafts: [] } }) },
}))


describe('GenerationToast -- BUG 2 (stale-job + current-draft suppression)', () => {
  beforeEach(() => {
    __resetGenerationJobs()
    __resetCurrentDraftPresence()
  })

  afterEach(() => {
    __resetGenerationJobs()
    __resetCurrentDraftPresence()
  })

  it('shows the RED error chrome when no current draft exists', () => {
    trackJob({
      job_id: 'job-failed-1',
      document_type: 'analytical_appendix',
      status: 'failed',
      draft_id: null,
      download_url: null,
      error: 'Generation job is no longer available.',
      created_at: '2026-06-27T15:00:00Z',
    })
    _renderToast()
    expect(
      screen.getByTestId('generation-toast-failed-icon'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('generation-toast-failed-msg'),
    ).toHaveTextContent(/generation failed/i)
    // Rescue UI is hidden.
    expect(
      screen.queryByTestId('generation-toast-rescue-msg'),
    ).toBeNull()
    expect(
      screen.queryByTestId('generation-toast-rescue-open-editor'),
    ).toBeNull()
  })

  it('suppresses RED + shows neutral rescue chrome when a current draft exists', () => {
    // Seed a current draft for analytical_appendix BEFORE the
    // failed job lands. setCurrentDraftPresence is the same call
    // DocumentGenerationPanel makes after its /drafts fetch.
    setCurrentDraftPresence([
      {
        id: 99,
        document_type: 'analytical_appendix',
        is_current: true,
        data_hash: 'abc123',
        updated_at: '2026-06-27T14:00:00Z',
      },
    ])
    trackJob({
      job_id: 'job-failed-2',
      document_type: 'analytical_appendix',
      status: 'failed',
      draft_id: null,
      download_url: null,
      error: 'Generation job is no longer available.',
      created_at: '2026-06-27T15:00:00Z',
    })
    _renderToast()
    // Red chrome is GONE.
    expect(
      screen.queryByTestId('generation-toast-failed-icon'),
    ).toBeNull()
    expect(
      screen.queryByTestId('generation-toast-failed-msg'),
    ).toBeNull()
    // Neutral rescue chrome + Open in Editor are PRESENT.
    expect(
      screen.getByTestId('generation-toast-rescue-icon'),
    ).toBeInTheDocument()
    const msg = screen.getByTestId('generation-toast-rescue-msg')
    expect(msg).toHaveTextContent(
      /previous .* generation attempt unavailable/i)
    expect(msg).toHaveTextContent(
      /your current draft is still available/i)
    expect(
      screen.getByTestId('generation-toast-rescue-open-editor'),
    ).toBeInTheDocument()
  })

  it('rescues per-doc_type -- a different doc_type with no draft still shows red', () => {
    // Brief has a current draft, deck does NOT. The toast for the
    // failed deck job should show red (no rescue).
    setCurrentDraftPresence([
      {
        id: 88,
        document_type: 'executive_brief',
        is_current: true,
        data_hash: 'def456',
        updated_at: '2026-06-27T14:00:00Z',
      },
    ])
    trackJob({
      job_id: 'job-deck-failed',
      document_type: 'presentation_deck',
      status: 'failed',
      draft_id: null,
      download_url: null,
      error: 'Generation job is no longer available.',
      created_at: '2026-06-27T15:00:00Z',
    })
    _renderToast()
    expect(
      screen.getByTestId('generation-toast-failed-icon'),
    ).toBeInTheDocument()
    expect(
      screen.queryByTestId('generation-toast-rescue-msg'),
    ).toBeNull()
  })

  it('draft with no data_hash is NOT treated as usable (defends against NULL content)', async () => {
    // PR #445 prevents NULL content_json from being marked
    // is_current=true, but defense-in-depth: setCurrentDraftPresence
    // uses data_hash as a content-present signal. A draft with no
    // data_hash should NOT trigger rescue.
    setCurrentDraftPresence([
      {
        id: 77,
        document_type: 'analytical_appendix',
        is_current: true,
        data_hash: null,
        updated_at: '2026-06-27T14:00:00Z',
      },
    ])
    trackJob({
      job_id: 'job-bare-draft',
      document_type: 'analytical_appendix',
      status: 'failed',
      draft_id: null,
      download_url: null,
      error: 'Generation job is no longer available.',
      created_at: '2026-06-27T15:00:00Z',
    })
    _renderToast()
    await waitFor(() => {
      expect(
        screen.getByTestId('generation-toast-failed-icon'),
      ).toBeInTheDocument()
    })
    expect(
      screen.queryByTestId('generation-toast-rescue-msg'),
    ).toBeNull()
  })
})
