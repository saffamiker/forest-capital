import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import InfoIcon from '../components/InfoIcon'
import ExplainerPanel from '../components/ExplainerPanel'
import { EXPLAINER_TOOLTIPS } from '../constants/explainerTooltips'

describe('explainerTooltips content', () => {
  it('every tooltip key has non-empty content', () => {
    const keys = Object.keys(EXPLAINER_TOOLTIPS)
    expect(keys.length).toBeGreaterThan(0)
    for (const key of keys) {
      expect(typeof EXPLAINER_TOOLTIPS[key]).toBe('string')
      expect(EXPLAINER_TOOLTIPS[key].trim().length).toBeGreaterThan(0)
    }
  })
})

describe('InfoIcon', () => {
  afterEach(() => vi.useRealTimers())

  it('renders the ⓘ icon button', () => {
    render(<InfoIcon tooltipKey="cagr" metricLabel="CAGR" />)
    expect(screen.getByLabelText('Explain CAGR')).toBeInTheDocument()
  })

  it('renders nothing for an unknown tooltip key', () => {
    const { container } = render(
      <InfoIcon tooltipKey="not_a_real_key" metricLabel="Mystery" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the static tooltip on hover after the 300ms delay', () => {
    vi.useFakeTimers()
    render(<InfoIcon tooltipKey="cagr" metricLabel="CAGR" />)
    fireEvent.mouseEnter(screen.getByLabelText('Explain CAGR'))
    act(() => { vi.advanceTimersByTime(300) })
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('Compound Annual Growth Rate')
  })
})

describe('ExplainerPanel', () => {
  afterEach(() => vi.restoreAllMocks())

  it('calls POST /api/council/explain on mount', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
        }),
      },
    })
    vi.stubGlobal('fetch', fetchMock)

    render(
      <ExplainerPanel
        metricLabel="Sharpe Ratio"
        currentValue="0.63"
        onClose={() => {}}
      />,
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/council/explain')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string).metric).toBe('Sharpe Ratio')
  })
})
