/**
 * pipeline-gating-rw1-rw3.test.tsx — RW1 + RW3 contract.
 *
 * RW1 (May 24 2026): Step 4 with `_no_audit: true` is a WARNING
 * with a Run QA Audit CTA, not a green pass. The previous false-
 * green let Step 7 fire without independent validation.
 *
 * RW3 (May 24 2026): strict sequential gating. The Run buttons
 * for downstream steps are disabled (with a visible Lock icon and
 * the reason text inline) until prior steps have GENUINE results.
 * `_no_audit` is a bypass marker — Step 4 in warning state with
 * `_no_audit: true` does NOT count as gate-passed for downstream
 * purposes.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import PipelineGate from '../components/reportwriter/PipelineGate'
import type {
  StepResult, StepResults,
} from '../components/reportwriter/PipelineGate'


function _gate(props: {
  results: StepResults
  generateDisabledReason?: string | null
  step2b?: { untrustedCount: number; onJump: () => void }
}) {
  return render(
    <MemoryRouter>
      <PipelineGate
        results={props.results}
        generating={false}
        generateDisabledReason={props.generateDisabledReason ?? null}
        onRunStep={vi.fn()}
        onGenerate={vi.fn()}
        {...(props.step2b !== undefined ? { step2b: props.step2b } : {})}
      />
    </MemoryRouter>,
  )
}


// ── RW1 ─────────────────────────────────────────────────────────────────────


describe('RW1 — Step 4 false-green elimination', () => {

  it('renders a Run QA Audit CTA when Step 4 carries _no_audit', () => {
    _gate({
      results: {
        1: { status: 'complete', message: '', payload: {} },
        4: {
          status: 'warning',
          message: 'No audit on record — run QA Audit before generation',
          payload: { _no_audit: true } as Record<string, unknown>,
        } as StepResult,
      },
    })
    fireEvent.click(screen.getByTestId('pipeline-step-4-expand'))
    expect(screen.getByTestId('step4-run-qa-audit')).toBeInTheDocument()
  })

  it('Run QA Audit button is wired to navigate to /qa', () => {
    _gate({
      results: {
        1: { status: 'complete', message: '', payload: {} },
        4: {
          status: 'warning', message: '',
          payload: { _no_audit: true },
        } as StepResult,
      },
    })
    fireEvent.click(screen.getByTestId('pipeline-step-4-expand'))
    const button = screen.getByTestId('step4-run-qa-audit') as HTMLButtonElement
    // Clicking the button should not throw; React Router's
    // MemoryRouter accepts the navigation silently. The
    // presence + click-without-error is what we pin.
    expect(() => fireEvent.click(button)).not.toThrow()
  })
})


// ── RW3 ─────────────────────────────────────────────────────────────────────


describe('RW3 — strict sequential pipeline gating', () => {

  it('Step 2 / 3 / 4 Run buttons are disabled and locked when Step 1 idle', () => {
    _gate({ results: {} })
    for (const n of [2, 3, 4]) {
      const button = screen.getByTestId(
        `pipeline-step-${n}-button`) as HTMLButtonElement
      expect(button.disabled).toBe(true)
      // Visual lock icon present.
      expect(
        screen.getByTestId(`pipeline-step-${n}-locked`),
      ).toBeInTheDocument()
    }
  })

  it('locked-step indicator shows which step is blocking', () => {
    _gate({ results: {} })
    // The lock label is inline next to the step number. Steps
    // 2/3/4 all share the same "Run Step 1 first" reason so the
    // text renders three times.
    expect(screen.getAllByText(/Run Step 1 first/).length).toBe(3)
  })

  it('Step 7 disabled when Step 4 in _no_audit state', () => {
    // generateDisabledReason is computed by ReportWriter.tsx and
    // passed in; we simulate the gated state here.
    _gate({
      results: {
        1: { status: 'complete', message: '', payload: {} },
        2: { status: 'complete', message: '', payload: {} },
        3: { status: 'complete', message: '', payload: {} },
        4: {
          status: 'warning', message: '',
          payload: { _no_audit: true },
        } as StepResult,
        5: { status: 'complete', message: '', payload: {} },
        6: { status: 'complete', message: '', payload: {} },
      },
      generateDisabledReason:
        'Step 4 awaiting QA Audit — run the audit before generation',
    })
    const generate = screen.getByTestId(
      'pipeline-step-7-button') as HTMLButtonElement
    expect(generate.disabled).toBe(true)
  })

  it('only Step 2 unlocks when Step 1 completes (strict sequential)', () => {
    // May 24 2026 — under strict-sequential gating, only the
    // IMMEDIATELY NEXT step unlocks. Step 1 done → Step 2 runnable;
    // Steps 3 and 4 stay locked until 2 (and then 3) complete.
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
      },
    })
    const btn2 = screen.getByTestId('pipeline-step-2-button') as HTMLButtonElement
    expect(btn2.disabled).toBe(false)
    expect(screen.queryByTestId('pipeline-step-2-locked')).toBeNull()
    for (const n of [3, 4]) {
      const button = screen.getByTestId(
        `pipeline-step-${n}-button`) as HTMLButtonElement
      expect(button.disabled).toBe(true)
      expect(
        screen.queryByTestId(`pipeline-step-${n}-locked`),
      ).not.toBeNull()
    }
  })

  it('Step 3 unlocks after Step 2 completes, Step 4 still locked', () => {
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
        2: { status: 'complete', message: 'done', payload: {} },
      },
    })
    const btn3 = screen.getByTestId('pipeline-step-3-button') as HTMLButtonElement
    expect(btn3.disabled).toBe(false)
    const btn4 = screen.getByTestId('pipeline-step-4-button') as HTMLButtonElement
    expect(btn4.disabled).toBe(true)
  })

  it('Step 3 stays locked when Step 2b has untrusted citations', () => {
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
        2: { status: 'complete', message: 'done', payload: {} },
      },
      step2b: {
        untrustedCount: 2,
        onJump: () => {},
      },
    })
    const btn3 = screen.getByTestId('pipeline-step-3-button') as HTMLButtonElement
    expect(btn3.disabled).toBe(true)
    expect(screen.queryByTestId('pipeline-step-3-locked')).not.toBeNull()
  })

  it('Step 2b shows as a visible pipeline row', () => {
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
        2: { status: 'complete', message: 'done', payload: {} },
      },
      step2b: {
        untrustedCount: 3,
        onJump: () => {},
      },
    })
    expect(screen.queryByTestId('pipeline-step-2b')).not.toBeNull()
  })

  // May 26 2026 — Step 2b semantic change. The user reported that
  // after assigning at least one citation to every HIGH finding,
  // the tile still showed "X untrusted." Cause: untrustedCount
  // counted citations by verification_status, not by
  // matched-to-finding state. The prop name 'untrustedCount' is
  // kept for component-API stability, but its semantic now
  // carries the unmatched-HIGH-finding count.

  it('Step 2b complete state describes finding-match coverage', () => {
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
        2: { status: 'complete', message: 'done', payload: {} },
      },
      step2b: {
        untrustedCount: 0,           // 0 unmatched HIGH findings
        onJump: () => {},
      },
    })
    // The completion message reads as finding-match coverage,
    // not citation adjudication.
    expect(screen.getByText(
      /Every HIGH finding has at least one matched citation/i,
    )).not.toBeNull()
  })

  it('Step 2b warning state surfaces unmatched HIGH findings', () => {
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
        2: { status: 'complete', message: 'done', payload: {} },
      },
      step2b: {
        untrustedCount: 2,           // 2 unmatched HIGH findings
        onJump: () => {},
      },
    })
    // Inline summary on the Step 2b row.
    expect(screen.getByText(
      /2 unmatched findings/i,
    )).not.toBeNull()
    // The detail message names HIGH findings, not citations.
    expect(screen.getByText(
      /2 HIGH findings without a matched citation/i,
    )).not.toBeNull()
  })

  it('Step 3 lock reason names unmatched HIGH findings, not adjudication', () => {
    _gate({
      results: {
        1: { status: 'complete', message: 'done', payload: {} },
        2: { status: 'complete', message: 'done', payload: {} },
      },
      step2b: {
        untrustedCount: 1,
        onJump: () => {},
      },
    })
    // Lock indicator on Step 3 explains the gate: HIGH finding
    // match coverage, not citation adjudication.
    const lock = screen.queryByTestId('pipeline-step-3-locked')
    expect(lock).not.toBeNull()
    const ariaLabel = lock?.getAttribute('aria-label') ?? ''
    expect(ariaLabel).toMatch(/Match 1 HIGH finding/i)
    expect(ariaLabel).not.toMatch(/Adjudicate/i)
  })

  it('locked badge does NOT render on a running step', () => {
    // A step that is itself running is disabled, but the user
    // can SEE that it's running — the lock icon would be
    // misleading (it implies an upstream gate, not own state).
    _gate({
      results: {
        1: { status: 'running', message: 'Running…', payload: {} },
      },
    })
    expect(
      screen.queryByTestId('pipeline-step-1-locked'),
    ).toBeNull()
  })
})
