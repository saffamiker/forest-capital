/**
 * risk-contribution-bar.test.tsx — Marginal Contribution to Risk
 * classification fix (May 23 2026).
 *
 * Two bugs prompted this test file:
 *
 *   1. The classification used a 0.5pp dead band (delta > 0.5 was
 *      "concentrator", delta < -0.5 was "diversifier"). A strategy
 *      at 11.6% with a 11.11% equal-weight reference had delta=+0.49
 *      — inside the dead band — and rendered neutral / blue. The
 *      fix uses a strict pct > weight comparison so the threshold
 *      tracks 1 / visibleStrategyCount exactly.
 *
 *   2. The diversifier bar / legend square used the Tailwind class
 *      `bg-positive/60`. The Tailwind config has no `positive`
 *      token, so the bars and the legend square rendered with no
 *      background. The fix switches diversifier to bg-electric/60
 *      (the blue that was already visible for the dead-band
 *      "neutral" state), collapsing the chart to a clean two-tone
 *      legend that matches what users see in the bars.
 *
 * The tests pin both fixes via the data-classification attribute
 * on each row (concentrator | diversifier).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

import { RiskContributionBar } from
  '../components/diversification/RiskContributionBar'


/** Mock the data hook so each test injects its own fixture. The
 *  hook is imported only by the chart component, so this mock
 *  intercepts every read. */
const mockData = { current: null as Record<string, unknown> | null }

vi.mock('../lib/useDiversificationData', () => ({
  useRiskContribution: () => ({
    data: mockData.current,
    loading: false,
    error: null,
  }),
}))


beforeEach(() => { mockData.current = null })
afterEach(() => { vi.clearAllMocks() })


/** Helper — builds a risk contribution payload for n strategies
 *  with the given equal-scheme pct contributions. The labels are
 *  S1 / S2 / … so tests can reference them by index. */
function makePayload(pcts: number[]) {
  return {
    labels: pcts.map((_, i) => `S${i + 1}`),
    pct_risk_contribution_equal: pcts,
    pct_risk_contribution_tangency: null,
    tangency_weights: null,
  }
}


describe('RiskContributionBar — classification threshold (1/n strict)', () => {
  it('with 9 strategies and equal scheme, a strategy at 11.6% is a concentrator', () => {
    // 9 strategies → equal weight reference = 11.11%. The old
    // 0.5pp dead band misclassified 11.6% as neutral.
    mockData.current = makePayload([
      14.1,  // REGIME_SWITCHING — concentrator (was concentrator)
      13.2,  // CLASSIC_60_40    — concentrator (was concentrator)
      11.6,  // MOMENTUM_ROTATION — concentrator (was BLUE before fix)
      11.2,  // EQUAL_WEIGHT      — concentrator (above 11.11%)
      11.2,  // BLACK_LITTERMAN   — concentrator (above 11.11%)
      10.0,  // diversifier
      9.5,   // diversifier
      9.5,   // diversifier
      9.7,   // diversifier
    ])
    render(<RiskContributionBar />)
    expect(screen.getByTestId('risk-contribution-row-S3'))
      .toHaveAttribute('data-classification', 'concentrator')
    expect(screen.getByTestId('risk-contribution-row-S4'))
      .toHaveAttribute('data-classification', 'concentrator')
    expect(screen.getByTestId('risk-contribution-row-S5'))
      .toHaveAttribute('data-classification', 'concentrator')
    expect(screen.getByTestId('risk-contribution-row-S6'))
      .toHaveAttribute('data-classification', 'diversifier')
  })

  it('threshold updates dynamically when n changes (3 strategies → 33.33%)', () => {
    // With 3 strategies, the 1/n threshold is 33.33%. A strategy
    // at 40% is a concentrator; at 20% it's a diversifier.
    mockData.current = makePayload([40, 20, 40])
    const { unmount } = render(<RiskContributionBar />)
    expect(screen.getByTestId('risk-contribution-row-S1'))
      .toHaveAttribute('data-classification', 'concentrator')
    expect(screen.getByTestId('risk-contribution-row-S2'))
      .toHaveAttribute('data-classification', 'diversifier')
    unmount()

    // Same strategies on a different n — now 6, so 1/n = 16.67%.
    // The 20% strategy is now a concentrator, not a diversifier.
    mockData.current = makePayload([40, 20, 40, 0, 0, 0])
    render(<RiskContributionBar />)
    expect(screen.getByTestId('risk-contribution-row-S2'))
      .toHaveAttribute('data-classification', 'concentrator')
  })

  it('a strategy exactly at its 1/n weight reference is a diversifier', () => {
    // 5 strategies → 20% each at equal weight. A strategy at 20.0%
    // satisfies pct <= weight; classified as diversifier (the
    // two-tone scheme has no neutral state). Floating-point exact
    // equality is rare in real data, but the contract is pinned.
    mockData.current = makePayload([20.0, 20.0, 20.0, 20.0, 20.0])
    render(<RiskContributionBar />)
    expect(screen.getByTestId('risk-contribution-row-S1'))
      .toHaveAttribute('data-classification', 'diversifier')
  })

  it('a strategy at 1/n + 0.1pp is still a concentrator (no dead band)', () => {
    // 9 strategies → 11.11%. A strategy at 11.2% has delta=+0.09;
    // the old 0.5pp dead band would have rendered it neutral. The
    // strict comparison classifies it as concentrator.
    mockData.current = makePayload([
      11.2, 11.11, 11.11, 11.11, 11.11, 11.11, 11.11, 11.11, 11.0,
    ])
    render(<RiskContributionBar />)
    expect(screen.getByTestId('risk-contribution-row-S1'))
      .toHaveAttribute('data-classification', 'concentrator')
  })
})


