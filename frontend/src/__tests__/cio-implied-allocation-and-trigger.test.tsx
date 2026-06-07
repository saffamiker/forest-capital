/**
 * CIO Recommendation card -- implied asset allocation + blend change
 * trigger (bridge #81).
 *
 * Three additions pinned here:
 *
 *  1. The "Blend" line is now labelled "Current Strategy Blend".
 *  2. A new "Implied Asset Allocation" line renders the equity / bond
 *     / cash split when the backend overlay carried it; omitted when
 *     the field is absent (cold strategy cache).
 *  3. A new "Blend Change Trigger" line renders the one-sentence
 *     guidance from the backend when the field is present.
 *
 *  Plus -- ordering: the divergence disclosure and the deterministic-
 *  fallback notice now render BETWEEN the confidence header and the
 *  blend block, not below it (pre-fix they sat at the bottom of the
 *  prose stack so the user often missed the regime divergence).
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
  confidence: { regime: 'BEAR', probability: 0.874, ess: 82.86, ess_warning: false },
  dissenting_view: 'Blend is constrained by the 40% ceiling.',
  key_risk: 'A rapid geopolitical de-escalation.',
  limitations: ['Three-asset universe.'],
  blend_weights: {
    VOL_TARGETING: 0.35, MIN_VARIANCE: 0.34, RISK_PARITY: 0.18, BENCHMARK: 0.05,
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

describe('CIO card -- bridge #81 additions', () => {
  it('renames the Blend line to Current Strategy Blend', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: true, recommendation: baseRec },
    })
    renderCard()
    const blend = await screen.findByTestId('cio-current-strategy-blend')
    expect(blend.textContent).toContain('Current Strategy Blend')
    expect(blend.textContent).toContain('VOL_TARGETING 35%')
    // The pre-fix bare "Blend: " label is gone.
    expect(blend.textContent).not.toMatch(/^Blend:/i)
  })

  it('renders Implied Asset Allocation when the overlay is present', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          implied_asset_allocation: {
            equity_pct: 0.42, bond_pct: 0.55, cash_pct: 0.03,
          },
        },
      },
    })
    renderCard()
    const line = await screen.findByTestId('cio-implied-asset-allocation')
    expect(line.textContent).toContain('Implied Asset Allocation')
    expect(line.textContent).toContain('Equity 42%')
    expect(line.textContent).toContain('Bonds 55%')
    expect(line.textContent).toContain('Cash 3%')
  })

  it('omits Implied Asset Allocation when the overlay is absent (cold cache)', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: true, recommendation: baseRec },
    })
    renderCard()
    await screen.findByTestId('cio-current-strategy-blend')
    expect(
      screen.queryByTestId('cio-implied-asset-allocation'),
    ).not.toBeInTheDocument()
  })

  it('renders Blend Change Trigger when the field is present', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          blend_change_trigger:
            'Blend de-risks further on a deeper bear signal (VIX sustained '
            + 'above 28) and re-risks when the HMM flips back to BULL.',
        },
      },
    })
    renderCard()
    const line = await screen.findByTestId('cio-blend-change-trigger')
    expect(line.textContent).toContain('Blend Change Trigger')
    expect(line.textContent).toContain('VIX sustained above 28')
  })

  it('omits Blend Change Trigger when the field is absent', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: true, recommendation: baseRec },
    })
    renderCard()
    await screen.findByTestId('cio-current-strategy-blend')
    expect(
      screen.queryByTestId('cio-blend-change-trigger'),
    ).not.toBeInTheDocument()
  })

  it('renders the divergence disclosure ABOVE the blend block', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          divergence_disclosure:
            'Note: live regime signal (BEAR at 87.4%) diverges from '
            + 'the blend regime (BULL).',
        },
      },
    })
    renderCard()
    const disclosure = await screen.findByTestId('cio-divergence-disclosure')
    const blend = await screen.findByTestId('cio-current-strategy-blend')

    // compareDocumentPosition returns Node.DOCUMENT_POSITION_FOLLOWING (4)
    // when the argument node FOLLOWS the receiver -- i.e. blend appears
    // later in the document than disclosure.
    expect(
      disclosure.compareDocumentPosition(blend)
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('renders the deterministic-fallback notice ABOVE the blend block', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          _model: 'deterministic_fallback',
        },
      },
    })
    renderCard()
    const notice = await screen.findByTestId(
      'cio-deterministic-fallback-notice')
    const blend = await screen.findByTestId('cio-current-strategy-blend')
    expect(
      notice.compareDocumentPosition(blend)
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('handles a recommendation with all three #81 fields populated', async () => {
    // End-to-end: a real-world response includes all three additions
    // plus the existing divergence disclosure. All four rows render
    // in the expected order.
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          divergence_disclosure: 'Models diverge.',
          implied_asset_allocation: {
            equity_pct: 0.40, bond_pct: 0.50, cash_pct: 0.10,
          },
          blend_change_trigger: 'Watch VIX above 28.',
        },
      },
    })
    renderCard()
    await waitFor(() => {
      expect(screen.getByTestId('cio-divergence-disclosure'))
        .toBeInTheDocument()
      expect(screen.getByTestId('cio-current-strategy-blend'))
        .toBeInTheDocument()
      expect(screen.getByTestId('cio-implied-asset-allocation'))
        .toBeInTheDocument()
      expect(screen.getByTestId('cio-blend-change-trigger'))
        .toBeInTheDocument()
    })
  })
})
