/**
 * document-editor-review-score.test.tsx — May 25 2026.
 *
 * Pins the editor's auto-fired Academic Review surface:
 *   - The score pill renders in the header on a midpoint / executive
 *     brief draft once the status endpoint reports `complete`.
 *   - A "Reviewing…" placeholder shows while the auto-fire is still
 *     in flight (status: "missing").
 *   - The amber advisory banner renders ONLY for midpoint drafts
 *     with score < 6.0; an executive brief at the same score shows
 *     the score pill but no banner (its gate is on the generation
 *     endpoint, not in the editor).
 *   - A presentation_deck draft never asks for review status.
 *   - The pill's data-advisory attribute differentiates green vs
 *     amber so the test contract pins both visual states.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

vi.mock('axios', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() },
}))

import axios from 'axios'
import DocumentEditor from '../pages/DocumentEditor'
import type { EditorDraft, TipTapDoc } from '../types/editor'

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
}

beforeEach(() => {
  mockedAxios.get.mockReset()
  mockedAxios.post.mockReset()
  mockedAxios.patch.mockReset()
  mockedAxios.patch.mockResolvedValue({ data: {} })
})


function midpointDraft(id = 42): EditorDraft {
  const content: TipTapDoc = {
    type: 'doc',
    content: [{ type: 'paragraph', content: [{ type: 'text',
      text: 'Body of the midpoint.' }] }],
  }
  return {
    id, document_type: 'midpoint_paper', owner_email: 't@q.edu',
    title: 'Midpoint Paper', word_count: 4, version: 1, is_current: true,
    is_deleted: false, created_from: 'generated',
    created_at: null, updated_at: null,
    content_text: 'Body of the midpoint.',
    content_json: content,
  }
}

function briefDraft(id = 43): EditorDraft {
  const content: TipTapDoc = {
    type: 'doc',
    content: [{ type: 'paragraph', content: [{ type: 'text',
      text: 'Brief summary.' }] }],
  }
  return {
    id, document_type: 'executive_brief', owner_email: 't@q.edu',
    title: 'Executive Brief', word_count: 2, version: 1, is_current: true,
    is_deleted: false, created_from: 'generated',
    created_at: null, updated_at: null,
    content_text: 'Brief summary.',
    content_json: content,
  }
}

function deckDraft(id = 44): EditorDraft {
  return {
    id, document_type: 'presentation_deck', owner_email: 't@q.edu',
    title: 'Deck', word_count: 0, version: 1, is_current: true,
    is_deleted: false, created_from: 'generated',
    created_at: null, updated_at: null, content_text: '',
    content_json: {
      slides: [{
        id: 1, title: 'Opening', background: '#FFFFFF', speaker_notes: '',
        speaker: 'Molly', elements: [],
      }],
    },
  }
}


// Wires the axios mock so the editor's draft load + the review status
// endpoint return what each test wants. The reviewStatus arg drives
// the GET /academic-review-status response.
function mountEditor(
  draft: EditorDraft,
  reviewStatus:
    | { status: 'complete' | 'running' | 'missing';
        score: number | null; rating: string | null;
        advisory: boolean; document_type?: string }
    | null,
) {
  mockedAxios.get.mockImplementation((url: string) => {
    if (url.endsWith('/versions')) {
      return Promise.resolve({ data: { versions: [] } })
    }
    if (url.endsWith('/academic-review-status')) {
      if (reviewStatus === null) {
        return Promise.reject(new Error('status fetch failed'))
      }
      return Promise.resolve({
        data: {
          status: reviewStatus.status,
          score: reviewStatus.score,
          rating: reviewStatus.rating,
          advisory: reviewStatus.advisory,
          document_type: reviewStatus.document_type
            ?? draft.document_type,
          section_ratings: {},
          run_at: '2026-05-25T12:00:00Z',
          threshold: 6.0,
        },
      })
    }
    if (url.includes('/documents/drafts/')) {
      return Promise.resolve({ data: draft })
    }
    return Promise.resolve({ data: {} })
  })
  return render(
    <MemoryRouter initialEntries={[`/editor/${draft.id}`]}>
      <Routes>
        <Route path="/editor/:draftId" element={<DocumentEditor />} />
      </Routes>
    </MemoryRouter>)
}


describe('DocumentEditor — auto-fired Academic Review score display', () => {
  it('renders the green score pill when score is >= 6.0 on a midpoint',
    async () => {
      mountEditor(midpointDraft(), {
        status: 'complete', score: 7.5, rating: 'Developing',
        advisory: false,
      })
      const pill = await screen.findByTestId('review-score-pill')
      expect(pill.textContent).toContain('7.5/10')
      expect(pill.getAttribute('data-advisory')).toBe('false')
      // No advisory banner above the header for a passing midpoint.
      expect(screen.queryByTestId('review-advisory-banner')).toBeNull()
    })

  it('renders the amber advisory banner AND amber pill when midpoint < 6.0',
    async () => {
      mountEditor(midpointDraft(), {
        status: 'complete', score: 5.5, rating: 'Needs Work',
        advisory: true,
      })
      const banner = await screen.findByTestId('review-advisory-banner')
      expect(banner.textContent).toMatch(/score: 5\.5\/10/)
      expect(banner.textContent).toMatch(/Review the findings in the Council/)
      const pill = screen.getByTestId('review-score-pill')
      expect(pill.getAttribute('data-advisory')).toBe('true')
      expect(pill.textContent).toContain('5.5/10')
    })

  it('shows the pill but NOT the advisory banner for an executive brief',
    async () => {
      // Same low score — but the executive brief is gated at
      // generation time, not in the editor. The pill surfaces the
      // score; the in-editor banner does not fire.
      mountEditor(briefDraft(), {
        status: 'complete', score: 5.5, rating: 'Needs Work',
        advisory: false,
      })
      const pill = await screen.findByTestId('review-score-pill')
      expect(pill.textContent).toContain('5.5/10')
      expect(screen.queryByTestId('review-advisory-banner')).toBeNull()
    })

  it('shows the Reviewing… placeholder while the auto-fire is in flight',
    async () => {
      mountEditor(midpointDraft(), {
        status: 'missing', score: null, rating: null, advisory: false,
      })
      const pending = await screen.findByTestId('review-score-pending')
      expect(pending.textContent).toMatch(/Reviewing/)
      expect(screen.queryByTestId('review-score-pill')).toBeNull()
    })

  it('does NOT request review status for a presentation_deck draft',
    async () => {
      // The deck path doesn't schedule an auto-review, so the editor
      // must not ask the status endpoint either.
      mountEditor(deckDraft(), null)
      // Wait for the draft to render — the back button is in the header.
      await screen.findByLabelText('Back to Reports')
      const statusCalls = mockedAxios.get.mock.calls.filter(
        (call: unknown[]) => typeof call[0] === 'string'
          && (call[0] as string).endsWith('/academic-review-status'))
      expect(statusCalls).toHaveLength(0)
      expect(screen.queryByTestId('review-score-pill')).toBeNull()
      expect(screen.queryByTestId('review-score-pending')).toBeNull()
    })

  it('renders nothing review-related when the status fetch errors',
    async () => {
      // A failed status fetch falls back to "no score" — the editor
      // shows nothing rather than an error chrome.
      mountEditor(midpointDraft(), null)
      await screen.findByLabelText('Back to Reports')
      await waitFor(() => {
        expect(mockedAxios.get.mock.calls.some(
          (call: unknown[]) => typeof call[0] === 'string'
            && (call[0] as string).endsWith('/academic-review-status')
        )).toBe(true)
      })
      // No pill, no pending placeholder, no advisory banner.
      expect(screen.queryByTestId('review-score-pill')).toBeNull()
      expect(screen.queryByTestId('review-score-pending')).toBeNull()
      expect(screen.queryByTestId('review-advisory-banner')).toBeNull()
    })
})
