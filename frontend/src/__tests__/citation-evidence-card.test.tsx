/**
 * citation-evidence-card.test.tsx — May 23 2026 evidence-card contract.
 *
 * The Citation Review panel now renders every tile as a full
 * evaluation card with finding / extract / rationale / confidence /
 * alternatives / manual override (item 13 spec). These tests pin
 * the card behavior independently of the prior action-flow tests:
 *
 *   1. Tile is collapsed by default, expand toggle reveals evidence
 *   2. Collapsed view shows confidence badge so it is visible
 *      without opening the tile
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
  isAxiosError: (err: unknown) => boolean
}


function makeCitationsWithEvidence() {
  return [
    {
      // Primary (pending_review) with 2 alternatives — exercises
      // the full evidence card including the alternatives section.
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
    },
    {
      // Primary (verified) with no alternatives — exercises the
      // verified-tile transparency view and the <3 alternatives flag.
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
    },
    {
      // Not-found, no metadata, no evidence — exercises the
      // graceful-degradation placeholders and the <3 flag.
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
    },
  ]
}


beforeEach(() => {
  useCitationReviewStore.getState()._reset()
  mockedAxios.get = vi.fn().mockResolvedValue({
    data: { citations: makeCitationsWithEvidence() },
  })
  mockedAxios.post = vi.fn()
  mockedAxios.isAxiosError = (err: unknown): err is { isAxiosError?: boolean } =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
})

afterEach(() => {
  vi.clearAllMocks()
  useCitationReviewStore.getState()._reset()
})


// ── 1 & 2. Collapsed by default, confidence visible without expanding ──────


describe('Citation tile — collapsed state', () => {
  it('renders tile collapsed by default', async () => {
    render(<CitationReviewPanel generationId={42} />)
    await waitFor(() =>
      screen.getByTestId('citation-row-cvar_coherent_risk'))
    // Header is present (toggle button), but the expanded body
    // (testid `citation-expanded-...`) is NOT in the DOM.
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
    // The badge text matches the formatted confidence score (0.65
    // → "0.65"). This proves the score reaches the collapsed
    // header rather than being buried in metadata.
    expect(badge.textContent?.trim()).toMatch(/0\.65/)
  })

  it('shows status badge on the collapsed header', async () => {
    render(<CitationReviewPanel generationId={42} />)
    const row = await screen.findByTestId(
      'citation-row-cvar_coherent_risk')
    // The pending_review status renders as "Needs review".
    expect(within(row).getByText(/Needs review/)).toBeTruthy()
  })
})


// ── 3. Expanded state renders all six evidence sections ─────────────────────


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

    // Section labels render verbatim.
    expect(within(expanded).getByText(/Finding supported/i)).toBeTruthy()
    expect(within(expanded).getByText(/Supporting extract/i)).toBeTruthy()
    expect(within(expanded).getByText(/Selection rationale/i)).toBeTruthy()
    expect(within(expanded).getByText(/Confidence/i)).toBeTruthy()

    // Field VALUES from the fixture render too — proves the data
    // flows through.
    expect(within(expanded).getByText(/CVaR is a coherent risk measure/))
      .toBeTruthy()
    expect(within(expanded).getByText(/four axiomatic coherence/))
      .toBeTruthy()
    expect(within(expanded).getByText(/off-trusted domain/))
      .toBeTruthy()
  })

  it('renders placeholder when an evidence field is null', async () => {
    render(<CitationReviewPanel generationId={42} />)
    // momentum_factor has all four evidence fields null.
    await waitFor(() =>
      screen.getByTestId('citation-toggle-momentum_factor'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-momentum_factor'))

    await waitFor(() =>
      screen.getByTestId('citation-expanded-momentum_factor'))
    const expanded = screen.getByTestId(
      'citation-expanded-momentum_factor')

    // The placeholder copy renders for every empty field.
    expect(within(expanded).getByText(/Extract not captured/i))
      .toBeTruthy()
    // Confidence label degrades to em-dash when score is null.
    const confBadge = within(expanded).getByText(/— of 1\.00/)
    expect(confBadge).toBeTruthy()
  })
})


// ── 4. Alternative cards render full per-option evidence ───────────────────


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
    // The fixture has 2 alternatives.
    expect(altCards.length).toBe(2)
    // First alt is the pass_2_academic Rockafellar paper.
    expect(within(altCards[0]!).getByText(/Rockafellar/)).toBeTruthy()
    expect(within(altCards[0]!).getByText(/four coherence axioms/))
      .toBeTruthy()
    expect(within(altCards[0]!).getByText(/Pass 2 — academic/))
      .toBeTruthy()
    // Each alternative has its own "Accept this instead" button.
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
      // Evidence fields ride along on the payload — pin so a
      // future refactor cannot drop them on a swap.
      expect(lastCall[1].selected_alternative.confidence_score)
        .toBe(0.75)
      expect(lastCall[1].selected_alternative.supporting_extract)
        .toContain('coherence axioms')
    })
  })
})


// ── 5. <3 options shows the Limited alternatives flag ──────────────────────


describe('Citation tile — Limited alternatives flag', () => {
  it('shows the flag when total options < 3', async () => {
    render(<CitationReviewPanel generationId={42} />)
    // momentum_factor has zero options total — flag must render.
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
    // The first fixture entry has primary URL + 2 alternatives =
    // 3 options total, which clears the threshold.
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


// ── 6. Verified tiles render evidence + suppress action buttons ────────────


describe('Citation tile — verified tile transparency', () => {
  it('verified tile renders the evidence card with no action buttons', async () => {
    render(<CitationReviewPanel generationId={42} />)
    // Verified bucket is a closed <details> on initial mount; open
    // it so the verified tile becomes interactive.
    await waitFor(() => screen.getByTestId('verified-bucket'))
    const bucket = screen.getByTestId('verified-bucket') as HTMLDetailsElement
    bucket.open = true
    // Wait for the verified row to be reachable.
    await waitFor(() =>
      screen.getByTestId('citation-toggle-sharpe_ratio'))
    fireEvent.click(
      screen.getByTestId('citation-toggle-sharpe_ratio'))

    const expanded = await screen.findByTestId(
      'citation-expanded-sharpe_ratio')
    // Evidence renders.
    expect(within(expanded).getByText(/expected return per unit of risk/))
      .toBeTruthy()
    // Action buttons (accept / reject / manual override) do NOT
    // render for a verified tile.
    expect(within(expanded).queryByTestId(
      'citation-accept-sharpe_ratio')).toBeNull()
    expect(within(expanded).queryByTestId(
      'citation-reject-sharpe_ratio')).toBeNull()
    expect(within(expanded).queryByTestId(
      'citation-manual-toggle-sharpe_ratio')).toBeNull()
  })
})


// ── 7. Expansion state persists across remount ─────────────────────────────


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

    // The expansion survives the remount — the store kept it.
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
