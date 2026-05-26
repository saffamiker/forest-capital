/**
 * citation-evidence-card.test.tsx — May 23 2026 evidence-card contract,
 * updated May 26 2026 for the Citation Review redesign (migration 045).
 *
 * Every tile is a full evaluation card with finding / extract /
 * rationale / confidence / alternatives / manual override. The
 * redesign nested these tiles under Level-1 finding sections, but
 * the per-tile contract is unchanged:
 *
 *   1. Tile is collapsed by default, expand toggle reveals evidence
 *   2. Collapsed view shows confidence badge
 *   3. Expanded view renders all six evidence sections including
 *      placeholders when a field is null
 *   4. Alternative cards render each alternative's own
 *      extract / rationale / confidence + "Accept this instead"
 *      button
 *   5. <3 options surfaces the "Limited alternatives" flag
 *   6. Verified tiles also render the evidence card (no action
 *      buttons) — transparency contract
 *   7. Expansion state persists across remount via the store
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
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


function makeCitationsWithEvidence() {
  return [
    {
      id: 100, concept_id: 'cvar_coherent_risk',
      author: 'Acerbi, C.', year: '2002',
      title: 'Coherent measures of risk',
      journal_or_institution: 'University of Milan',
      volume_issue_pages: null,
      url: 'https://www.uni-milan.edu/papers/wp1.pdf',
      verification_status: 'pending_review',
      search_query_used: 'CVaR coherent risk',
      alternatives: [
        {
          author: 'Rockafellar, R.', year: '2000',
          title: 'Optimization of conditional value-at-risk',
          journal_or_institution: 'Journal of Risk',
          volume_issue_pages: '2(3), 21-41',
          url: 'https://imf.org/papers/cvar.pdf',
          pass_source: 'pass_2_academic',
          supporting_extract: 'CVaR satisfies the four coherence axioms.',
          selection_rationale: 'IMF-hosted Journal of Risk paper.',
          confidence_score: 0.75,
          finding_supported: 'CVaR is a coherent risk measure.',
        },
        {
          author: 'Pflug, G.', year: '2000',
          title: 'Some remarks on the value-at-risk',
          journal_or_institution: 'University of Vienna',
          volume_issue_pages: null,
          url: 'https://oecd.org/cvar_remarks.pdf',
          pass_source: 'pass_3_widest',
          supporting_extract: 'VaR lacks subadditivity in general.',
          selection_rationale: 'OECD-hosted retrospective on VaR limitations.',
          confidence_score: 0.55,
          finding_supported: 'VaR is non-coherent so CVaR is preferred.',
        },
      ],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: null,
      supporting_extract: 'CVaR has four axiomatic coherence properties.',
      selection_rationale: 'University working paper, off-trusted domain.',
      confidence_score: 0.65,
      finding_supported: 'CVaR is a coherent risk measure for downside risk.',
      citation_type: 'methodological',
      trust_flag: null,
      scoring_rationale: null,
      matched_finding_ids: [],
    },
    {
      id: 101, concept_id: 'sharpe_ratio',
      author: 'Sharpe, W. F.', year: '1994',
      title: 'The Sharpe Ratio',
      journal_or_institution: 'Journal of Portfolio Management',
      volume_issue_pages: '21(1), 49-58',
      url: 'https://www.jstor.org/stable/jpm.21.1.49',
      verification_status: 'verified',
      search_query_used: 'sharpe ratio',
      alternatives: [],
      reviewer_email: null, reviewed_at: null, review_action: null,
      formatted: 'Sharpe, W. F. (1994). The Sharpe Ratio.',
      supporting_extract: 'The Sharpe ratio is the expected return per unit of risk.',
      selection_rationale: 'Original Sharpe paper on trusted JSTOR.',
      confidence_score: 0.98,
      finding_supported: 'The Sharpe ratio measures risk-adjusted return.',
      citation_type: 'theoretical',
      trust_flag: 'verified',
      scoring_rationale: null,
      matched_finding_ids: [],
    },
    {
      id: 102, concept_id: 'momentum_factor',
      author: null, year: null, title: null,
      journal_or_institution: null, volume_issue_pages: null,
      url: null,
      verification_status: 'not_found',
      search_query_used: 'momentum factor',
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
      matched_finding_ids: [],
    },
  ]
}


function makeFindingsResponse(generationId: number) {
  return {
    data: {
      generation_id: generationId,
      seeded_at: new Date().toISOString(),
      findings: [
        {
          id: 9001,
          source: 'audit',
          source_id: 'D04',
          title: 'Strategy return-series coverage',
          description: null,
          rank: 'high',
          status: 'warning',
          severity: 'warning',
          matched_count: 0,
        },
      ],
      citations: makeCitationsWithEvidence(),
    },
  }
}


beforeEach(() => {
  useCitationReviewStore.getState()._reset()
  mockedAxios.get = vi.fn().mockResolvedValue(makeFindingsResponse(42))
  mockedAxios.post = vi.fn()
  mockedAxios.delete = vi.fn()
  mockedAxios.isAxiosError = (err: unknown): err is { isAxiosError?: boolean } =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
})

afterEach(() => {
  vi.clearAllMocks()
  useCitationReviewStore.getState()._reset()
})


describe('Citation tile — collapsed state', () => {
  it('renders tile collapsed by default', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-row-cvar_coherent_risk'))
    expect(screen.getByTestId('citation-toggle-cvar_coherent_risk'))
      .toBeTruthy()
    expect(screen.queryByTestId('citation-expanded-cvar_coherent_risk'))
      .toBeNull()
  })

  it('shows confidence badge on the collapsed header', async () => {
    render(<CitationReviewPanel generationId={42} />)
    const badge = await screen.findByTestId(
      'citation-confidence-cvar_coherent_risk')
    expect(badge).toBeTruthy()
    expect(badge.textContent?.trim()).toMatch(/0\.65/)
  })

  it('shows status badge on the collapsed header', async () => {
    render(<CitationReviewPanel generationId={42} />)
    const row = await screen.findByTestId(
      'citation-row-cvar_coherent_risk')
    expect(within(row).getByText(/Needs review/)).toBeTruthy()
  })
})


describe('Citation tile — expanded evidence card', () => {
  it('renders finding / extract / rationale / confidence sections when expanded', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))

    await waitFor(() =>
      screen.getByTestId('citation-expanded-cvar_coherent_risk'))
    const expanded = screen.getByTestId(
      'citation-expanded-cvar_coherent_risk')

    expect(within(expanded).getByText(/Finding supported/i)).toBeTruthy()
    expect(within(expanded).getByText(/Supporting extract/i)).toBeTruthy()
    expect(within(expanded).getByText(/Selection rationale/i)).toBeTruthy()
    expect(within(expanded).getByText(/Confidence/i)).toBeTruthy()

    expect(within(expanded).getByText(/CVaR is a coherent risk measure/))
      .toBeTruthy()
    expect(within(expanded).getByText(/four axiomatic coherence/))
      .toBeTruthy()
    expect(within(expanded).getByText(/off-trusted domain/))
      .toBeTruthy()
  })

  it('renders placeholder when an evidence field is null', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-momentum_factor'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-momentum_factor'))

    await waitFor(() =>
      screen.getByTestId('citation-expanded-momentum_factor'))
    const expanded = screen.getByTestId(
      'citation-expanded-momentum_factor')

    expect(within(expanded).getByText(/Extract not captured/i))
      .toBeTruthy()
    const confBadge = within(expanded).getByText(/— of 1\.00/)
    expect(confBadge).toBeTruthy()
  })
})


describe('Citation tile — alternative cards', () => {
  it('renders one alternative card per option with its evidence', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))

    await waitFor(() =>
      screen.getByTestId('citation-expanded-cvar_coherent_risk'))
    const expanded = screen.getByTestId(
      'citation-expanded-cvar_coherent_risk')

    const altCards = within(expanded).getAllByTestId(
      'citation-alternative-card')
    expect(altCards.length).toBe(2)
    expect(within(altCards[0]!).getByText(/Rockafellar/)).toBeTruthy()
    expect(within(altCards[0]!).getByText(/four coherence axioms/))
      .toBeTruthy()
    expect(within(altCards[0]!).getByText(/Pass 2 — academic/))
      .toBeTruthy()
    expect(within(altCards[0]!).getByTestId(
      'citation-accept-alternative')).toBeTruthy()
    expect(within(altCards[1]!).getByTestId(
      'citation-accept-alternative')).toBeTruthy()
  })

  it('"Accept this instead" submits the alternative payload', async () => {
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        citation: {
          ...makeCitationsWithEvidence()[0],
          verification_status: 'search_selected',
        },
      },
    })

    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))

    const acceptButtons = await screen.findAllByTestId(
      'citation-accept-alternative')
    fireEvent.click(acceptButtons[0]!)

    await waitFor(() => {
      const calls = mockedAxios.post.mock.calls
      const lastCall = calls[calls.length - 1]
      expect(lastCall[0]).toBe('/api/v1/citations/100/review')
      expect(lastCall[1].action).toBe('select_alternative')
      expect(lastCall[1].selected_alternative.author).toBe(
        'Rockafellar, R.')
      expect(lastCall[1].selected_alternative.confidence_score)
        .toBe(0.75)
      expect(lastCall[1].selected_alternative.supporting_extract)
        .toContain('coherence axioms')
    })
  })
})


describe('Citation tile — Limited alternatives flag', () => {
  it('shows the flag when total options < 3', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-momentum_factor'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-momentum_factor'))

    const expanded = await screen.findByTestId(
      'citation-expanded-momentum_factor')
    expect(within(expanded).getByText(/Limited alternatives/i))
      .toBeTruthy()
    expect(within(expanded).getByText(
      /manual review recommended/i)).toBeTruthy()
  })

  it('hides the flag when total options >= 3', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))

    const expanded = await screen.findByTestId(
      'citation-expanded-cvar_coherent_risk')
    expect(within(expanded).queryByText(/Limited alternatives/i))
      .toBeNull()
  })
})


describe('Citation tile — verified tile transparency', () => {
  it('verified tile renders the evidence card with no action buttons', async () => {
    render(<CitationReviewPanel generationId={42} />)
    // In the redesign every citation renders directly under its
    // finding's type sub-group — no more closed Verified <details>
    // bucket. The verified tile is immediately reachable.
    await waitFor(() =>
      screen.getByTestId('citation-toggle-sharpe_ratio'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-sharpe_ratio'))

    const expanded = await screen.findByTestId(
      'citation-expanded-sharpe_ratio')
    expect(within(expanded).getByText(/expected return per unit of risk/))
      .toBeTruthy()
    expect(within(expanded).queryByTestId(
      'citation-accept-sharpe_ratio')).toBeNull()
    expect(within(expanded).queryByTestId(
      'citation-reject-sharpe_ratio')).toBeNull()
    expect(within(expanded).queryByTestId(
      'citation-manual-toggle-sharpe_ratio')).toBeNull()
  })
})


describe('Citation tile — expansion persistence', () => {
  it('an expanded tile stays expanded after panel remount', async () => {
    const { unmount } = render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-cvar_coherent_risk'))
    await waitFor(() =>
      screen.getByTestId('citation-expanded-cvar_coherent_risk'))

    unmount()
    render(<CitationReviewPanel generationId={42} />)

    await waitFor(() =>
      screen.getByTestId('citation-expanded-cvar_coherent_risk'))
  })

  it('the store exposes setExpanded as a per-citation toggle', () => {
    useCitationReviewStore.getState().setExpanded(7, true)
    useCitationReviewStore.getState().setExpanded(8, false)
    expect(
      useCitationReviewStore.getState().expandedByCitationId[7]).toBe(true)
    expect(
      useCitationReviewStore.getState().expandedByCitationId[8]).toBe(false)
  })
})
