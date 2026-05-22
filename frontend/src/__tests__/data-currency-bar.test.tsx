/**
 * data-currency-bar.test.tsx — the data currency indicator.
 *
 * DataCurrencyBar reads GET /api/v1/admin/data-status and renders one of
 * three states: all-current, factor-model-lagging, or market-data-stale.
 * axios is mocked so each state can be exercised directly.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import axios from 'axios'

vi.mock('axios')

import DataCurrencyBar from '../components/DataCurrencyBar'
// useDataStatus now reads from a Zustand store (F3 fix, May 22 2026).
// The store is a module singleton, so a previous test's payload leaks
// into the next test unless reset.
import { useDataStatusStore } from '../stores/dataStatusStore'

interface Tbl {
  name: string
  row_count: number
  min_date: string | null
  max_date: string | null
  display_label: string | null
  last_updated: string | null
  staleness: string
}

function status(tables: Tbl[]) {
  return { data: { available: true, study_period: null, tables } }
}

const MARKET_CURRENT: Tbl = {
  name: 'market_data_monthly', row_count: 286,
  min_date: '2002-07-31', max_date: '2026-04-30',
  display_label: 'April 2026', last_updated: null, staleness: 'green',
}
const FF_CURRENT: Tbl = {
  name: 'ff_factors_monthly', row_count: 286,
  min_date: '2002-07-31', max_date: '2026-04-30',
  display_label: 'April 2026', last_updated: null, staleness: 'green',
}

beforeEach(() => {
  vi.clearAllMocks()
  // Reset the data-status store between tests — otherwise the first
  // test's payload satisfies isStale() for every subsequent test and
  // the new axios mock never fires.
  useDataStatusStore.setState({
    status: null, loading: false, fetchedAt: null,
  })
})

describe('DataCurrencyBar', () => {
  it('shows the all-current state when market and factor data agree', async () => {
    vi.mocked(axios.get).mockResolvedValue(status([MARKET_CURRENT, FF_CURRENT]))
    render(<DataCurrencyBar />)
    expect(await screen.findByText(
      /Data through April 2026 \(286 months · 2002-07 to 2026-04\)/,
    )).toBeInTheDocument()
  })

  it('flags the factor model when it lags the market data', async () => {
    const ffBehind: Tbl = {
      ...FF_CURRENT, row_count: 282, max_date: '2025-12-31',
      display_label: 'December 2025',
    }
    vi.mocked(axios.get).mockResolvedValue(status([MARKET_CURRENT, ffBehind]))
    render(<DataCurrencyBar />)
    expect(await screen.findByText(
      /Market data through April 2026 · Factor model through December 2025/,
    )).toBeInTheDocument()
    expect(screen.getByText(
      /Carhart loadings reflect data through December 2025/,
    )).toBeInTheDocument()
  })

  it('shows an amber warning when the market data is stale', async () => {
    const stale: Tbl = {
      ...MARKET_CURRENT, max_date: '2025-12-31',
      display_label: 'December 2025', staleness: 'red',
    }
    vi.mocked(axios.get).mockResolvedValue(status([stale, FF_CURRENT]))
    render(<DataCurrencyBar />)
    expect(await screen.findByText(
      /Market data through December 2025 · Last updated/,
    )).toBeInTheDocument()
  })

  it('renders nothing when the data status is unavailable', () => {
    vi.mocked(axios.get).mockResolvedValue({
      data: { available: false, study_period: null, tables: [] },
    })
    const { container } = render(<DataCurrencyBar />)
    expect(container).toBeEmptyDOMElement()
  })
})
