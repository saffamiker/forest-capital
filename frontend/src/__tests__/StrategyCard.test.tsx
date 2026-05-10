import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import StrategyCard from '../components/StrategyCard'
import type { StrategyResult } from '../types/strategies'

const mockStrategy: StrategyResult = {
  strategy_name: 'VOL_TARGETING',
  strategy_type: 'dynamic',
  cagr: 0.095,
  total_return: 8.42,
  volatility: 0.118,
  max_drawdown: -0.156,
  drawdown_duration_days: 312,
  drawdown_recovery_days: 180,
  var_95: -0.014,
  cvar_95: -0.021,
  skewness: -0.32,
  kurtosis: 3.8,
  sharpe_ratio: 0.83,
  sortino_ratio: 1.21,
  calmar_ratio: 0.61,
  information_ratio: 0.44,
  omega_ratio: 1.72,
  alpha: 0.032,
  alpha_bps: 320,
  alpha_after_costs_bps: 270,
  beta: 0.61,
  r_squared: 0.71,
  avg_monthly_turnover: 0.12,
  avg_equity_weight: 0.74,
  avg_bond_weight: 0.26,
  is_economically_significant: true,
  min_viable_aum: 2500000,
  p_value_ttest: 0.003,
  p_value_sharpe_jk: 0.002,
  p_value_alpha: 0.001,
  p_value_corrected: 0.004,
  p_value_bootstrap: 0.002,
  normality_rejected: true,
  bootstrap_used: true,
  has_autocorrelation: false,
  is_stationary: true,
  is_adequately_powered: true,
  deflated_sharpe_ratio: 0.81,
  dsr_p_value: 0.003,
  probabilistic_sharpe_ratio: 0.94,
  sharpe_ci_95: [0.71, 0.95],
  spa_p_value: 0.002,
  passes_spa: true,
  oos_sharpe: 0.79,
  oos_cagr: 0.088,
  oos_p_value: 0.004,
  oos_significant: true,
  tier1_gates_passed: 5,
  is_significant: true,
  significance_summary: 'Passed all 5 Tier 1 gates at p < 0.005.',
  cv_stability_score: 0.81,
  stress_results: {
    GFC_2008:       { return: -0.18, max_dd: -0.183, vs_benchmark: 0.325 },
    COVID_2020:     { return: -0.08, max_dd: -0.142, vs_benchmark: 0.211 },
    RATE_HIKE_2022: { return: -0.06, max_dd: -0.112, vs_benchmark: 0.087 },
    DOTCOM_2000:    { return: -0.14, max_dd: -0.198, vs_benchmark: 0.310 },
    TAPER_TANTRUM:  { return: 0.02,  max_dd: -0.041, vs_benchmark: 0.043 },
    note: 'No p-values reported — insufficient observations for valid testing',
  },
}

const benchmarkStrategy: StrategyResult = {
  ...mockStrategy,
  strategy_name: 'BENCHMARK',
  strategy_type: 'static',
  sharpe_ratio: 0.61,
  cagr: 0.098,
  max_drawdown: -0.508,
  is_significant: false,
  tier1_gates_passed: 0,
  significance_summary: 'Did not pass any Tier 1 gates.',
  cv_stability_score: 0.42,
}

describe('StrategyCard', () => {
  it('renders without errors', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(document.body).toBeTruthy()
  })

  it('renders strategy name', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('VOL TARGETING')).toBeInTheDocument()
  })

  it('renders the strategy type badge', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('DYNAMIC')).toBeInTheDocument()
  })

  it('renders Sharpe ratio', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('0.83')).toBeInTheDocument()
  })

  it('renders the Sharpe 95% CI', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText(/0\.71.*0\.95/)).toBeInTheDocument()
  })

  it('renders max drawdown as negative percentage', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('-15.6%')).toBeInTheDocument()
  })

  it('shows SIGNIFICANT badge for significant strategy', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    const badges = screen.getAllByText('SIGNIFICANT')
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })

  it('does not show SIGNIFICANT badge for non-significant strategy', () => {
    render(<StrategyCard strategy={benchmarkStrategy} />)
    expect(screen.queryByText('SIGNIFICANT')).not.toBeInTheDocument()
  })

  it('renders tier 1 gates count', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText(/5\/5 Tier 1 gates/)).toBeInTheDocument()
  })

  it('renders CV stability score', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('0.81')).toBeInTheDocument()
  })

  it('shows "More detail" button initially', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('More detail')).toBeInTheDocument()
  })

  it('expands to show detailed stats on click', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    fireEvent.click(screen.getByText('More detail'))
    expect(screen.getByText('Less detail')).toBeInTheDocument()
    expect(screen.getByText(/Tier 1 Significance Tests/i)).toBeInTheDocument()
  })

  it('collapses back to summary view on second click', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    fireEvent.click(screen.getByText('More detail'))
    fireEvent.click(screen.getByText('Less detail'))
    expect(screen.getByText('More detail')).toBeInTheDocument()
  })

  it('calls onAskCouncil with strategy name when Ask button clicked', () => {
    const onAskCouncil = vi.fn()
    render(<StrategyCard strategy={mockStrategy} onAskCouncil={onAskCouncil} />)
    fireEvent.click(screen.getByText(/Ask the Council about VOL TARGETING/i))
    expect(onAskCouncil).toHaveBeenCalledWith('VOL_TARGETING')
  })

  it('renders static type badge for static strategy', () => {
    render(<StrategyCard strategy={benchmarkStrategy} />)
    expect(screen.getByText('STATIC')).toBeInTheDocument()
  })

  it('renders CAGR', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('9.5%')).toBeInTheDocument()
  })

  it('renders volatility', () => {
    render(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByText('11.8%')).toBeInTheDocument()
  })
})
