/**
 * portfolio-profile-panel.test.tsx — Item 9 Commit 3 contract.
 *
 * Two surfaces under test:
 *   - useCharacterisationsStore: one fetch per session, fail-open
 *     on axios error, byId keyed by strategy_id.
 *   - PortfolioProfilePanel: three cards render the documented fields
 *     in the documented sections, with a graceful empty state when the
 *     characterisation row is missing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

import {
  useCharacterisationsStore,
} from '../stores/strategyCharacterisationsStore'
import { PortfolioProfilePanel } from '../components/PortfolioProfilePanel'


const SAMPLE_ROW = {
  strategy_id: 'VOL_TARGETING',
  construction_summary:
    'Volatility Targeting scales the equity weight monthly so the '
    + 'portfolio targets 10% annualised volatility using the trailing '
    + '21-day realised vol.',
  portfolio_characteristics: {
    avg_holdings: 2.0,
    avg_turnover_pct: 38.5,
    avg_concentration: 64.0,
    rebalance_frequency: 'monthly',
  },
  behavioural_profile: {
    outperforms_when: 'Realised volatility is stable and trending.',
    underperforms_when: 'Volatility regime shifts quickly.',
    primary_risk_factor: 'Market (MKT-RF)',
    diversification_role:
      'Holds portfolio risk roughly constant — adds a vol-target '
      + 'sleeve the other strategies do not.',
  },
  regime_sensitivity:
    'De-risks into turbulent periods and re-risks into calm ones.',
  behavioural_tag:
    'Adaptive — targets constant volatility',
}


function renderInRouter(node: React.ReactNode) {
  return render(<MemoryRouter>{node}</MemoryRouter>)
}


beforeEach(() => {
  // Reset the store between tests — Zustand stores are module
  // singletons.
  useCharacterisationsStore.setState({
    byId: {}, loading: false, loaded: false,
    fetchedAt: null, available: false,
  })
  mockedAxios.get = vi.fn().mockResolvedValue({
    data: {
      available: true,
      data_hash: 'test_hash',
      strategies: [SAMPLE_ROW],
    },
  })
})


// ── Store contract ────────────────────────────────────────────────────────────

describe('useCharacterisationsStore', () => {
  it('loads once per session — repeated load() calls share one fetch',
    async () => {
      const calls = Array.from({ length: 5 }, () =>
        useCharacterisationsStore.getState().load())
      await Promise.all(calls)
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
      expect(mockedAxios.get).toHaveBeenCalledWith(
        '/api/v1/strategies/characterisations')
      expect(useCharacterisationsStore.getState().available).toBe(true)
      expect(useCharacterisationsStore.getState().byId.VOL_TARGETING)
        .toBeDefined()
    })

  it('subsequent load() after loaded is a no-op', async () => {
    await useCharacterisationsStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    await useCharacterisationsStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('reload() forces a fresh fetch', async () => {
    await useCharacterisationsStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    await useCharacterisationsStore.getState().reload()
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })

  it('fail-open: an axios error leaves available=false without throwing',
    async () => {
      mockedAxios.get = vi.fn().mockRejectedValue(new Error('500'))
      await useCharacterisationsStore.getState().load()
      expect(useCharacterisationsStore.getState().available).toBe(false)
      expect(useCharacterisationsStore.getState().byId).toEqual({})
    })

  it('endpoint returns no rows -> available=false, byId empty',
    async () => {
      mockedAxios.get = vi.fn().mockResolvedValue({
        data: { available: false, strategies: [],
                note: 'not yet computed' },
      })
      await useCharacterisationsStore.getState().load()
      expect(useCharacterisationsStore.getState().available).toBe(false)
      expect(useCharacterisationsStore.getState().byId).toEqual({})
    })
})


// ── Panel rendering ───────────────────────────────────────────────────────────

describe('PortfolioProfilePanel — three cards', () => {
  it('renders all three cards with the documented field placements',
    async () => {
      renderInRouter(<PortfolioProfilePanel strategyId="VOL_TARGETING" />)
      await waitFor(() => expect(
        screen.getByTestId('portfolio-profile-panel')).toBeInTheDocument())

      // Card 1 — How it's built
      expect(screen.getByTestId('profile-card-how-its-built'))
        .toBeInTheDocument()
      expect(screen.getByTestId('profile-construction-summary')
        .textContent).toContain('Volatility Targeting scales')
      // Stat row figures
      const card1 = screen.getByTestId('profile-card-how-its-built')
      expect(card1.textContent).toContain('2.0 avg')   // Holdings
      expect(card1.textContent).toContain('38.5%')      // Turnover
      expect(card1.textContent).toContain('64% avg')    // Largest holding
      expect(card1.textContent).toContain('monthly')    // Rebalances

      // Card 2 — Performance conditions
      expect(screen.getByTestId('profile-card-performance-conditions'))
        .toBeInTheDocument()
      expect(screen.getByTestId('profile-outperforms-column').textContent)
        .toContain('Realised volatility is stable')
      expect(screen.getByTestId('profile-underperforms-column').textContent)
        .toContain('Volatility regime shifts quickly')
      expect(screen.getByTestId('profile-regime-sensitivity').textContent)
        .toContain('De-risks into turbulent periods')

      // Card 3 — Role in the portfolio
      expect(screen.getByTestId('profile-card-role')).toBeInTheDocument()
      expect(screen.getByTestId('profile-diversification-role').textContent)
        .toContain('vol-target sleeve')
      // Primary risk factor badge — text + link target
      const badge = screen.getByTestId('profile-primary-risk-factor')
      expect(badge.textContent).toContain('Market (MKT-RF)')
      expect(badge.getAttribute('href')).toBe('/analytics#factor-loadings')
    })

  it('renders the empty state when characterisation is missing',
    async () => {
      mockedAxios.get = vi.fn().mockResolvedValue({
        data: { available: false, strategies: [] },
      })
      renderInRouter(<PortfolioProfilePanel strategyId="VOL_TARGETING" />)
      await waitFor(() => expect(
        screen.getByTestId('portfolio-profile-empty')).toBeInTheDocument())
      expect(screen.getByText(/Portfolio Profile not yet computed/))
        .toBeInTheDocument()
    })

  it('humanises strategyId for the empty-state heading when no name is given',
    async () => {
      mockedAxios.get = vi.fn().mockResolvedValue({
        data: { available: false, strategies: [] },
      })
      renderInRouter(<PortfolioProfilePanel strategyId="MAX_SHARPE_ROLLING" />)
      await waitFor(() => expect(
        screen.getByTestId('portfolio-profile-empty')).toBeInTheDocument())
      // 'MAX_SHARPE_ROLLING' -> 'MAX SHARPE ROLLING'
      expect(screen.getByTestId('portfolio-profile-empty').textContent)
        .toContain('MAX SHARPE ROLLING')
    })
})
