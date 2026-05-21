import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import Dashboard from '../components/Dashboard'
import type { StrategyResult } from '../types/strategies'
import { useStrategiesStore } from '../stores/strategiesStore'
import { useRegimeStore } from '../stores/regimeStore'
import { UIProvider } from '../context/UIContext'
import { AuthContext } from '../App'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

function makeStrategy(overrides: Partial<StrategyResult> & { strategy_name: string; strategy_type: 'static' | 'dynamic' }): StrategyResult {
  return {
    cagr: 0.082,
    total_return: 5.2,
    volatility: 0.14,
    max_drawdown: -0.327,
    drawdown_duration_days: 400,
    drawdown_recovery_days: 300,
    var_95: -0.018,
    cvar_95: -0.026,
    skewness: -0.4,
    kurtosis: 4.1,
    sharpe_ratio: 0.79,
    sortino_ratio: 1.1,
    calmar_ratio: 0.5,
    information_ratio: 0.3,
    omega_ratio: 1.5,
    alpha: 0.02,
    alpha_bps: 200,
    alpha_after_costs_bps: 150,
    beta: 0.65,
    r_squared: 0.72,
    avg_monthly_turnover: 0.08,
    avg_equity_weight: 0.60,
    avg_bond_weight: 0.40,
    is_economically_significant: true,
    min_viable_aum: 1000000,
    p_value_ttest: 0.003,
    p_value_sharpe_jk: 0.004,
    p_value_alpha: 0.002,
    p_value_corrected: 0.004,
    p_value_bootstrap: 0.003,
    normality_rejected: false,
    bootstrap_used: false,
    has_autocorrelation: false,
    is_stationary: true,
    is_adequately_powered: true,
    deflated_sharpe_ratio: 0.76,
    dsr_p_value: 0.004,
    probabilistic_sharpe_ratio: 0.91,
    sharpe_ci_95: [0.64, 0.94],
    spa_p_value: 0.003,
    passes_spa: true,
    oos_sharpe: 0.72,
    oos_cagr: 0.075,
    oos_p_value: 0.004,
    oos_significant: true,
    tier1_gates_passed: 5,
    is_significant: true,
    significance_summary: 'All 5 Tier 1 gates passed.',
    cv_stability_score: 0.78,
    ...overrides,
  }
}

const MOCK_STRATEGIES: StrategyResult[] = [
  makeStrategy({ strategy_name: 'BENCHMARK',          strategy_type: 'static',  sharpe_ratio: 0.61, is_significant: false, tier1_gates_passed: 0 }),
  makeStrategy({ strategy_name: 'CLASSIC_60_40',      strategy_type: 'static',  sharpe_ratio: 0.79 }),
  makeStrategy({ strategy_name: 'RISK_PARITY',        strategy_type: 'static',  sharpe_ratio: 0.88 }),
  makeStrategy({ strategy_name: 'MIN_VARIANCE',       strategy_type: 'static',  sharpe_ratio: 0.74 }),
  makeStrategy({ strategy_name: 'EQUAL_WEIGHT',       strategy_type: 'static',  sharpe_ratio: 0.71, is_significant: false, tier1_gates_passed: 2 }),
  makeStrategy({ strategy_name: 'MOMENTUM_ROTATION',  strategy_type: 'dynamic', sharpe_ratio: 0.92 }),
  makeStrategy({ strategy_name: 'REGIME_SWITCHING',   strategy_type: 'dynamic', sharpe_ratio: 0.96 }),
  makeStrategy({ strategy_name: 'VOL_TARGETING',      strategy_type: 'dynamic', sharpe_ratio: 0.83 }),
  makeStrategy({ strategy_name: 'BLACK_LITTERMAN',    strategy_type: 'dynamic', sharpe_ratio: 0.94 }),
  makeStrategy({ strategy_name: 'MAX_SHARPE_ROLLING', strategy_type: 'dynamic', sharpe_ratio: 0.89 }),
]

const MOCK_REGIME = {
  threshold_regime: 'BULL',
  hmm_regime: 1,
  hmm_probabilities: [0.12, 0.80, 0.08],
  regimes_agree: true,
  vix_level: 18.4,
  yield_curve_slope: 0.42,
  credit_spread: 3.21,
  equity_trend: 0.08,
  pre_2022_avg_correlation: -0.31,
  post_2022_avg_correlation: 0.48,
}

