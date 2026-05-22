/**
 * explainer-followup.test.tsx — May 22 2026 (item 3 in the sprint queue).
 *
 * Pins the new ExplainerPanel follow-up thread + handoff package
 * contract:
 *   - Thread input renders below the static explanation when it lands
 *   - Submit fires POST /api/v1/council/explainer-followup with the
 *     full context (topic, content, thread, question)
 *   - SSE response is parsed into a CIO message in the thread
 *   - Exchange counter increments 1/3 → 2/3 → 3/3 and the input is
 *     replaced with the handoff prompt when the limit is reached
 *   - Ask the Council navigates to /council with the handoff package
 *     on route state (handoff_question + thread + topic + content)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import ExplainerPanel from '../components/ExplainerPanel'


function _streamingBodyFor(events: string[]) {
  // Build a fetch-Response-like body that streams the SSE frames.
  let i = 0
  return {
    getReader: () => ({
      read: () =>
        Promise.resolve(
          i >= events.length
            ? { done: true, value: undefined }
            : { done: false,
                value: new TextEncoder().encode(events[i++] + '\n\n') }),
    }),
  }
}

function mockFetchSequence(handlers: Array<() => Response | Promise<Response>>) {
  // First fetch: the /api/council/explain stream (the static
  // explainer). Second fetch onward: the follow-up endpoint. Each
  // handler is a function that returns the response to use for the
  // next call.
  let idx = 0
  return vi.fn().mockImplementation(() => {
    const handler = handlers[Math.min(idx, handlers.length - 1)]
    idx += 1
    return handler()
  })
}

function makeOkStream(events: string[]): Response {
  return {
    ok: true,
    status: 200,
    body: _streamingBodyFor(events),
  } as unknown as Response
}

const FOLLOWUP_OK_FRAMES = [
  `data: {"type":"chunk","text":"The Sharpe ratio compares excess return to volatility."}`,
  `data: {"type":"meta","exchanges_used":1,"suggest_council":false}`,
  `data: [DONE]`,
]

let locationProbe: { pathname: string; state: unknown } = { pathname: '', state: null }
function LocationProbe() {
  const loc = useLocation()
  locationProbe = { pathname: loc.pathname, state: loc.state }
  return null
}


beforeEach(() => {
  locationProbe = { pathname: '', state: null }
})

afterEach(() => {
  vi.restoreAllMocks()
})


describe('ExplainerPanel — CIO follow-up thread', () => {
  it('renders the thread input once the explainer content lands', async () => {
    const fetchMock = mockFetchSequence([
      () => makeOkStream(['Sharpe is a risk-adjusted return measure.']),
    ])
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <ExplainerPanel
          metricLabel="Sharpe Ratio"
          currentValue="0.63"
          onClose={() => {}}
        />
      </MemoryRouter>,
    )

    // Wait for the initial explain stream to land.
    await waitFor(() => {
      expect(screen.getByTestId('explainer-followup-input'))
        .toBeInTheDocument()
    })
    expect(screen.getByText(/0 of 3 follow-ups used/i)).toBeInTheDocument()
  })

  it('POSTs the follow-up with thread + topic context and appends CIO reply', async () => {
    const fetchMock = mockFetchSequence([
      () => makeOkStream(['Sharpe is a risk-adjusted return measure.']),
      () => makeOkStream(FOLLOWUP_OK_FRAMES),
    ])
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <ExplainerPanel
          metricLabel="Sharpe Ratio"
          currentValue="0.63"
          onClose={() => {}}
        />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('explainer-followup-input'))
        .toBeInTheDocument()
    })

    // Type a follow-up question.
    const input = screen.getByTestId('explainer-followup-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'How does it compare to Sortino?' } })

    await act(async () => {
      fireEvent.click(screen.getByTestId('explainer-followup-submit'))
    })

    // The user message lands in the thread immediately.
    await waitFor(() => {
      expect(screen.getByText(/How does it compare to Sortino/)).toBeInTheDocument()
    })
    // The CIO reply lands after the stream resolves.
    await waitFor(() => {
      expect(screen.getByText(/risk-adjusted return measure/i))
        .toBeInTheDocument()
    })

    // The second fetch call (the follow-up endpoint) carried the
    // full context.
    const [url, init] = fetchMock.mock.calls[1]
    expect(url).toBe('/api/v1/council/explainer-followup')
    const body = JSON.parse((init.body as string))
    expect(body.explainer_topic).toBe('Sharpe Ratio')
    expect(body.question).toBe('How does it compare to Sortino?')
    expect(body.thread).toEqual([])

    // The exchange counter has incremented.
    await waitFor(() => {
      expect(screen.getByText(/1 of 3 follow-ups used/i)).toBeInTheDocument()
    })
  })

  it('replaces the input with the council handoff prompt at the 3-exchange limit', async () => {
    // Three sequential follow-ups → input replaced.
    const followupHandlers = [0, 1, 2].map((i) => () =>
      makeOkStream([
        `data: {"type":"chunk","text":"answer ${i + 1}"}`,
        `data: {"type":"meta","exchanges_used":${i + 1},"suggest_council":false}`,
        `data: [DONE]`,
      ]))
    const fetchMock = mockFetchSequence([
      () => makeOkStream(['static.']),
      ...followupHandlers,
    ])
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter>
        <ExplainerPanel
          metricLabel="CAGR"
          onClose={() => {}}
        />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('explainer-followup-input'))
        .toBeInTheDocument()
    })

    for (let i = 0; i < 3; i++) {
      const input = screen.getByTestId('explainer-followup-input') as HTMLInputElement
      fireEvent.change(input, { target: { value: `q${i + 1}` } })
      await act(async () => {
        fireEvent.click(screen.getByTestId('explainer-followup-submit'))
      })
      await waitFor(() => {
        expect(screen.getByText(`answer ${i + 1}`)).toBeInTheDocument()
      })
    }

    // Input is gone; the limit-reached prompt is visible.
    expect(screen.queryByTestId('explainer-followup-input')).toBeNull()
    expect(
      screen.getByText(/You've used all 3 follow-ups/i),
    ).toBeInTheDocument()
  })
})


describe('ExplainerPanel — council handoff package', () => {
  it('navigates to /council with handoff package on route state', async () => {
    const fetchMock = mockFetchSequence([
      () => makeOkStream(['Sharpe is risk-adjusted return.']),
      () => makeOkStream([
        `data: {"type":"chunk","text":"sortino is similar"}`,
        `data: {"type":"meta","exchanges_used":1,"suggest_council":true}`,
        `data: [DONE]`,
      ]),
    ])
    vi.stubGlobal('fetch', fetchMock)

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <ExplainerPanel
          metricLabel="Sharpe Ratio"
          currentValue="0.63"
          chartContext={{
            name: 'strategy-table',
            values: { strategy: 'REGIME_SWITCHING' },
          }}
          onClose={() => {}}
        />
        <LocationProbe />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByTestId('explainer-followup-input'))
        .toBeInTheDocument()
    })

    // Run a follow-up so the thread is non-empty.
    const input = screen.getByTestId('explainer-followup-input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'compare to Sortino' } })
    await act(async () => {
      fireEvent.click(screen.getByTestId('explainer-followup-submit'))
    })
    await waitFor(() => {
      expect(screen.getByText(/sortino is similar/)).toBeInTheDocument()
    })

    // suggest_council=true → the inline prompt renders with a council
    // handoff link. Use the body's main "Ask the Council about this"
    // button which is always present.
    fireEvent.click(screen.getByText(/Ask the Council about this/i))

    expect(locationProbe.pathname).toBe('/council')
    const state = locationProbe.state as {
      prefillQuestion?: string
      handoff?: {
        explainer_topic?: string
        explainer_content?: string
        thread?: Array<{ role: string; content: string }>
        chart_context?: { name?: string }
      }
    }
    expect(state.prefillQuestion).toBe('compare to Sortino')
    expect(state.handoff).toBeTruthy()
    expect(state.handoff!.explainer_topic).toBe('Sharpe Ratio')
    expect(state.handoff!.explainer_content).toContain('risk-adjusted return')
    expect(state.handoff!.chart_context?.name).toBe('strategy-table')
    // Both the user question and the CIO reply travel in the package.
    const thread = state.handoff!.thread ?? []
    expect(thread.length).toBe(2)
    expect(thread[0].role).toBe('user')
    expect(thread[0].content).toBe('compare to Sortino')
    expect(thread[1].role).toBe('cio')
  })
})
