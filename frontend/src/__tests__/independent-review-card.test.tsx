/**
 * independent-review-card.test.tsx — May 25 2026.
 *
 * Pins the Independent Review surface — the advisory second-opinion
 * card that lands below the primary arbiter verdict on the Academic
 * Review section.
 *
 * Renders three states:
 *   - null review → no card
 *   - Plausible / Concerns / Implausible verdict → full card with
 *     verdict pill, overall reasoning, per-finding rows
 *   - per-finding with a concern → flagged inline
 *
 * Advisory-only language is asserted explicitly so a future redesign
 * can't accidentally drop the disclaimer that this card never
 * affects the primary score.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import IndependentReviewCard from '../components/IndependentReviewCard'
import type {
  IndependentReview,
} from '../stores/academicReviewStore'


function makeReview(
  verdict: IndependentReview['verdict'] = 'Plausible',
): IndependentReview {
  return {
    verdict,
    overall_reasoning: 'The findings hang together at a graduate level.',
    per_finding: [
      { finding: 'best_strategy_sharpe',
        label: 'Best Strategy Sharpe',
        assessment: 'Sharpe 0.63 is plausible for monthly multi-asset data.',
        concern: '' },
      { finding: 'regime_break_significance',
        label: '2022 Regime Break',
        assessment: 'Pre/post-2022 shift is consistent with the literature.',
        concern: '' },
      { finding: 'oos_validation',
        label: 'Out-of-Sample Validation',
        assessment: 'Walk-forward retained Sharpe — defensible.',
        concern: '' },
      { finding: 'diversification_benefit',
        label: 'Diversification Benefit',
        assessment: 'Drawdown cushion is plausible.',
        concern: '' },
      { finding: 'factor_loadings_summary',
        label: 'Factor Loadings Summary',
        assessment: 'Market beta near 1.0 for equity-tilted strategies.',
        concern: '' },
    ],
    model: 'gemini-2.5-pro',
    findings_seen: {},
  }
}


describe('IndependentReviewCard', () => {
  it('renders nothing when review is null', () => {
    const { container } = render(<IndependentReviewCard review={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the Plausible verdict with the success pill', () => {
    render(<IndependentReviewCard review={makeReview('Plausible')} />)
    const card = screen.getByTestId('independent-review-card')
    expect(card.getAttribute('data-verdict')).toBe('Plausible')
    const pill = screen.getByTestId('independent-verdict-pill')
    expect(pill.textContent).toBe('Plausible')
  })

  it('renders the Concerns verdict with the warning pill', () => {
    render(<IndependentReviewCard review={makeReview('Concerns')} />)
    const card = screen.getByTestId('independent-review-card')
    expect(card.getAttribute('data-verdict')).toBe('Concerns')
    expect(screen.getByTestId('independent-verdict-pill').textContent)
      .toBe('Concerns')
  })

  it('renders the Implausible verdict with the negative pill', () => {
    render(<IndependentReviewCard review={makeReview('Implausible')} />)
    const card = screen.getByTestId('independent-review-card')
    expect(card.getAttribute('data-verdict')).toBe('Implausible')
    expect(screen.getByTestId('independent-verdict-pill').textContent)
      .toBe('Implausible')
  })

  it('renders the advisory disclaimer prominently', () => {
    render(<IndependentReviewCard review={makeReview('Concerns')} />)
    // The disclaimer must always render so a reviewer doesn't
    // mistake this for the primary academic readiness verdict.
    expect(screen.getByText(/Advisory only — does not affect score or gates/))
      .toBeInTheDocument()
  })

  it('renders the model attribution so the reader sees which agent ran',
    () => {
      const review = makeReview('Plausible')
      review.model = 'gemini-2.5-pro'
      render(<IndependentReviewCard review={review} />)
      // The model name appears in the card so the reader sees that
      // this is a different agent from the primary arbiter.
      expect(screen.getByText(/gemini-2.5-pro/)).toBeInTheDocument()
    })

  it('renders the overall reasoning paragraph', () => {
    render(<IndependentReviewCard review={makeReview('Plausible')} />)
    expect(screen.getByTestId('independent-overall-reasoning').textContent)
      .toContain('The findings hang together')
  })

  it('renders every per-finding row by data-testid', () => {
    render(<IndependentReviewCard review={makeReview('Plausible')} />)
    // Every canonical finding has its own row.
    for (const key of [
      'best_strategy_sharpe', 'regime_break_significance',
      'oos_validation', 'diversification_benefit',
      'factor_loadings_summary',
    ]) {
      expect(screen.getByTestId(`independent-finding-${key}`))
        .toBeInTheDocument()
    }
  })

  it('renders the per-finding label and assessment text', () => {
    render(<IndependentReviewCard review={makeReview('Plausible')} />)
    const row = screen.getByTestId('independent-finding-best_strategy_sharpe')
    expect(row.textContent).toContain('Best Strategy Sharpe')
    expect(row.textContent).toContain('Sharpe 0.63 is plausible')
  })

  it('flags a per-finding concern inline when present', () => {
    const review = makeReview('Concerns')
    review.per_finding[0].concern =
      'A 1.2 Sharpe on monthly multi-asset data is well above '
      + 'typical literature values; worth questioning the test design.'
    render(<IndependentReviewCard review={review} />)
    const concern = screen.getByTestId(
      'independent-concern-best_strategy_sharpe')
    expect(concern.textContent).toContain('1.2 Sharpe')
  })

  it('does not render a concern element when concern is empty', () => {
    render(<IndependentReviewCard review={makeReview('Plausible')} />)
    // Plausible verdict has all empty concerns → no concern elements
    // render for any finding.
    expect(screen.queryByTestId(
      'independent-concern-best_strategy_sharpe')).toBeNull()
  })

  it('handles a review with zero per_finding entries gracefully', () => {
    const review: IndependentReview = {
      verdict:           'Concerns',
      overall_reasoning: 'Review failed to produce per-finding entries.',
      per_finding:       [],
      model:             'stub',
      findings_seen:     {},
    }
    render(<IndependentReviewCard review={review} />)
    // The card still renders with the overall reasoning.
    expect(screen.getByTestId('independent-review-card')).toBeInTheDocument()
    expect(screen.getByTestId('independent-overall-reasoning').textContent)
      .toContain('Review failed')
  })
})