const MOCK_FRONTIER = {
  frontier_points: Array.from({ length: 11 }, (_, i) => ({ volatility: 0.05 + i * 0.01, expected_return: 0.04 + i * 0.008 })),
  portfolio_points: MOCK_STRATEGIES.slice(0, 10).map((s) => ({ strategy: s.strategy_name, volatility: s.volatility, expected_return: s.cagr })),
  max_sharpe_point: { volatility: 0.10, expected_return: 0.09 },
}

function renderDashboard() {
  // UIProvider is needed because Dashboard renders LearnModeBanner, which
  // reads `mode` from useUI() to decide whether to show itself.
  // AuthContext is needed because Dashboard renders MacroResearchPanel
  // (FEATURE 2), which contains a TeamGate that reads useAuth().
  const authValue = {
    session: { token: 't', email: 'viewer@queens.edu', permissions: [] },
    isVerifying: false,
    login: vi.fn(),
    logout: vi.fn(),
  }
  return render(
    <MemoryRouter>
      <AuthContext.Provider value={authValue}>
        <UIProvider>
          <Dashboard />
        </UIProvider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

describe('Dashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset stores between tests — stores are singletons; without reset,
    // load() is a no-op once loaded=true and tests can't observe loading state
    useStrategiesStore.setState({ strategies: [], loading: false, error: null, loaded: false, lastFetchedAt: null })
    useRegimeStore.setState({ regime: null, loading: false, error: null, fetchedAt: null })
    mockedAxios.get = vi.fn()
      .mockImplementation((url: string) => {
        if (url === '/api/backtest/compare') return Promise.resolve({ data: { strategies: MOCK_STRATEGIES } })
        if (url === '/api/regime/current') return Promise.resolve({ data: MOCK_REGIME })
        // FEATURE 2 — MacroResearchPanel polls /api/v1/research/latest
        // on mount. Return an empty "no digest yet" payload so the panel
        // renders its empty state instead of throwing to the catch arm.
        if (url === '/api/v1/research/latest') {
          return Promise.resolve({ data: { digest: null, last_completed_at: null } })
        }
        return Promise.reject(new Error(`Unexpected GET: ${url}`))
      })
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { efficient_frontier: MOCK_FRONTIER } })
  })

  it('renders without errors', async () => {
    renderDashboard()
    await waitFor(() => expect(screen.queryByText(/loading portfolio data/i)).not.toBeInTheDocument())
  })

  it('shows loading state while fetch is in-flight', async () => {
    // Use a never-resolving promise so we can observe the loading state
    // before the axios mock settles
    mockedAxios.get = vi.fn().mockReturnValue(new Promise(() => {}))
    renderDashboard()
    await waitFor(() =>
      expect(screen.getByText(/loading portfolio data/i)).toBeInTheDocument()
    )
  })

  it('renders strategy table with 10 rows after load', async () => {
    renderDashboard()
    await waitFor(() => {
      const rows = screen.getAllByText(/DYNAMIC|STATIC/)
      expect(rows).toHaveLength(10)
    })
  })

  it('renders the regime indicator after load', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getAllByText('BULL').length).toBeGreaterThan(0)
    })
  })

  it('renders the 2022 correlation breakdown warning', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(/2022 Equity-Bond Correlation Breakdown/i)).toBeInTheDocument()
    })
  })

  it('renders summary metric tiles after load', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(/significant strategies/i)).toBeInTheDocument()
    })
  })

  it('shows BENCHMARK strategy name in table', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getAllByText('BENCHMARK').length).toBeGreaterThan(0)
    })
  })

  it('renders strategy names with underscores replaced by spaces', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getAllByText('VOL TARGETING').length).toBeGreaterThan(0)
    })
  })

  it('renders SIG badge for significant strategies', async () => {
    renderDashboard()
    await waitFor(() => {
      const sigBadges = screen.getAllByText('SIG')
      expect(sigBadges.length).toBeGreaterThan(0)
    })
  })

  it('renders the efficient frontier chart section', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText(/efficient frontier/i)).toBeInTheDocument()
    })
  })
})
