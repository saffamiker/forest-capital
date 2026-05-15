import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import StrategyCard from '../components/StrategyCard'
import type { StrategyResult } from '../types/strategies'
import { UIProvider } from '../context/UIContext'
import { useGlossaryStore } from '../stores/glossaryStore'

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
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(document.body).toBeTruthy()
  })

  it('renders strategy name', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('VOL TARGETING')).toBeInTheDocument()
  })

  it('renders the strategy type badge', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('DYNAMIC')).toBeInTheDocument()
  })

  it('renders Sharpe ratio', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('0.83')).toBeInTheDocument()
  })

  it('renders the Sharpe 95% CI', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText(/0\.71.*0\.95/)).toBeInTheDocument()
  })

  it('renders max drawdown as negative percentage', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('-15.6%')).toBeInTheDocument()
  })

  it('shows SIGNIFICANT badge for significant strategy', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    const badges = screen.getAllByText('SIGNIFICANT')
    expect(badges.length).toBeGreaterThanOrEqual(1)
  })

  it('does not show SIGNIFICANT badge for non-significant strategy', () => {
    render(<UIProvider><StrategyCard strategy={benchmarkStrategy} /></UIProvider>)
    expect(screen.queryByText('SIGNIFICANT')).not.toBeInTheDocument()
  })

  it('renders tier 1 gates count', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText(/5\/5 Tier 1 gates/)).toBeInTheDocument()
  })

  it('renders CV stability score', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('0.81')).toBeInTheDocument()
  })

  it('shows "More detail" button initially', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('More detail')).toBeInTheDocument()
  })

  it('expands to show detailed stats on click', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    fireEvent.click(screen.getByText('More detail'))
    expect(screen.getByText('Less detail')).toBeInTheDocument()
    expect(screen.getByText(/Tier 1 Significance Tests/i)).toBeInTheDocument()
  })

  it('collapses back to summary view on second click', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    fireEvent.click(screen.getByText('More detail'))
    fireEvent.click(screen.getByText('Less detail'))
    expect(screen.getByText('More detail')).toBeInTheDocument()
  })

  it('calls onAskCouncil with strategy name when Ask button clicked', () => {
    const onAskCouncil = vi.fn()
    render(<UIProvider><StrategyCard strategy={mockStrategy} onAskCouncil={onAskCouncil} /></UIProvider>)
    fireEvent.click(screen.getByText(/Ask the Council about VOL TARGETING/i))
    expect(onAskCouncil).toHaveBeenCalledWith('VOL_TARGETING')
  })

  it('renders static type badge for static strategy', () => {
    render(<UIProvider><StrategyCard strategy={benchmarkStrategy} /></UIProvider>)
    expect(screen.getByText('STATIC')).toBeInTheDocument()
  })

  it('renders CAGR', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('9.5%')).toBeInTheDocument()
  })

  it('renders volatility', () => {
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    expect(screen.getByText('11.8%')).toBeInTheDocument()
  })
})


// ── ExplainableText label wraps (Explainer §2.4) ─────────────────────────────
//
// Each metric label is wrapped in ExplainableText with a stable term ID
// so a glossary lookup hits the same entry whether the click happens on
// the dashboard table header or here. ExplainableText renders an
// aria-label="Explain {term}" button in Commentary mode WHEN the
// glossary has a matching entry — in Analyst mode children pass through
// plainly; in Commentary mode without a glossary entry the chrome is a
// muted-state dotted-underline span instead.
//
// To exercise the interactive path we pre-seed the glossary store with
// stub entries for every metric term wrapped in StrategyCard.

const GLOSSARY_TERMS = [
  'sharpe_ratio', 'cagr', 'max_drawdown', 'volatility', 'cv_score',
  'tier1_gates', 'dsr', 'p_fdr',
]

function seedGlossary() {
  useGlossaryStore.setState({
    terms: Object.fromEntries(
      GLOSSARY_TERMS.map((t) => [t, { hover: `${t} hover`, what: 'What', why: 'Why' }]),
    ),
    parameters: {}, personas: {}, qa: {}, charts: {},
    termsLoaded: true, termsLoading: false, inflight: new Set<string>(),
  })
}

function renderInCommentary(node: React.ReactNode) {
  sessionStorage.setItem('fc_ui_mode', 'commentary')
  seedGlossary()
  return render(<UIProvider>{node}</UIProvider>)
}

describe('StrategyCard — ExplainableText metric labels', () => {
  beforeEach(() => {
    // Reset the store before every test to keep state isolated.
    useGlossaryStore.setState({
      terms: {}, parameters: {}, personas: {}, qa: {}, charts: {},
      termsLoaded: false, termsLoading: false, inflight: new Set<string>(),
    })
  })

  it('Sharpe Ratio label is wrapped with explain affordance', () => {
    renderInCommentary(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByLabelText('Explain sharpe_ratio')).toBeInTheDocument()
  })

  it('CAGR label is wrapped', () => {
    renderInCommentary(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByLabelText('Explain cagr')).toBeInTheDocument()
  })

  it('Max Drawdown label is wrapped', () => {
    renderInCommentary(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByLabelText('Explain max_drawdown')).toBeInTheDocument()
  })

  it('Volatility label is wrapped', () => {
    renderInCommentary(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByLabelText('Explain volatility')).toBeInTheDocument()
  })

  it('CV Stability Score label is wrapped', () => {
    renderInCommentary(<StrategyCard strategy={mockStrategy} />)
    expect(screen.getByLabelText('Explain cv_score')).toBeInTheDocument()
  })

  it('Tier 1 Significance Tests label is wrapped when expanded', () => {
    renderInCommentary(<StrategyCard strategy={mockStrategy} />)
    fireEvent.click(screen.getByText('More detail'))
    expect(screen.getByLabelText('Explain tier1_gates')).toBeInTheDocument()
  })

  it('in Analyst mode labels are plain text (no explain button)', () => {
    sessionStorage.setItem('fc_ui_mode', 'analyst')
    seedGlossary()
    render(<UIProvider><StrategyCard strategy={mockStrategy} /></UIProvider>)
    // Analyst mode → ExplainableText emits children directly, no aria-label.
    expect(screen.queryByLabelText('Explain sharpe_ratio')).toBeNull()
    // Plain label text is still there though.
    expect(screen.getByText('Sharpe Ratio')).toBeInTheDocument()
  })
})
