/**
 * cio-regime-blends-implied.test.tsx -- June 8 2026.
 *
 * The CIO recommendation card now renders a "Blend Shift on Regime
 * Flip" section showing the three regime blends (BULL / BEAR /
 * TRANSITION) each with their strategy weights, the asset-class
 * implied split, and the delta-from-current-portfolio (in pp).
 *
 * The endpoint overlays this from analytics_metrics_cache
 * 'regime_blends' crossed with per-strategy avg_equity_weight /
 * avg_bond_weight. Absent when the cache is cold or the live current
 * implied is unavailable -- the card omits the whole section.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'

import CIORecommendationCard from '../components/CIORecommendationCard'
import { UIProvider } from '../context/UIContext'

vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
}

const baseRec = {
  signal: 'Bear regime posterior at 87%.',
  recommendation: 'Concentrate in low-beta strategies.',
  confidence: {
    regime: 'BEAR', probability: 0.874, ess: 82.86, ess_warning: false,
  },
  dissenting_view: 'Blend is constrained by the 40% ceiling.',
  key_risk: 'A rapid geopolitical de-escalation.',
  limitations: ['Three-asset universe.'],
  blend_weights: {
    VOL_TARGETING: 0.35, MIN_VARIANCE: 0.34,
    RISK_PARITY: 0.18, BENCHMARK: 0.05,
  },
  implied_asset_allocation: {
    equity_pct: 0.324, bond_pct: 0.676, cash_pct: 0,
  },
  computed_at: '2026-06-06 01:24:18+00:00',
  _model: 'claude-sonnet-4-6',
}

function renderCard() {
  return render(
    <MemoryRouter>
      <UIProvider>
        <CIORecommendationCard />
      </UIProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => { mockedAxios.get = vi.fn() })
afterEach(() => { vi.clearAllMocks() })


describe('CIO card -- Blend Shift on Regime Flip (June 8 2026)', () => {

  it('omits the section when regime_blends_implied is absent', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: true, recommendation: baseRec },
    })
    renderCard()
    // Wait for the card to render past the loading state.
    await screen.findByText(/BEAR/)
    expect(
      screen.queryByTestId('cio-regime-blends-implied')).not.toBeInTheDocument()
  })

  it('renders the section header when the overlay is present', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BULL: {
              weights: { BENCHMARK: 0.40, REGIME_SWITCHING: 0.40,
                         CLASSIC_60_40: 0.20 },
              equity_pct: 0.68, bond_pct: 0.32, cash_pct: 0,
              equity_delta_pp: 35.6,
              bond_delta_pp: -35.6,
            },
          },
        },
      },
    })
    renderCard()
    const section = await screen.findByTestId('cio-regime-blends-implied')
    expect(section).toBeInTheDocument()
    expect(section.textContent).toMatch(/Blend Shift on Regime Flip/i)
  })

  it('renders the strategy weights row in the canonical compact format', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BULL: {
              weights: { BENCHMARK: 0.40, REGIME_SWITCHING: 0.40,
                         CLASSIC_60_40: 0.20 },
              equity_pct: 0.68, bond_pct: 0.32, cash_pct: 0,
            },
          },
        },
      },
    })
    renderCard()
    const bull = await screen.findByTestId('cio-regime-blend-BULL')
    // The "STRAT N%" format matches the digest's existing output --
    // top three strategies by weight, integer-rounded percentages.
    expect(bull.textContent).toContain('BENCHMARK 40%')
    expect(bull.textContent).toContain('REGIME_SWITCHING 40%')
    expect(bull.textContent).toContain('CLASSIC_60_40 20%')
  })

  it('renders the implied equity/bonds split line per regime', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BULL: {
              weights: { A: 1.0 },
              equity_pct: 0.68, bond_pct: 0.32, cash_pct: 0,
            },
          },
        },
      },
    })
    renderCard()
    const bull = await screen.findByTestId('cio-regime-blend-BULL')
    // One-decimal percentages -- matches the digest format so the
    // two surfaces never disagree on rounding.
    expect(bull.textContent).toContain('Equity 68.0%')
    expect(bull.textContent).toContain('Bonds 32.0%')
  })

  it('renders the delta line in percentage points with explicit sign', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BULL: {
              weights: { A: 1.0 },
              equity_pct: 0.68, bond_pct: 0.32, cash_pct: 0,
              equity_delta_pp: 35.6,
              bond_delta_pp: -35.6,
            },
          },
        },
      },
    })
    renderCard()
    const delta = await screen.findByTestId('cio-regime-blend-BULL-delta')
    expect(delta.textContent).toMatch(/vs today/i)
    // Explicit +/- sign + pp unit. The frontend uses the field value
    // verbatim -- no re-multiplying by 100. If the backend ever
    // returned a fraction here, the rendered string would be tiny.
    expect(delta.textContent).toContain('Equity +35.6pp')
    expect(delta.textContent).toContain('Bonds -35.6pp')
  })

  it('omits the delta line when delta fields are absent', async () => {
    // The endpoint may emit the section without deltas when the live
    // current implied isn't available. The strategy-weights and
    // implied-split lines still render; the delta line is dropped.
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BEAR: {
              weights: { DEFENSIVE: 1.0 },
              equity_pct: 0.05, bond_pct: 0.95, cash_pct: 0,
              // No equity_delta_pp / bond_delta_pp.
            },
          },
        },
      },
    })
    renderCard()
    const row = await screen.findByTestId('cio-regime-blend-BEAR')
    expect(row.textContent).toContain('Equity 5.0%')
    expect(
      screen.queryByTestId('cio-regime-blend-BEAR-delta'),
    ).not.toBeInTheDocument()
  })

  it('renders IG / HY split when the overlay carries it (June 2026)', async () => {
    // When the strategy cache rows carry avg_ig_weight / avg_hy_weight
    // (post-backfill), the regime entry gains ig_bond_pct, hy_bond_pct,
    // ig_bond_delta_pp, hy_bond_delta_pp. The implied + delta lines
    // render IG and HY columns; the combined "Bonds X%" wording is
    // replaced with "IG X% | HY Y%".
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BULL: {
              weights: { DYN: 1.0 },
              equity_pct: 0.7, bond_pct: 0.3, cash_pct: 0,
              ig_bond_pct: 0.225, hy_bond_pct: 0.075,
              equity_delta_pp: 37.6,
              bond_delta_pp: -37.6,
              ig_bond_delta_pp: -12.5,
              hy_bond_delta_pp: -25.1,
            },
          },
        },
      },
    })
    renderCard()
    const bull = await screen.findByTestId('cio-regime-blend-BULL')
    expect(bull.textContent).toContain('IG 22.5%')
    expect(bull.textContent).toContain('HY 7.5%')
    // Combined "Bonds X%" wording must be GONE on the IG/HY path.
    expect(bull.textContent).not.toContain('Bonds 30.0%')
    const delta = await screen.findByTestId('cio-regime-blend-BULL-delta')
    expect(delta.textContent).toContain('IG -12.5pp')
    expect(delta.textContent).toContain('HY -25.1pp')
    // Combined bond delta also gone.
    expect(delta.textContent).not.toContain('Bonds -37.6pp')
  })

  it('falls back to combined Bonds when the overlay omits IG/HY', async () => {
    // Pre-backfill rows have only the combined fields. The card
    // must render gracefully (no crash, no missing rows).
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            BULL: {
              weights: { OLD: 1.0 },
              equity_pct: 0.7, bond_pct: 0.3, cash_pct: 0,
              equity_delta_pp: 37.6, bond_delta_pp: -37.6,
              // No ig_bond_pct / hy_bond_pct.
            },
          },
        },
      },
    })
    renderCard()
    const bull = await screen.findByTestId('cio-regime-blend-BULL')
    expect(bull.textContent).toContain('Bonds 30.0%')
    expect(bull.textContent).not.toContain('IG ')
  })

  it('renders BULL, BEAR, TRANSITION in that fixed order', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          regime_blends_implied: {
            // Deliberately reverse the key order so we prove the
            // frontend imposes BULL / BEAR / TRANSITION ordering
            // and doesn't rely on the JS object iteration order.
            TRANSITION: {
              weights: { T: 1.0 },
              equity_pct: 0.50, bond_pct: 0.50, cash_pct: 0,
            },
            BEAR: {
              weights: { B: 1.0 },
              equity_pct: 0.05, bond_pct: 0.95, cash_pct: 0,
            },
            BULL: {
              weights: { L: 1.0 },
              equity_pct: 0.95, bond_pct: 0.05, cash_pct: 0,
            },
          },
        },
      },
    })
    renderCard()
    await waitFor(() => expect(
      screen.getByTestId('cio-regime-blend-BULL')).toBeInTheDocument())
    const section = screen.getByTestId('cio-regime-blends-implied')
    const html = section.innerHTML
    // BULL must appear before BEAR before TRANSITION in the rendered
    // DOM regardless of the input dict's iteration order.
    expect(html.indexOf('cio-regime-blend-BULL'))
      .toBeLessThan(html.indexOf('cio-regime-blend-BEAR'))
    expect(html.indexOf('cio-regime-blend-BEAR'))
      .toBeLessThan(html.indexOf('cio-regime-blend-TRANSITION'))
  })
})
