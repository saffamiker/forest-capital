/**
 * rubric-review.test.tsx — May 25 2026.
 *
 * Pins the WritingAssistant's new "Review Against Rubric" surface:
 *   - The button renders on a midpoint_paper draft and is hidden on
 *     other document types (rubric is midpoint-specific).
 *   - Clicking POSTs to /api/v1/documents/drafts/{id}/rubric-review.
 *   - The structured response renders inline: overall verdict pill,
 *     per-section pass/fail rows, and suggested edits with reasoning.
 *   - The endpoint never modifies the draft — no PATCH / PUT fired.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

vi.mock('axios')
const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>
  patch: ReturnType<typeof vi.fn>
  put: ReturnType<typeof vi.fn>
}

import WritingAssistant from '../components/editor/WritingAssistant'


const RUBRIC_PAYLOAD = {
  sections: {
    methodology: { verdict: 'pass',
                   reasoning: 'Data sources and constraints stated.' },
    results:     { verdict: 'fail',
                   reasoning: 'Results lack explicit interpretation.' },
    roles:       { verdict: 'pass',
                   reasoning: 'Roles attributed factually.' },
    next_steps:  { verdict: 'pass',
                   reasoning: 'Forward-looking and specific.' },
  },
  edits: [
    { section: 'results',
      suggestion: 'Add one sentence interpreting the post-2022 Sharpe gap.',
      reasoning: 'Rubric requires interpretation, not listing.' },
    { section: 'methodology',
      suggestion: 'Name the Carhart MOM factor explicitly.',
      reasoning: 'The rubric calls for four factors, not three.' },
  ],
  overall: {
    verdict: 'needs_work',
    reasoning: 'Three sections pass; results section needs interpretation.',
  },
}


beforeEach(() => {
  mockedAxios.post = vi.fn().mockResolvedValue({ data: RUBRIC_PAYLOAD })
  mockedAxios.patch = vi.fn().mockResolvedValue({ data: {} })
  mockedAxios.put = vi.fn().mockResolvedValue({ data: {} })
})


describe('WritingAssistant — Review Against Rubric', () => {
  it('renders the rubric review button on a midpoint_paper draft', () => {
    render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                              documentType="midpoint_paper" />)
    expect(screen.getByTestId('rubric-review-button')).toBeInTheDocument()
  })

  it('hides the rubric review button on non-midpoint document types', () => {
    // Executive brief / deck / script don't carry the FNA 670 midpoint
    // rubric; the button would mislead the user. Hidden entirely.
    render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                              documentType="executive_brief" />)
    expect(screen.queryByTestId('rubric-review-button')).toBeNull()
  })

  it('hides the rubric review button when documentType is undefined', () => {
    // Defensive — a draft loaded without a document_type should not
    // render the rubric path (no rubric to review against).
    render(<WritingAssistant draftId={42} unresolvedMarkers={0} />)
    expect(screen.queryByTestId('rubric-review-button')).toBeNull()
  })

  it('POSTs to /rubric-review on click', async () => {
    render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                              documentType="midpoint_paper" />)
    fireEvent.click(screen.getByTestId('rubric-review-button'))
    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/documents/drafts/42/rubric-review')
    })
  })

  it('renders the overall verdict, per-section verdicts, and edits',
    async () => {
      render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                                documentType="midpoint_paper" />)
      fireEvent.click(screen.getByTestId('rubric-review-button'))
      // Overall card renders the human-friendly label.
      const overall = await screen.findByTestId('rubric-overall')
      expect(overall.textContent).toMatch(/Needs work/i)
      expect(overall.textContent).toContain('Three sections pass')

      // Each of the four sections appears with its data-verdict.
      const methodology = screen.getByTestId('rubric-section-methodology')
      expect(methodology.getAttribute('data-verdict')).toBe('pass')
      expect(methodology.textContent).toContain('Pass')
      expect(methodology.textContent)
        .toContain('Data sources and constraints stated.')

      const results = screen.getByTestId('rubric-section-results')
      expect(results.getAttribute('data-verdict')).toBe('fail')
      expect(results.textContent).toContain('Fail')
      expect(results.textContent).toContain('lack explicit interpretation')

      // Suggested edits render with their section label, suggestion text,
      // and reasoning ("Why: …" prefix).
      const edit0 = screen.getByTestId('rubric-edit-0')
      expect(edit0.textContent).toContain('Preliminary Results')
      expect(edit0.textContent).toContain('post-2022 Sharpe gap')
      expect(edit0.textContent).toMatch(/Why:.*Rubric requires interpretation/)
    })

  it('does not fire any draft-modification request', async () => {
    // The whole point of the rubric path is "suggestions only — never
    // modifies the draft". Asserting no PATCH/PUT fires keeps the
    // contract honest.
    render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                              documentType="midpoint_paper" />)
    fireEvent.click(screen.getByTestId('rubric-review-button'))
    await screen.findByTestId('rubric-overall')
    expect(mockedAxios.patch).not.toHaveBeenCalled()
    expect(mockedAxios.put).not.toHaveBeenCalled()
    // Only ONE post — the rubric review itself. No follow-up writes.
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/v1/documents/drafts/42/rubric-review')
  })

  it('surfaces a 422 detail message verbatim on error', async () => {
    mockedAxios.post = vi.fn().mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 422,
        data: { detail:
          'Rubric review is only available for midpoint paper drafts.' },
      },
    })
    // Lift axios.isAxiosError so the component's type guard works under
    // the mock. The real axios export carries this helper.
    ;(axios as unknown as { isAxiosError: (e: unknown) => boolean })
      .isAxiosError = (_e: unknown) => true

    render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                              documentType="midpoint_paper" />)
    fireEvent.click(screen.getByTestId('rubric-review-button'))
    expect(await screen.findByText(
      /only available for midpoint paper drafts/)).toBeInTheDocument()
  })

  it('renders an "unavailable" banner when the backend returns that flag',
    async () => {
      // The endpoint returns a structured "unavailable" payload when
      // the Gemini key is missing, the parse fails, or a transient
      // Gemini error fires. The UI keeps the structured shape so the
      // user sees a clean failure mode rather than a raw error.
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: {
          ...RUBRIC_PAYLOAD,
          unavailable: true,
          overall: { verdict: 'not_ready',
                     reasoning: 'GOOGLE_API_KEY env var is not set.' },
        },
      })
      render(<WritingAssistant draftId={42} unresolvedMarkers={0}
                                documentType="midpoint_paper" />)
      fireEvent.click(screen.getByTestId('rubric-review-button'))
      const card = await screen.findByTestId('rubric-review-result')
      expect(card.textContent).toContain('Rubric review unavailable')
      expect(card.textContent).toContain('GOOGLE_API_KEY')
    })
})
