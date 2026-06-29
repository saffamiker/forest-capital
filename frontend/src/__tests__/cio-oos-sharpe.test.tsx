/**
 * cio-oos-sharpe.test.tsx -- June 15 2026.
 *
 * The CIO recommendation card renders an OOS Validation subsection
 * when the /api/v1/recommendation response carries oos_sharpe (the
 * submission-freeze Sharpe values for the blend and the benchmark
 * plus the value-add event counts from the play-by-play scorecard).
 * Omitted gracefully when the overlay is null (cold oos_summary
 * cache).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'

import CIORecommendationCard from '../components/CIORecommendationCard'
import { UIProvider } from '../context/UIContext'

vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
}

const baseRec = {
  signal: 'TRANSITION regime.',
  recommendation: 'Hold the regime blend.',
  confidence: {
    regime: 'TRANSITION', probability: 0.84,
    ess: 35, ess_warning: false,
  },
  dissenting_view: 'Dissent.',
  key_risk: 'Risk.',
  limitations: ['L.'],
  blend_weights: { BENCHMARK: 0.5, REGIME_SWITCHING: 0.5 },
  computed_at: '2026-06-15 12:00:00+00:00',
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


describe('CIO card -- OOS Sharpe subsection', () => {

  it('renders blend / benchmark with explicit delta when the overlay is present',
    async () => {
      mockedAxios.get.mockResolvedValue({
        data: {
          available: true,
          recommendation: {
            ...baseRec,
            oos_sharpe: {
              blend: 1.2442,
              benchmark: 0.7303,
              value_add_events: 2,
              total_events: 9,
            },
          },
        },
      })
      renderCard()
      const section = await screen.findByTestId('cio-oos-sharpe')
      // Section header.
      expect(section.textContent).toMatch(/OOS Sharpe \(submission lock\)/i)
      // Sharpe values rounded to 2 decimals (1.2442 -> 1.24,
      // 0.7303 -> 0.73).
      expect(section.textContent).toContain('1.24')
      expect(section.textContent).toContain('0.73')
      // Explicit-sign delta vs benchmark (+0.51 = 1.24 - 0.73).
      expect(section.textContent).toMatch(/\+0\.51 vs benchmark/)
    })

  it('renders the value-add events line', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          oos_sharpe: {
            blend: 1.24, benchmark: 0.73,
            value_add_events: 2, total_events: 9,
          },
        },
      },
    })
    renderCard()
    const evts = await screen.findByTestId('cio-oos-events')
    expect(evts.textContent).toMatch(
      /Blend outperformed at 2 of 9 rebalance events/)
  })

  it('omits the OOS subsection when oos_sharpe is null (cold cache)',
    async () => {
      mockedAxios.get.mockResolvedValue({
        data: {
          available: true,
          recommendation: { ...baseRec, oos_sharpe: null },
        },
      })
      renderCard()
      // Wait for the card to render past the loading state.
      await screen.findByTestId('cio-current-strategy-blend')
      expect(screen.queryByTestId('cio-oos-sharpe'))
        .not.toBeInTheDocument()
      expect(screen.queryByTestId('cio-oos-events'))
        .not.toBeInTheDocument()
    })

  it('omits the OOS subsection when the field is missing entirely',
    async () => {
      // Old API vintages (pre-overlay) never carried the field.
      mockedAxios.get.mockResolvedValue({
        data: { available: true, recommendation: { ...baseRec } },
      })
      renderCard()
      await screen.findByTestId('cio-current-strategy-blend')
      expect(screen.queryByTestId('cio-oos-sharpe'))
        .not.toBeInTheDocument()
    })

  it('omits the events line when total_events is zero', async () => {
    // Defensive: blend Sharpe present but the play-by-play has not
    // produced any events yet (empty scorecard). The main line
    // still renders; the secondary events line drops.
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          oos_sharpe: {
            blend: 1.24, benchmark: 0.73,
            value_add_events: 0, total_events: 0,
          },
        },
      },
    })
    renderCard()
    const section = await screen.findByTestId('cio-oos-sharpe')
    expect(section).toBeInTheDocument()
    expect(screen.queryByTestId('cio-oos-events'))
      .not.toBeInTheDocument()
  })

  it('renders a negative delta correctly (no double sign)', async () => {
    // Defensive: a hypothetical world where the blend underperformed
    // the benchmark. The delta still renders with a single minus
    // sign (-0.12), never "+-0.12".
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          oos_sharpe: {
            blend: 0.61, benchmark: 0.73,
            value_add_events: 1, total_events: 9,
          },
        },
      },
    })
    renderCard()
    const section = await screen.findByTestId('cio-oos-sharpe')
    expect(section.textContent).toMatch(/-0\.12 vs benchmark/)
    expect(section.textContent).not.toContain('+-')
  })
})
