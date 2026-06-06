/**
 * The CIO recommendation card must surface the daily-vs-monthly HMM
 * divergence disclosure when /api/v1/recommendation overlays a
 * `divergence_disclosure` string on the payload — and stay silent
 * when the field is absent / null. The disclosure is a live overlay
 * (not baked into the cached prose) so the rendering test pins both
 * branches.
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
  recommendation: 'Concentrate in low-beta, tail-risk-managed strategies.',
  confidence: { regime: 'BEAR', probability: 0.874, ess: 82.86, ess_warning: false },
  dissenting_view: 'Blend is constrained by the 40% ceiling.',
  key_risk: 'A rapid geopolitical de-escalation.',
  limitations: ['Three-asset universe.'],
  blend_weights: { VOL_TARGETING: 0.35, MIN_VARIANCE: 0.34 },
  computed_at: '2026-06-06 01:24:18+00:00',
  model: 'claude-sonnet-4-6',
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

beforeEach(() => {
  mockedAxios.get = vi.fn()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('CIORecommendationCard divergence disclosure', () => {
  it('renders the disclosure when divergence_disclosure is present', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: {
          ...baseRec,
          divergence_disclosure:
            'Note: live regime signal (BEAR at 87.4%) diverges from '
            + 'the blend regime (BULL). Blend weights reflect the monthly '
            + 'model; the live label reflects the daily model.',
        },
      },
    })
    renderCard()
    const note = await screen.findByTestId('cio-divergence-disclosure')
    expect(note.textContent).toContain('BEAR at 87.4%')
    expect(note.textContent).toContain('BULL')
    expect(note.textContent).toContain('monthly model')
    expect(note.textContent).toContain('daily model')
  })

  it('does not render the disclosure when the field is absent', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: true, recommendation: baseRec },
    })
    renderCard()
    // Wait for the signal to render so the card has settled.
    await screen.findByText(/Bear regime posterior at 87%/)
    expect(
      screen.queryByTestId('cio-divergence-disclosure'),
    ).not.toBeInTheDocument()
  })

  it('does not render the disclosure when the field is null', async () => {
    mockedAxios.get.mockResolvedValue({
      data: {
        available: true,
        recommendation: { ...baseRec, divergence_disclosure: null },
      },
    })
    renderCard()
    await screen.findByText(/Bear regime posterior at 87%/)
    expect(
      screen.queryByTestId('cio-divergence-disclosure'),
    ).not.toBeInTheDocument()
  })

  it('also stays silent when recommendation is null (cold cache)', async () => {
    mockedAxios.get.mockResolvedValue({
      data: { available: false, recommendation: null },
    })
    renderCard()
    await waitFor(() => expect(mockedAxios.get).toHaveBeenCalled())
    expect(
      screen.queryByTestId('cio-divergence-disclosure'),
    ).not.toBeInTheDocument()
  })
})