describe('RiskContributionBar — legend matches bar colours', () => {
  it('renders both an orange concentrator square and a blue diversifier square', () => {
    mockData.current = makePayload([14, 11, 11, 11, 11, 11, 11, 11, 11])
    const { container } = render(<RiskContributionBar />)

    // Both legend squares must be VISIBLE — the previous diversifier
    // square used bg-positive/60 (an undefined Tailwind token), so
    // the legend rendered no square at all next to "diversifier".
    const orangeSquare = container.querySelector('span.bg-warning\\/60')
    const blueSquare = container.querySelector('span.bg-electric\\/60')
    expect(orangeSquare).not.toBeNull()
    expect(blueSquare).not.toBeNull()

    // The undefined token must not appear anywhere — a regression
    // that reintroduces bg-positive would re-blank the diversifier
    // surface.
    const positiveAnywhere = container.querySelector(
      '[class*="bg-positive"]')
    expect(positiveAnywhere).toBeNull()
  })

  it('legend text mentions both classifications with the right comparator', () => {
    mockData.current = makePayload([14, 11, 11, 11, 11, 11, 11, 11, 11])
    const { container } = render(<RiskContributionBar />)
    // The legend text is interleaved with <span> colored squares,
    // so getByText's text-node matcher won't find it. Read the
    // full legend paragraph's textContent.
    const legend = container.querySelector('p.text-2xs')
    const text = legend?.textContent ?? ''
    expect(text).toMatch(/risk concentrator/i)
    expect(text).toMatch(/diversifier/i)
    // The legend's diversifier criterion now uses ≤ (was a strict
    // <), matching the two-tone classification logic.
    expect(text).toMatch(/contribution ≤ weight/i)
  })
})


describe('RiskContributionBar — scheme toggle preserves classification', () => {
  it('switching to tangency uses tangency weights as the reference', () => {
    // When tangency is available, the threshold is the per-row
    // tangency_weight × 100, not 1/n. A row whose pct exceeds its
    // tangency weight is still a concentrator.
    mockData.current = {
      labels: ['S1', 'S2', 'S3'],
      pct_risk_contribution_equal:    [40, 30, 30],
      pct_risk_contribution_tangency: [50, 25, 25],
      tangency_weights:               [0.6, 0.2, 0.2],
    }
    render(<RiskContributionBar />)
    // Default equal scheme: 1/n = 33.33%. S1 at 40% → concentrator.
    expect(screen.getByTestId('risk-contribution-row-S1'))
      .toHaveAttribute('data-classification', 'concentrator')

    // Switch to tangency scheme. S1's tangency weight = 60%, but
    // its risk contribution is 50% → diversifier (50% < 60%).
    fireEvent.click(screen.getByTestId('risk-contribution-scheme-tangency'))
    expect(screen.getByTestId('risk-contribution-row-S1'))
      .toHaveAttribute('data-classification', 'diversifier')
    // S2's tangency weight = 20%, contribution 25% → concentrator.
    expect(screen.getByTestId('risk-contribution-row-S2'))
      .toHaveAttribute('data-classification', 'concentrator')
  })
})
