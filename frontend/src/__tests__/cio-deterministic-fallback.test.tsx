/**
 * The CIO recommendation card must surface the deterministic-fallback
 * notice when /api/v1/recommendation returns a payload whose `_model`
 * field equals "deterministic_fallback" — the sentinel emitted by the
 * Python pipeline when the LLM call failed and the user is being
 * shown the structured fail-open recommendation. The notice must be
 * absent when `_model` is a normal LLM model id (e.g.
 * `claude-sonnet-4-6`) or when the field is omitted entirely (legacy
 * cached rows before the marker was added).
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
  dissenting_view: 'Blend constrained by the 40% ceiling.',
  key_risk: 'A rapid geopolitical de-escalation.',
  limitations: ['Three-asset universe.'],
  blend_weights: { VOL_TARGETING: 0.35, MIN_VARIANCE: 0.34 },
  computed_at: '2026-06-06 01:24:18+00:00',
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

describe('CIORecommendationCard deterministic-fallback notice', () => {
  it('renders the notice when _model is "deterministic_fallback"', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: { ...baseRec, _model: 'deterministic_fallback' },
      },
    })
    renderCard()
    const note = await screen.findByTestId('cio-deterministic-fallback-notice')
    expect(note.textContent).toContain('Live regime unavailable')
    expect(note.textContent).toContain('last deterministic recommendation')
  })

  it('does not render the notice for a normal LLM model id', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: { ...baseRec, _model: 'claude-sonnet-4-6' },
      },
    })
    renderCard()
    await screen.findByText(/Bear regime posterior at 87%/)
    expect(
      screen.queryByTestId('cio-deterministic-fallback-notice'),
    ).not.toBeInTheDocument()
  })

  it('does not render the notice when _model is omitted (legacy cached rows)', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: true, recommendation: baseRec },
    })
    renderCard()
    await screen.findByText(/Bear regime posterior at 87%/)
    expect(
      screen.queryByTestId('cio-deterministic-fallback-notice'),
    ).not.toBeInTheDocument()
  })

  it('renders both the divergence disclosure and the fallback notice when both apply', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          _model: 'deterministic_fallback',
          divergence_disclosure:
            'Note: live regime signal (BEAR at 87.4%) diverges from '
            + 'the blend regime (BULL).',
        },
      },
    })
    renderCard()
    await screen.findByText(/Bear regime posterior at 87%/)
    await waitFor(() => {
      expect(
        screen.getByTestId('cio-divergence-disclosure'),
      ).toBeInTheDocument()
      expect(
        screen.getByTestId('cio-deterministic-fallback-notice'),
      ).toBeInTheDocument()
    })
  })
})
