/**
 * pipeline-step-detail.test.tsx — scope-blockers commit contract.
 *
 * Verifies the inline step-detail expansion + the Step 3 / Step 4
 * fallback handling shipped in the blockers commit.
 *
 *   1. Each terminal-status step shows a View details toggle.
 *   2. Toggle expands an inline detail panel keyed by step number.
 *   3. Step 2 detail renders the citation table with concept/state/
 *      source columns.
 *   4. Step 3 detail handles empty activity (no rows shipped) and
 *      groups counts by member when present.
 *   5. Step 4 detail switches to "no audit on record" when the
 *      payload carries _no_audit (the new fallback).
 *   6. Step 6 detail renders one card per condition with PASS/FAIL.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import PipelineGate from '../components/reportwriter/PipelineGate'
import type {
  StepResult, StepResults,
} from '../components/reportwriter/PipelineGate'


function _baseResults(overrides: Partial<StepResults>): StepResults {
  // Mark Step 1 complete so 2/3/4 are not gated by prerequisites.
  return {
    1: { status: 'complete', message: 'Done', payload: {} },
    ...overrides,
  } as StepResults
}


function _renderGate(results: StepResults) {
  // May 24 2026 RW1 — Step 4 detail now uses useNavigate to drive
  // the "Run QA Audit" CTA button. Tests must wrap in a Router.
  return render(
    <MemoryRouter>
      <PipelineGate
        results={results}
        generating={false}
        generateDisabledReason={null}
        onRunStep={vi.fn()}
        onGenerate={vi.fn()}
      />
    </MemoryRouter>,
  )
}


describe('PipelineGate — step detail expansion', () => {
  it('shows View details toggle on a complete step with payload', () => {
    _renderGate(_baseResults({
      2: { status: 'complete', message: '8 verified',
           payload: { citations: { ssr1: {
             verification_status: 'verified',
             author: 'Markowitz', year: '1952',
             url: 'https://example.com',
           }}}} as StepResult,
    }))
    expect(
      screen.getByTestId('pipeline-step-2-expand'),
    ).toBeInTheDocument()
  })

  it('does NOT show View details for an idle step', () => {
    _renderGate({})
    expect(
      screen.queryByTestId('pipeline-step-1-expand'),
    ).not.toBeInTheDocument()
  })

  it('opens the detail modal on click; close X dismisses it', () => {
    // Item 3 (May 23 2026): the inline expansion was replaced with a
    // modal so wide detail tables can show full width instead of
    // truncating to fit the sidebar. The button still says "View
    // details" and the detail container still carries the
    // pipeline-step-N-detail testid — but a second click no longer
    // toggles, the close X is the explicit dismissal.
    _renderGate(_baseResults({
      2: { status: 'complete', message: '',
           payload: { citations: {} } } as StepResult,
    }))
    const btn = screen.getByTestId('pipeline-step-2-expand')
    expect(
      screen.queryByTestId('pipeline-step-2-detail'),
    ).not.toBeInTheDocument()
    fireEvent.click(btn)
    expect(
      screen.getByTestId('pipeline-step-2-modal'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('pipeline-step-2-detail'),
    ).toBeInTheDocument()
    // Close via the explicit X button.
    fireEvent.click(
      screen.getByTestId('pipeline-step-2-modal-close'))
    expect(
      screen.queryByTestId('pipeline-step-2-modal'),
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId('pipeline-step-2-detail'),
    ).not.toBeInTheDocument()
  })

  it('backdrop click closes the modal', () => {
    _renderGate(_baseResults({
      2: { status: 'complete', message: '',
           payload: { citations: {} } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-2-expand'))
    const modal = screen.getByTestId('pipeline-step-2-modal')
    // The backdrop is the testid'd container itself; clicking it
    // closes (the inner role="dialog" stops propagation).
    fireEvent.click(modal)
    expect(
      screen.queryByTestId('pipeline-step-2-modal'),
    ).not.toBeInTheDocument()
  })

  it('escape key closes the modal', () => {
    _renderGate(_baseResults({
      2: { status: 'complete', message: '',
           payload: { citations: {} } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-2-expand'))
    expect(
      screen.getByTestId('pipeline-step-2-modal'),
    ).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(
      screen.queryByTestId('pipeline-step-2-modal'),
    ).not.toBeInTheDocument()
  })

  it('modal carries the step name + status pill', () => {
    _renderGate(_baseResults({
      2: { status: 'warning', message: '',
           payload: { citations: {} } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-2-expand'))
    const modal = screen.getByTestId('pipeline-step-2-modal')
    // Step 2 default label is "Source Citations".
    expect(modal.textContent).toMatch(/Source Citations/i)
    // Status pill mirrors the result status (warning).
    expect(modal.textContent).toMatch(/warning/i)
  })
})


describe('Step 2 — citation detail table', () => {
  it('renders one row per concept with state/source/url', () => {
    _renderGate(_baseResults({
      2: { status: 'warning', message: '',
           payload: {
             citations: {
               coherent_risk: {
                 concept_id: 'coherent_risk',
                 verification_status: 'verified',
                 author: 'Artzner et al.',
                 year: '1999',
                 url: 'https://example.com/artzner',
               },
               sharpe_ratio: {
                 concept_id: 'sharpe_ratio',
                 verification_status: 'not_found',
                 search_query_used: 'Sharpe ratio mutual fund',
               },
               gips_verification: {
                 concept_id: 'gips_verification',
                 verification_status: 'untrusted_source',
                 author: 'Some Blog',
                 year: '2024',
                 url: 'https://untrusted.example.org',
               },
             },
             verified_count: 1, concept_count: 3, quality: 'red',
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-2-expand'))
    const detail = screen.getByTestId('pipeline-step-2-detail')
    expect(detail).toHaveTextContent('coherent_risk')
    expect(detail).toHaveTextContent('sharpe_ratio')
    expect(detail).toHaveTextContent('gips_verification')
    expect(detail).toHaveTextContent('Artzner et al.')
    expect(detail).toHaveTextContent('Sharpe ratio mutual fund')
    expect(detail).toHaveTextContent('verified')
    expect(detail).toHaveTextContent('untrusted')
    expect(detail).toHaveTextContent('not found')
  })

  it('renders quality pill correctly for each tier', () => {
    _renderGate(_baseResults({
      2: { status: 'complete', message: '',
           payload: {
             citations: {}, verified_count: 8,
             concept_count: 10, quality: 'green',
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-2-expand'))
    const detail = screen.getByTestId('pipeline-step-2-detail')
    expect(detail).toHaveTextContent('Quality: green')
    expect(detail).toHaveTextContent('8 verified')
    expect(detail).toHaveTextContent('2 need action')
  })
})


describe('Step 3 — team activity detail', () => {
  it('renders per-member rows when activity is populated', () => {
    _renderGate(_baseResults({
      3: { status: 'complete', message: '50 UAT steps',
           payload: {
             activity: {
               michael_commits: 42,
               bob_uat_steps: 25,
               molly_uat_steps: 25,
               team_total_uat_steps: 50,
             },
             cross_check_flags: [],
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-3-expand'))
    const detail = screen.getByTestId('pipeline-step-3-detail')
    expect(detail).toHaveTextContent('Michael')
    expect(detail).toHaveTextContent('Bob')
    expect(detail).toHaveTextContent('Molly')
    expect(detail).toHaveTextContent('42')
    expect(detail).toHaveTextContent('25')
    expect(detail).toHaveTextContent('50')
  })

  it('renders empty-state message when activity has no rows', () => {
    _renderGate(_baseResults({
      3: { status: 'complete',
           message: 'No activity recorded yet',
           payload: { activity: {}, cross_check_flags: [] } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-3-expand'))
    expect(screen.getByTestId('pipeline-step-3-detail'))
      .toHaveTextContent(/fresh deployment/i)
  })

  it('surfaces cross-check flags when present', () => {
    _renderGate(_baseResults({
      3: { status: 'warning', message: '',
           payload: {
             activity: { michael_commits: 1, team_total_uat_steps: 5 },
             cross_check_flags: [
               'Bob 2 + Molly 1 = 3 ≠ platform total 5',
             ],
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-3-expand'))
    expect(screen.getByTestId('pipeline-step-3-detail'))
      .toHaveTextContent(/Bob 2/)
  })
})


describe('Step 4 — validation data fallback', () => {
  it('renders the no-audit fallback when _no_audit is set', () => {
    // May 24 2026 RW1 — Step 4 with _no_audit is now WARNING
    // (was previously falsely-complete). The detail panel
    // explains the gate + carries a Run QA Audit CTA button.
    _renderGate(_baseResults({
      4: { status: 'warning',
           message: 'No audit on record — run QA Audit before generation',
           payload: { _no_audit: true } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-4-expand'))
    const detail = screen.getByTestId('pipeline-step-4-detail')
    expect(detail).toHaveTextContent(/No QA audit has been run yet/i)
    expect(detail).toHaveTextContent(/Step 7/)
    expect(detail).toHaveTextContent(/before generating/i)
    // The Run QA Audit CTA button is the actionable affordance.
    expect(screen.getByTestId('step4-run-qa-audit')).toBeInTheDocument()
  })

  it('renders the audit status when present', () => {
    _renderGate(_baseResults({
      4: { status: 'complete', message: 'Statistical audit: pass',
           payload: {
             statistical_status: 'pass',
             passed: 39, failed: 0, warning: 2,
             run_at: '2026-05-22T10:00:00Z',
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-4-expand'))
    const detail = screen.getByTestId('pipeline-step-4-detail')
    expect(detail).toHaveTextContent('Stat: pass')
    expect(detail).toHaveTextContent('39 passed')
    expect(detail).toHaveTextContent('2 warnings')
    expect(detail).toHaveTextContent('2026-05-22')
  })
})


describe('Step 6 — thesis condition cards', () => {
  it('renders one card per condition with PASS/FAIL', () => {
    _renderGate(_baseResults({
      6: { status: 'complete', message: '',
           payload: {
             passed: true,
             conditions: [
               { id: 'benchmark_not_first',
                 description: 'Benchmark not top ranked',
                 value: 6, threshold: 1, passed: true },
               { id: 'material_corr_shift',
                 description: 'Material correlation shift',
                 value: 0.66, threshold: 0.30, passed: true },
               { id: 'meaningful_dd_reduction',
                 description: 'Meaningful drawdown reduction',
                 value: 0.20, threshold: 0.10, passed: true },
             ],
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-6-expand'))
    const detail = screen.getByTestId('pipeline-step-6-detail')
    expect(detail).toHaveTextContent('Benchmark not top ranked')
    expect(detail).toHaveTextContent('Material correlation shift')
    expect(detail).toHaveTextContent('Meaningful drawdown reduction')
    // Three PASS pills.
    expect(detail.querySelectorAll('[data-testid^="step6-condition-"]'))
      .toHaveLength(3)
  })

  it('marks a failing condition with FAIL styling', () => {
    _renderGate(_baseResults({
      6: { status: 'failed', message: '',
           payload: {
             passed: false,
             conditions: [
               { id: 'benchmark_not_first',
                 description: 'Benchmark not top ranked',
                 value: 1, threshold: 1, passed: false },
             ],
             blocker_reasons: ['benchmark is first'],
           } } as StepResult,
    }))
    fireEvent.click(screen.getByTestId('pipeline-step-6-expand'))
    const card = screen.getByTestId('step6-condition-benchmark_not_first')
    expect(card).toHaveTextContent('FAIL')
  })
})
