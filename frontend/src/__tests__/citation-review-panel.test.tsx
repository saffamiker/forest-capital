/**
 * citation-review-panel.test.tsx
 *
 * Updated May 26 2026 (migration 045 — Citation Review redesign).
 * The panel now fetches /api/v1/citations/findings/{generation_id}
 * and renders a 3-level Finding ▸ Type ▸ Citation hierarchy. Every
 * test in this file drives that flow.
 *
 * The existing per-row review actions (accept primary / reject /
 * manual override / select alternative) are unchanged — those
 * tests still apply, just nested under a finding section.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

import CitationReviewPanel from '../components/reportwriter/CitationReviewPanel'
import { useCitationReviewStore } from '../stores/citationReviewStore'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  delete: ReturnType<typeof vi.fn>
  isAxiosError: (err: unknown) => boolean
}


function makeCitations() {
  return [
    {
      id: 1, concept_id: 'sharpe_ratio',
      author: 'Sharpe, W. F.', year: '1994',
      title: 'The Sharpe Ratio',
      journal_or_institution: 'Journal of Portfolio Management',
      volume_issue_pages: '21(1), 49-58',
      url: 'https://www.jstor.org/stable/jpm.21.1.49',
      verification_status: 'verified',
      search_query_used: 'sharpe ratio definition',
      alternatives: [],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: 'Sharpe, W. F. (1994). The Sharpe Ratio.',
      supporting_extract: 'Expected return per unit of risk.',
      selection_rationale: 'Original paper on JSTOR.',
      confidence_score: 0.98,
      finding_supported: 'Sharpe ratio is the standard risk-adjusted metric.',
      citation_type: 'theoretical',
      trust_flag: 'verified',
      scoring_rationale: null,
      matched_finding_ids: [] as number[],
    },
    {
      id: 2, concept_id: 'cvar_coherent_risk',
      author: 'Acerbi, C.', year: '2002',
      title: 'Coherent measures of risk in a working paper',
      journal_or_institution: 'University of Milan',
      volume_issue_pages: null,
      url: 'https://www.uni-milan.edu/papers/wp1.pdf',
      verification_status: 'pending_review',
      search_query_used: 'CVaR coherent risk measure',
      alternatives: [
        {
          author: 'Rockafellar, R.', year: '2000',
          title: 'Optimization of conditional value-at-risk',
          journal_or_institution: 'Journal of Risk',
          volume_issue_pages: '2(3), 21-41',
          url: 'https://imf.org/papers/cvar.pdf',
          pass_source: 'pass_2_academic',
          supporting_extract: 'CVaR is a coherent measure of risk.',
          selection_rationale: 'Academic journal on IMF.',
          confidence_score: 0.75,
          finding_supported: 'CVaR is a coherent risk measure.',
        },
      ],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: null,
      supporting_extract: 'CVaR has four axiomatic coherence properties.',
      selection_rationale: 'University working paper.',
      confidence_score: 0.65,
      finding_supported: 'CVaR is a coherent risk measure for downside risk.',
      citation_type: 'methodological',
      trust_flag: null,
      scoring_rationale: null,
      matched_finding_ids: [] as number[],
    },
    {
      id: 3, concept_id: 'momentum_factor',
      author: null, year: null, title: null,
      journal_or_institution: null, volume_issue_pages: null,
      url: null,
      verification_status: 'not_found',
      search_query_used: 'momentum factor return',
      alternatives: [],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: null,
      supporting_extract: null,
      selection_rationale: null,
      confidence_score: null,
      finding_supported: null,
      citation_type: 'empirical',
      trust_flag: null,
      scoring_rationale: null,
      matched_finding_ids: [] as number[],
    },
  ]
}


function makeFindingsResponse(generationId: number, opts?: {
  citations?: ReturnType<typeof makeCitations>
  findings?: Array<Record<string, unknown>>
}) {
  return {
    data: {
      generation_id: generationId,
      seeded_at: new Date().toISOString(),
      findings: opts?.findings ?? [
        {
          id: 9001,
          source: 'audit',
          source_id: 'D04',
          // Description carries tokens that overlap with each
          // citation in the default fixture (sharpe/ratio/cvar/
          // risk/measures/momentum/factor) so the May-26 relevance
          // filter keeps them visible under the default view. The
          // dedicated TestRelevanceFilter block exercises the
          // filter-out + show-all toggle paths.
          title: 'Strategy return-series coverage',
          description: (
            'Sharpe ratio, CVaR risk measures, momentum factor '
            + 'evidence for splice junction coverage.'),
          rank: 'high',
          status: 'warning',
          severity: 'warning',
          matched_count: 0,
        },
      ],
      citations: opts?.citations ?? makeCitations(),
    },
  }
}


beforeEach(() => {
  useCitationReviewStore.getState()._reset()
  mockedAxios.get    = vi.fn()
  mockedAxios.post   = vi.fn()
  mockedAxios.delete = vi.fn()
  mockedAxios.isAxiosError = (err) =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
})

afterEach(() => {
  vi.clearAllMocks()
  useCitationReviewStore.getState()._reset()
})


describe('CitationReviewPanel — empty state', () => {
  it('renders nothing when generationId is null', () => {
    const { container } = render(
      <CitationReviewPanel generationId={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('shows the no-data message when findings and citations are both empty', async () => {
    mockedAxios.get.mockResolvedValueOnce({
      data: {
        generation_id: 42,
        seeded_at: null,
        findings: [],
        citations: [],
      },
    })
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.getByText(
        /No findings or citations to review/i)).toBeTruthy()
    })
  })
})


describe('CitationReviewPanel — fetch and render', () => {
  it('GETs the findings endpoint and renders the finding section', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    render(<CitationReviewPanel generationId={42} />)

    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledWith(
        '/api/v1/citations/findings/42')
    })
    await waitFor(() => {
      expect(screen.getByTestId('finding-section-9001')).toBeTruthy()
    })
    // Header summary chip shows the totals.
    expect(screen.getByText(/1 finding · 3 citations/)).toBeTruthy()
    expect(screen.getByText(/1 gap/)).toBeTruthy()
  })

  it('groups citations by type inside a finding section', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    render(<CitationReviewPanel generationId={42} />)

    // Three citation rows render — one per fixture citation —
    // grouped under their respective type sub-headers.
    await waitFor(() =>
      screen.getByTestId('citation-row-cvar_coherent_risk'))
    expect(screen.getByTestId('type-subgroup-9001-theoretical'))
      .toBeTruthy()
    expect(screen.getByTestId('type-subgroup-9001-methodological'))
      .toBeTruthy()
    expect(screen.getByTestId('type-subgroup-9001-empirical'))
      .toBeTruthy()
    expect(screen.getByTestId('citation-row-sharpe_ratio'))
      .toBeTruthy()
    expect(screen.getByTestId('citation-row-momentum_factor'))
      .toBeTruthy()
  })

  it('shows error when the fetch fails', async () => {
    mockedAxios.get.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 500, data: { detail: 'Server error' }},
      message: 'Request failed with status code 500',
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => {
      expect(screen.queryByText(/Server error|status code 500/)).toBeTruthy()
    })
  })

  it('renders a gap warning on findings with 0 matched citations', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('finding-gap-9001'))
    expect(screen.getByText(
      /No supporting citations yet/i)).toBeTruthy()
  })

  it('does not show the no-findings copy when at least one finding is present', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() => screen.getByTestId('finding-section-9001'))
    expect(screen.queryByText(
      /No high or medium-rank findings/)).toBeNull()
  })
})


describe('CitationReviewPanel — match checkbox', () => {
  it('clicking an unticked checkbox POSTs to /citations/match', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    mockedAxios.post.mockResolvedValueOnce({
      data: { matched: true },
    })

    render(<CitationReviewPanel generationId={42} />)
    const checkbox = await screen.findByTestId(
      'citation-match-9001-2')
    expect((checkbox as HTMLInputElement).checked).toBe(false)

    fireEvent.click(checkbox)

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/citations/match',
        { citation_id: 2, finding_id: 9001 })
    })
  })

  it('clicking a ticked checkbox DELETEs from /citations/match', async () => {
    const cits = makeCitations()
    cits[1]!.matched_finding_ids = [9001]
    const findings = [{
      id: 9001,
      source: 'audit',
      source_id: 'D04',
      title: 'Strategy return-series coverage',
      description: null,
      rank: 'high',
      status: 'warning',
      severity: 'warning',
      matched_count: 1,
    }]
    mockedAxios.get.mockResolvedValueOnce(
      makeFindingsResponse(42, { citations: cits, findings }))
    mockedAxios.delete.mockResolvedValueOnce({
      data: { removed: true },
    })

    render(<CitationReviewPanel generationId={42} />)
    const checkbox = await screen.findByTestId(
      'citation-match-9001-2')
    expect((checkbox as HTMLInputElement).checked).toBe(true)

    fireEvent.click(checkbox)

    await waitFor(() => {
      expect(mockedAxios.delete).toHaveBeenCalledWith(
        '/api/v1/citations/match',
        { data: { citation_id: 2, finding_id: 9001 }})
    })
  })
})


describe('CitationReviewPanel — review actions', () => {
  it('accept_untrusted POSTs the right body', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      citations: cits,
    }))
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[1]!,
          verification_status: 'human_verified',
          reviewer_email: 'bob@queens.edu',
          review_action: 'accept_untrusted',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    await waitFor(() =>
      screen.getByTestId('citation-accept-cvar_coherent_risk'))

    fireEvent.click(screen.getByTestId('citation-accept-cvar_coherent_risk'))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/citations/2/review',
        { action: 'accept_untrusted' })
    })
  })

  it('reject POSTs the right body', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      citations: cits,
    }))
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[2]!,
          verification_status: 'rejected',
          review_action: 'reject',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-momentum_factor'))
    fireEvent.click(screen.getByTestId('citation-toggle-momentum_factor'))
    await waitFor(() =>
      screen.getByTestId('citation-reject-momentum_factor'))

    fireEvent.click(screen.getByTestId('citation-reject-momentum_factor'))

    await waitFor(() => {
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/v1/citations/3/review',
        { action: 'reject' })
    })
  })

  it('select_alternative POSTs the picked entry', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      citations: cits,
    }))
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[1]!,
          verification_status: 'search_selected',
          review_action: 'select_alternative',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    const alternativeButtons = await waitFor(() =>
      screen.getAllByTestId('citation-accept-alternative'))
    expect(alternativeButtons.length).toBeGreaterThan(0)

    fireEvent.click(alternativeButtons[0]!)

    await waitFor(() => {
      const calls = (mockedAxios.post as ReturnType<typeof vi.fn>).mock.calls
      const lastCall = calls[calls.length - 1]
      expect(lastCall[0]).toBe('/api/v1/citations/2/review')
      expect(lastCall[1].action).toBe('select_alternative')
      expect(lastCall[1].selected_alternative.author).toBe('Rockafellar, R.')
    })
  })

  it('manual_add toggles the form and submits the entered citation', async () => {
    const cits = makeCitations()
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      citations: cits,
    }))
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...cits[2]!,
          verification_status: 'manually_added',
          author: 'Jegadeesh, N.', year: '1993',
          title: 'Returns to Buying Winners',
          review_action: 'manual_add',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-momentum_factor'))
    fireEvent.click(screen.getByTestId('citation-toggle-momentum_factor'))
    await waitFor(() =>
      screen.getByTestId('citation-manual-toggle-momentum_factor'))

    fireEvent.click(
      screen.getByTestId('citation-manual-toggle-momentum_factor'))

    fireEvent.change(
      screen.getByTestId('citation-manual-author-momentum_factor'),
      { target: { value: 'Jegadeesh, N.' }})
    const inputs = screen.getAllByPlaceholderText(/Year|Title/i)
    fireEvent.change(inputs[0]!, { target: { value: '1993' }})
    fireEvent.change(inputs[1]!, { target: { value: 'Returns to Buying Winners' }})

    fireEvent.click(
      screen.getByTestId('citation-manual-submit-momentum_factor'))

    await waitFor(() => {
      const calls = (mockedAxios.post as ReturnType<typeof vi.fn>).mock.calls
      const lastCall = calls[calls.length - 1]
      const body = lastCall[1]
      expect(body.action).toBe('manual_add')
      expect(body.manual_citation.author).toBe('Jegadeesh, N.')
      expect(body.manual_citation.year).toBe('1993')
      expect(body.manual_citation.title).toBe('Returns to Buying Winners')
    })
  })
})


describe('CitationReviewPanel — relevance filter (May 26 2026)', () => {
  // The filter hides citations whose finding_supported / concept_id /
  // title share NO significant token with the finding's title +
  // description. An already-matched citation always renders. A
  // "Show all" toggle is the escape hatch when the heuristic
  // misses a genuinely relevant citation.

  function _findingWithDescription(desc: string) {
    return [{
      id: 9001,
      source: 'audit',
      source_id: 'D04',
      title: 'Test Finding',
      description: desc,
      rank: 'high',
      status: 'warning',
      severity: 'warning',
      matched_count: 0,
    }]
  }

  it('hides citations with no token overlap by default', async () => {
    // Finding description has NO overlap with the three default
    // fixture citations (sharpe / cvar / momentum). All citations
    // should be hidden under the default filtered view.
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      findings: _findingWithDescription(
        'Tax loss harvesting accounting treatment.'),
    }))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('finding-section-9001'))
    // No citation rows render under the filtered view.
    expect(screen.queryByTestId('citation-row-sharpe_ratio'))
      .toBeNull()
    expect(screen.queryByTestId('citation-row-cvar_coherent_risk'))
      .toBeNull()
    expect(screen.queryByTestId('citation-row-momentum_factor'))
      .toBeNull()
    // Summary line shows the filtered count.
    expect(screen.getByTestId('relevance-summary-9001')).toBeTruthy()
    expect(screen.getByText(/Showing 0 of 3 citations/i)).toBeTruthy()
  })

  it('shows citations whose tokens overlap with the finding', async () => {
    // Finding description names "Sharpe ratio" — overlaps with the
    // sharpe_ratio citation's title/concept_id but NOT the cvar /
    // momentum citations.
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      findings: _findingWithDescription(
        'Sharpe ratio statistical significance.'),
    }))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-row-sharpe_ratio'))
    expect(screen.queryByTestId('citation-row-cvar_coherent_risk'))
      .toBeNull()
    expect(screen.queryByTestId('citation-row-momentum_factor'))
      .toBeNull()
    // 1 of 3 visible — summary shows the filter count.
    expect(screen.getByText(/Showing 1 of 3 citations/i)).toBeTruthy()
  })

  it('Show all toggle reveals every citation', async () => {
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      findings: _findingWithDescription(
        'Tax loss harvesting accounting treatment.'),
    }))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('relevance-toggle-9001'))

    fireEvent.click(screen.getByTestId('relevance-toggle-9001'))

    // Now every citation renders.
    await waitFor(() =>
      screen.getByTestId('citation-row-sharpe_ratio'))
    expect(screen.getByTestId('citation-row-cvar_coherent_risk'))
      .toBeTruthy()
    expect(screen.getByTestId('citation-row-momentum_factor'))
      .toBeTruthy()
    expect(screen.getByText(/Showing all 3 citations/i)).toBeTruthy()
  })

  it('matched citation always renders, even without token overlap', async () => {
    // Finding has NO overlap with sharpe_ratio's tokens, BUT the
    // citation is already matched to the finding. The user's
    // explicit match wins over the heuristic.
    const cits = makeCitations()
    cits[0]!.matched_finding_ids = [9001]  // sharpe_ratio matched
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42, {
      citations: cits,
      findings: _findingWithDescription(
        'Tax loss harvesting accounting treatment.'),
    }))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-row-sharpe_ratio'))
    // The non-matched citations are still hidden.
    expect(screen.queryByTestId('citation-row-cvar_coherent_risk'))
      .toBeNull()
    // Summary reflects the matched-only render (1 of 3).
    expect(screen.getByText(/Showing 1 of 3 citations/i)).toBeTruthy()
  })

  it('no toggle when every citation is already relevant', async () => {
    // The fixture-default finding ('Strategy return-series coverage'
    // with description naming sharpe/cvar/risk/measures/momentum/
    // factor) overlaps with every citation — nothing is filtered.
    // The relevance-summary line should not render in that case.
    mockedAxios.get.mockResolvedValueOnce(makeFindingsResponse(42))
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-row-sharpe_ratio'))
    expect(screen.queryByTestId('relevance-summary-9001')).toBeNull()
    expect(screen.queryByTestId('relevance-toggle-9001')).toBeNull()
  })
})
