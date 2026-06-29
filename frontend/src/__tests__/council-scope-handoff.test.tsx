/**
 * Page-scoped council context — Part 4 frontend tests.
 *
 * Each of the three council-facing tiles carries an "Ask about this"
 * button that navigates to /council with the page's contextScope and a
 * pre-populated (editable) question in route state. These tests assert
 * the navigation target, the scope, and the question for each tile.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'

import CIORecommendationCard from '../components/CIORecommendationCard'
import ForwardConfidenceChart from '../components/ForwardConfidenceChart'
import PerformanceRecordLink from '../components/PerformanceRecordLink'

const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }))

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateMock }
})

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

// Minimal payloads so each card renders its available branch (and thus
// the "Ask about this" button).
const PAYLOADS: Record<string, unknown> = {
  '/api/v1/recommendation': {
    available: true,
    recommendation: {
      signal: 'defensive', recommendation: 'hold blend',
      confidence: { regime: 'TRANSITION', probability: 0.7 },
      dissenting_view: 'd', key_risk: 'k', limitations: ['l1'],
      computed_at: '2026-05-29T00:00:00Z',
    },
  },
  '/api/v1/forward-projection': {
    available: true,
    projection: {
      horizons_months: [1],
      bands: { blend: { '1': { median: 0.1, p05: 0, p95: 0.2 } } },
      p_outperform: { benchmark: { '1': 0.6 } },
      regime: 'TRANSITION', regime_probability: 0.7,
      _computed_at: '2026-05-29T00:00:00Z',
    },
  },
  '/api/v1/play-by-play': {
    available: true,
    scorecard: { n_total: 9, n_evaluable: 9, n_value_added: 2, framing: 'f' },
  },
}

beforeEach(() => {
  navigateMock.mockClear()
  mockedAxios.get = vi.fn().mockImplementation((url: string) =>
    Promise.resolve({ data: PAYLOADS[url] ?? { available: false } }))
})

async function clickAsk() {
  const btn = await screen.findByRole('button', { name: /ask about this/i })
  fireEvent.click(btn)
}

describe('council-facing tile hand-offs', () => {
  it('CIO recommendation tile → recommendation scope', async () => {
    render(<MemoryRouter><CIORecommendationCard /></MemoryRouter>)
    await clickAsk()
    expect(navigateMock).toHaveBeenCalledWith('/council', {
      state: {
        prefillQuestion: 'Why is the blend positioned defensively right now?',
        contextScope: 'recommendation',
      },
    })
  })

  it('forward confidence tile → prediction scope', async () => {
    render(<MemoryRouter><ForwardConfidenceChart /></MemoryRouter>)
    await clickAsk()
    expect(navigateMock).toHaveBeenCalledWith('/council', {
      state: {
        prefillQuestion:
          'What drives the outperformance probability at 12 months?',
        contextScope: 'prediction',
      },
    })
  })

  it('performance record tile → performance scope', async () => {
    render(<MemoryRouter><PerformanceRecordLink /></MemoryRouter>)
    await clickAsk()
    expect(navigateMock).toHaveBeenCalledWith('/council', {
      state: {
        prefillQuestion:
          'How does 2/9 event accuracy reconcile with cumulative outperformance?',
        contextScope: 'performance',
      },
    })
  })

  it('store forwards context_scope only when provided', async () => {
    // res.ok=false short-circuits after the request is sent, so we can
    // inspect the body without driving the full SSE stream.
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false, status: 500, body: null, json: async () => ({}),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { useCouncilStore } = await import('../stores/councilStore')

    await useCouncilStore.getState().runQuery('scoped', 'recommendation')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      query: 'scoped', context_scope: 'recommendation',
    })

    useCouncilStore.setState({ loading: false })
    await useCouncilStore.getState().runQuery('plain')
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      query: 'plain',
    })

    vi.unstubAllGlobals()
  })
})
