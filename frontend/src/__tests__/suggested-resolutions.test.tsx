/**
 * suggested-resolutions.test.tsx — Banner + Modal + row badge
 * (Suggested Resolutions, Commit 7/7).
 *
 * Covers:
 *   - SuggestionsBanner — hidden when no suggestions, shows the right
 *     count when populated, dismissable for the session.
 *   - SuggestionReviewModal — Confirm gate, Dismiss flow, paginated
 *     Prev / Next, scopedToFailureId filtering, sibling cascade
 *     removal after approve.
 *   - Failure Reports row badge — renders when a pending suggestion
 *     exists, opens the scoped modal on click.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  render, screen, fireEvent, waitFor, within,
} from '@testing-library/react'

vi.mock('axios')

import axios from 'axios'
import {
  SuggestionsBanner, SuggestionReviewModal, type PRSuggestion,
} from '../components/SuggestedResolutions'
import { TestAdminSections } from '../components/TestRunnerSettings'

const mockedAxios = vi.mocked(axios, true)


function makeSuggestion(over: Partial<PRSuggestion> = {}): PRSuggestion {
  return {
    suggestion_id: 1,
    failure_report_id: 42,
    pr_number: 65,
    pr_title: 'May 21 batch — UAT/Bob fixes',
    pr_url: 'https://github.com/saffamiker/forest-capital/pull/65',
    pr_merged_at: '2026-05-21T13:00:00Z',
    pr_author: 'saffamiker',
    matched_commit_shas: ['abc1234'],
    matched_on: 'Resolves failure #42',
    created_at: '2026-05-21T14:00:00Z',
    failure: {
      id: 42,
      script_id: 'all_testers_v1',
      step_id: 'council_submit',
      user_email: 'thaob@queens.edu',
      failure_description: 'Council 502 under load.',
      actual_result: '502 Bad Gateway',
      severity: 'major',
      attested_at: '2026-05-20T10:00:00Z',
    },
    ...over,
  }
}


beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
})


// ── SuggestionsBanner ─────────────────────────────────────────────────────────

describe('SuggestionsBanner', () => {
  it('renders nothing when the GET returns no suggestions', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({ data: { suggestions: [] } })
    const { container } = render(
      <SuggestionsBanner onReview={() => {}} />,
    )
    // Wait for the fetch to settle.
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled())
    // The banner is a <div> with role="region"; absent → empty render.
    expect(container.querySelector('[role="region"]')).toBeNull()
  })

  it('shows the right count when suggestions exist', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: {
        suggestions: [
          makeSuggestion({ suggestion_id: 1 }),
          makeSuggestion({ suggestion_id: 2, failure_report_id: 43 }),
          makeSuggestion({ suggestion_id: 3, failure_report_id: 44 }),
        ],
      },
    })
    render(<SuggestionsBanner onReview={() => {}} />)
    await waitFor(() => expect(
      screen.getByText(/3 failures may be resolved/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /review now/i }))
      .toBeInTheDocument()
  })

  it('handles the singular form for exactly one suggestion', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: { suggestions: [makeSuggestion()] },
    })
    render(<SuggestionsBanner onReview={() => {}} />)
    await waitFor(() => expect(
      screen.getByText(/1 failure may be resolved/i)).toBeInTheDocument())
  })

  it('Review Now invokes the onReview callback with the suggestion list',
    async () => {
      const suggestions = [makeSuggestion(), makeSuggestion({
        suggestion_id: 2, failure_report_id: 43,
      })]
      mockedAxios.get = vi.fn().mockResolvedValue({ data: { suggestions } })
      const onReview = vi.fn()
      render(<SuggestionsBanner onReview={onReview} />)
      await waitFor(() =>
        expect(screen.getByRole('button', { name: /review now/i }))
          .toBeInTheDocument())
      fireEvent.click(screen.getByRole('button', { name: /review now/i }))
      expect(onReview).toHaveBeenCalledWith(suggestions)
    })

  it('the × button dismisses the banner for the session', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: { suggestions: [makeSuggestion()] },
    })
    const { container, unmount } = render(
      <SuggestionsBanner onReview={() => {}} />,
    )
    await waitFor(() =>
      expect(container.querySelector('[role="region"]')).not.toBeNull())
    fireEvent.click(screen.getByLabelText(/dismiss for this session/i))
    expect(container.querySelector('[role="region"]')).toBeNull()
    // Remount → still dismissed because the sessionStorage flag persists.
    unmount()
    const { container: c2 } = render(
      <SuggestionsBanner onReview={() => {}} />,
    )
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalledTimes(2))
    expect(c2.querySelector('[role="region"]')).toBeNull()
  })
})


// ── SuggestionReviewModal ────────────────────────────────────────────────────

describe('SuggestionReviewModal — Confirm gate', () => {
  it('Confirm is disabled by default (root_cause empty)', () => {
    render(<SuggestionReviewModal
      suggestions={[makeSuggestion()]}
      onClose={() => {}} onActioned={() => {}} />)
    expect(screen.getByRole('button', { name: /confirm resolution/i }))
      .toBeDisabled()
  })

  it('Confirm enables once root_cause + remediation_note are filled', () => {
    render(<SuggestionReviewModal
      suggestions={[makeSuggestion()]}
      onClose={() => {}} onActioned={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/what caused this failure/i),
      { target: { value: 'Race condition.' } })
    fireEvent.change(screen.getByPlaceholderText(
      /what was changed and how does it address/i),
      { target: { value: 'Added a lock.' } })
    expect(screen.getByRole('button', { name: /confirm resolution/i }))
      .toBeEnabled()
  })

  it('switching type to no_bug_detected drops the remediation requirement',
    () => {
      render(<SuggestionReviewModal
        suggestions={[makeSuggestion()]}
        onClose={() => {}} onActioned={() => {}} />)
      fireEvent.click(screen.getByLabelText(/no bug detected/i))
      fireEvent.change(screen.getByPlaceholderText(/what caused this failure/i),
        { target: { value: 'User error.' } })
      // No remediation field rendered now, and Confirm is enabled.
      expect(screen.queryByPlaceholderText(
        /what was changed and how does it address/i)).toBeNull()
      expect(screen.getByRole('button', { name: /confirm resolution/i }))
        .toBeEnabled()
    })
})


describe('SuggestionReviewModal — actions', () => {
  it('Confirm POSTs the approve endpoint with the form values',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({ data: {
        approved: true, failure_id: 42, siblings_dismissed: [],
      }})
      const onActioned = vi.fn()
      render(<SuggestionReviewModal
        suggestions={[makeSuggestion({ suggestion_id: 7 })]}
        onClose={() => {}} onActioned={onActioned} />)
      fireEvent.change(screen.getByPlaceholderText(/what caused this failure/i),
        { target: { value: 'Stale cache.' } })
      fireEvent.change(screen.getByPlaceholderText(
        /what was changed and how does it address/i),
        { target: { value: 'Invalidated on push.' } })
      fireEvent.click(screen.getByRole('button', { name: /confirm resolution/i }))
      await waitFor(() =>
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/api/v1/testing/suggestions/7/approve', {
            root_cause: 'Stale cache.',
            remediation_note: 'Invalidated on push.',
          }))
      await waitFor(() => expect(onActioned).toHaveBeenCalled())
    })

  it('Dismiss POSTs the dismiss endpoint and removes the card',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { dismissed: true },
      })
      const onClose = vi.fn()
      render(<SuggestionReviewModal
        suggestions={[makeSuggestion({ suggestion_id: 9 })]}
        onClose={onClose} onActioned={() => {}} />)
      fireEvent.click(screen.getByRole('button',
        { name: /dismiss suggestion/i }))
      await waitFor(() =>
        expect(mockedAxios.post).toHaveBeenCalledWith(
          '/api/v1/testing/suggestions/9/dismiss'))
      // Last card → modal auto-closes when the working list empties.
      await waitFor(() => expect(onClose).toHaveBeenCalled())
    })

  it('approve also removes sibling cards the backend cascade-dismissed',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: {
          approved: true, failure_id: 42,
          // The cascade dismissed two other suggestions.
          siblings_dismissed: [2, 3],
        },
      })
      const onClose = vi.fn()
      render(<SuggestionReviewModal
        suggestions={[
          makeSuggestion({ suggestion_id: 1, failure_report_id: 42 }),
          makeSuggestion({ suggestion_id: 2, failure_report_id: 42 }),
          makeSuggestion({ suggestion_id: 3, failure_report_id: 42 }),
        ]}
        onClose={onClose} onActioned={() => {}} />)
      // Three cards initially → "1 of 3".
      expect(screen.getByText(/1 of 3/i)).toBeInTheDocument()
      fireEvent.change(screen.getByPlaceholderText(/what caused this failure/i),
        { target: { value: 'rc' } })
      fireEvent.change(screen.getByPlaceholderText(
        /what was changed and how does it address/i),
        { target: { value: 'rn' } })
      fireEvent.click(screen.getByRole('button', { name: /confirm resolution/i }))
      // Approve removed THIS card (1) + the two cascaded siblings (2, 3),
      // → working list emptied → modal auto-closes.
      await waitFor(() => expect(onClose).toHaveBeenCalled())
    })
})


describe('SuggestionReviewModal — pagination and scoping', () => {
  it('renders "1 of N" with multiple suggestions and navigates Next/Prev',
    () => {
      render(<SuggestionReviewModal
        suggestions={[
          makeSuggestion({ suggestion_id: 1, failure_report_id: 1,
                           matched_on: 'Resolves failure #1' }),
          makeSuggestion({ suggestion_id: 2, failure_report_id: 2,
                           matched_on: 'Resolves failure #2' }),
        ]}
        onClose={() => {}} onActioned={() => {}} />)
      expect(screen.getByText(/1 of 2/i)).toBeInTheDocument()
      expect(screen.getByText('"Resolves failure #1"')).toBeInTheDocument()
      fireEvent.click(screen.getByLabelText(/next suggestion/i))
      expect(screen.getByText('"Resolves failure #2"')).toBeInTheDocument()
      fireEvent.click(screen.getByLabelText(/previous suggestion/i))
      expect(screen.getByText('"Resolves failure #1"')).toBeInTheDocument()
    })

  it('scopedToFailureId filters to one failure\'s suggestions only', () => {
    render(<SuggestionReviewModal
      suggestions={[
        makeSuggestion({ suggestion_id: 1, failure_report_id: 1,
                         matched_on: 'Resolves failure #1' }),
        makeSuggestion({ suggestion_id: 2, failure_report_id: 2,
                         matched_on: 'Resolves failure #2' }),
        makeSuggestion({ suggestion_id: 3, failure_report_id: 2,
                         matched_on: 'Fixes failure #2' }),
      ]}
      scopedToFailureId={2}
      onClose={() => {}} onActioned={() => {}} />)
    // Only the two cards for failure_id=2 are visible.
    expect(screen.queryByText('"Resolves failure #1"')).toBeNull()
    expect(screen.getByText(/1 of 2/i)).toBeInTheDocument()
  })
})


// ── Row badge — wired into FailureReportsBlock ───────────────────────────────

describe('Failure Reports row badge', () => {
  function mountTabs() {
    return render(<TestAdminSections />)
  }

  beforeEach(() => {
    mockedAxios.get = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/v1/testing/failures') {
        return Promise.resolve({ data: { failures: [{
          id: 42, user_email: 'thaob@queens.edu',
          script_id: 'all_testers_v1', step_id: 'council_submit',
          failure_description: 'Council 502.',
          expected_result: null, actual_result: '502',
          severity: 'major', screenshot_paths: [],
          low_quality: false, attested_at: '2026-05-20T10:00:00Z',
          resolved_at: null, resolved_by: null, resolution_note: null,
          resolution_type: null, fix_reference: null, remediation_note: null,
        }, {
          id: 43, user_email: 'murdockm@queens.edu',
          script_id: 'all_testers_v1', step_id: 'council_markdown',
          failure_description: 'Markdown not rendering.',
          expected_result: null, actual_result: null,
          severity: 'minor', screenshot_paths: [],
          low_quality: false, attested_at: '2026-05-19T10:00:00Z',
          resolved_at: null, resolved_by: null, resolution_note: null,
          resolution_type: null, fix_reference: null, remediation_note: null,
        }] } })
      }
      if (url === '/api/v1/testing/suggestions') {
        return Promise.resolve({ data: { suggestions: [
          makeSuggestion({ suggestion_id: 1, failure_report_id: 42 }),
        ] } })
      }
      if (url === '/api/v1/testing/suggestions/by-failure') {
        // Only failure 42 has a pending suggestion.
        return Promise.resolve({ data: { by_failure: { 42: 1 } } })
      }
      if (url === '/api/v1/testing/feedback') {
        return Promise.resolve({ data: { feedback: [] } })
      }
      if (url === '/api/v1/testing/triage') {
        return Promise.resolve({ data: { reports: [] } })
      }
      if (url === '/api/v1/testing/issue-tracker') {
        return Promise.resolve({ data: { issues: [] } })
      }
      return Promise.reject(new Error(`Unexpected GET: ${url}`))
    })
  })

  it('renders the "Fix available — review" badge on a matched failure',
    async () => {
      mountTabs()
      // Failure Reports tab is the default; wait for it to load.
      await waitFor(() => expect(
        screen.getByText(/fix available — review/i)).toBeInTheDocument())
    })

  it('does not render the badge on a failure without a suggestion',
    async () => {
      mountTabs()
      // Wait for both failures to render.
      await waitFor(() =>
        expect(screen.getByText('Markdown not rendering.'))
          .toBeInTheDocument())
      // Only ONE badge across the whole tab — on failure 42 only.
      const badges = screen.getAllByText(/fix available — review/i)
      expect(badges).toHaveLength(1)
    })

  it('clicking the badge opens the scoped modal', async () => {
    mountTabs()
    await waitFor(() => expect(
      screen.getByText(/fix available — review/i)).toBeInTheDocument())
    fireEvent.click(screen.getByText(/fix available — review/i))
    // Wait for the modal to mount — its dialog role is unique.
    await waitFor(() => expect(
      screen.getByRole('dialog', { name: /suggested resolutions/i }))
      .toBeInTheDocument())
    // Inside the modal, the matched_on citation surfaces.
    within(screen.getByRole('dialog'))
      .getByText('"Resolves failure #42"')
  })
})
