/**
 * pipeline-warning-passthrough.test.tsx
 *
 * Two report-writer pipeline bugs (May 23 2026):
 *
 *   1. Step 5/6 auto-fire deadlocked when Step 2 landed at
 *      'warning' (a normal state when 1-2 citations need review
 *      but most are verified). The strict 'complete' check
 *      prevented Step 5 from ever firing, blocking Step 6 and
 *      the Generate button.
 *
 *   2. The Step 2 modal footer pointed users to the Citation
 *      Review panel "on the editor screen" before the editor
 *      existed — there is no editor surface pre-generation. The
 *      copy now explains that the pipeline continues and the
 *      Citation Review panel appears AFTER Step 7 generates the
 *      draft.
 *
 * The hook-level tests use a minimal harness that renders
 * useAutoFireStep5And6 with controllable results and a fireStep
 * spy. The copy test pins the new wording on the Step2Detail
 * modal so a regression that re-introduces the misleading
 * "editor screen" pointer fails immediately.
 */
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'

import {
  useAutoFireStep5And6,
  type StepResults, type StepResult, type StepStatus,
} from '../components/reportwriter/PipelineGate'


/** Render a tiny component that wires the hook to a spy and
 *  exposes the spy via the returned tuple. */
function renderAutoFire(results: StepResults): {
  fireSpy: ReturnType<typeof vi.fn>
} {
  const fireSpy = vi.fn()
  const fireStep = (n: number): Promise<void> => {
    fireSpy(n)
    return Promise.resolve()
  }
  function Harness() {
    useAutoFireStep5And6(results, fireStep)
    return null
  }
  render(<Harness />)
  return { fireSpy }
}


function step(status: StepStatus, payload: unknown = {}): StepResult {
  return { status, message: '', payload: payload as Record<string, unknown> }
}


describe('useAutoFireStep5And6 — warning is passthrough', () => {
  it('Step 5 fires when Step 2 is warning and 1, 3, 4 are complete', () => {
    const results: StepResults = {
      1: step('complete'),
      2: step('warning'),  // ← the previously-deadlocking state
      3: step('complete'),
      4: step('complete'),
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).toHaveBeenCalledWith(5)
  })

  it('Step 5 fires when every earlier step is warning', () => {
    // Edge case — all four earlier steps land at warning. The
    // pipeline should still progress; only 'failed' should gate.
    const results: StepResults = {
      1: step('warning'),
      2: step('warning'),
      3: step('warning'),
      4: step('warning'),
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).toHaveBeenCalledWith(5)
  })

  it('Step 5 does NOT fire when Step 4 itself is failed', () => {
    // May 24 2026 — the auto-fire gate is now narrow: Step 5
    // fires when Step 4 passes (complete or warning, without
    // _no_audit). The pipeline's strict-sequential gating
    // prevents 4 from completing while 2 is failed in
    // practice — the auto-fire hook only inspects Step 4.
    const results: StepResults = {
      1: step('complete'),
      2: step('complete'),
      3: step('complete'),
      4: step('failed'),  // hard block — Step 5 must not fire
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).not.toHaveBeenCalledWith(5)
  })

  it('Step 5 does NOT fire when Step 4 is still running', () => {
    const results: StepResults = {
      1: step('complete'),
      2: step('complete'),
      3: step('complete'),
      4: step('running'),
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).not.toHaveBeenCalledWith(5)
  })

  it('Step 5 does NOT fire when Step 4 is _no_audit bypass', () => {
    // _no_audit on Step 4 is the explicit "no QA audit has run"
    // signal. The user's directive: Steps 5 and 6 auto-fire only
    // after Step 4 completes with a REAL QA audit result.
    const results: StepResults = {
      1: step('complete'),
      2: step('complete'),
      3: step('complete'),
      4: {
        status: 'warning' as const,
        message: 'No QA audit',
        payload: { _no_audit: true },
      },
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).not.toHaveBeenCalledWith(5)
  })

  it('Step 6 fires when Step 5 is warning (already worked, regression guard)', () => {
    const results: StepResults = {
      1: step('complete'),
      2: step('complete'),
      3: step('complete'),
      4: step('complete'),
      5: step('warning'),
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).toHaveBeenCalledWith(6)
  })

  it('Step 5 does not re-fire when it is already running', () => {
    const results: StepResults = {
      1: step('complete'),
      2: step('warning'),
      3: step('complete'),
      4: step('complete'),
      5: step('running'),  // not idle
    }
    const { fireSpy } = renderAutoFire(results)
    expect(fireSpy).not.toHaveBeenCalledWith(5)
  })
})


describe('Step 2 modal copy — accurate post-generation pointer', () => {
  it('Step2Detail footer no longer points to the editor screen pre-generation', async () => {
    // Render the PipelineGate's Step 2 detail directly with a
    // payload that has unverified citations so the footer prose
    // renders. The detail is normally opened via the modal; here
    // we render the PipelineGate row and click the View details
    // toggle to open the modal.
    const { default: PipelineGate } = await import(
      '../components/reportwriter/PipelineGate')
    const { fireEvent, screen } = await import('@testing-library/react')
    const results: StepResults = {
      2: step('warning', {
        citations: {
          c1: { verification_status: 'pending_review',
                search_query_used: 'x' },
        },
        verified_count: 0,
        concept_count: 1,
        quality: 'amber',
      }),
    }
    render(<PipelineGate
      results={results}
      generating={false}
      generateDisabledReason="Run earlier steps"
      onRunStep={async () => {}}
      onGenerate={async () => {}} />)

    fireEvent.click(screen.getByTestId('pipeline-step-2-expand'))
    // The footer must describe the post-generation Citation
    // Review panel — NOT point users to "the editor screen"
    // before Step 7 has run.
    const body = screen.getByTestId('pipeline-step-2-detail')
    const text = body.textContent ?? ''
    expect(text).toMatch(/After Step 7 generates the draft/i)
    expect(text).toMatch(/Citation Review panel/i)
    // The misleading old wording must NOT come back.
    expect(text).not.toMatch(
      /Citation Review panel on the editor screen/i)
  })
})
