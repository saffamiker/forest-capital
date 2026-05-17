/**
 * test-runner.test.tsx — guided UAT test runner.
 *
 * Covers the code-versioned test scripts (testScripts.ts) and the
 * failure/feedback submission panel's required-field gating. The
 * server-backed flows (endpoint calls, quality gate, resume) are
 * exercised by the backend suite (tests/test_test_runner.py).
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import {
  TEST_SCRIPTS, TEST_SCRIPT_VERSION, getTestScript, scriptForEmail,
} from '../constants/testScripts'
import TestSubmissionPanel from '../components/TestSubmissionPanel'
import type { TestStep } from '../constants/testScripts'

// ── Test scripts config ───────────────────────────────────────────────────────

describe('testScripts config', () => {
  it('defines exactly the four UAT scripts', () => {
    const ids = TEST_SCRIPTS.map((s) => s.id).sort()
    expect(ids).toEqual([
      'all_testers_v1', 'bob_thao_v1', 'michael_ruurds_v1', 'molly_murdock_v1',
    ])
  })

  it('every script has a non-empty, well-formed step list', () => {
    for (const script of TEST_SCRIPTS) {
      expect(script.steps.length).toBeGreaterThan(0)
      expect(script.version).toBe(TEST_SCRIPT_VERSION)
      for (const step of script.steps) {
        expect(step.id.length).toBeGreaterThan(0)
        expect(step.route.startsWith('/')).toBe(true)
        expect(step.title.length).toBeGreaterThan(0)
        expect(step.instruction.length).toBeGreaterThan(0)
        expect(step.expectedResult.length).toBeGreaterThan(0)
      }
    }
  })

  it('step ids are unique within each script', () => {
    for (const script of TEST_SCRIPTS) {
      const ids = script.steps.map((s) => s.id)
      expect(new Set(ids).size).toBe(ids.length)
    }
  })

  it('getTestScript looks a script up by id', () => {
    expect(getTestScript('all_testers_v1')?.assignedTo).toBe('all')
    expect(getTestScript('nope')).toBeUndefined()
  })

  it('scriptForEmail maps each tester to their role script', () => {
    expect(scriptForEmail('ruurdsm@queens.edu')?.id).toBe('michael_ruurds_v1')
    expect(scriptForEmail('thaob@queens.edu')?.id).toBe('bob_thao_v1')
    expect(scriptForEmail('murdockm@queens.edu')?.id).toBe('molly_murdock_v1')
    expect(scriptForEmail('someone@else.edu')).toBeUndefined()
  })
})

// ── TestSubmissionPanel — required-field gating ───────────────────────────────

const STEP: TestStep = {
  id: 'demo_step', route: '/', target: null, title: 'Demo step',
  instruction: 'Do the thing.', expectedResult: 'The thing happened.',
  allowSkip: true,
}

describe('TestSubmissionPanel', () => {
  it('failure report requires a description and an actual result', () => {
    render(
      <TestSubmissionPanel
        mode="failure" step={STEP} scriptId="all_testers_v1"
        onClose={vi.fn()} onSubmitted={vi.fn()} />,
    )
    const submit = screen.getByRole('button', { name: /Submit Failure Report/i })
    // Both required fields empty → disabled.
    expect(submit).toBeDisabled()

    const [whatHappened] = screen.getAllByRole('textbox')
    fireEvent.change(whatHappened, { target: { value: 'It crashed on click' } })
    // "What happened?" alone is not enough — actual result still empty.
    expect(submit).toBeDisabled()
  })

  it('feedback submit is gated on a title and a description', () => {
    render(
      <TestSubmissionPanel
        mode="feedback" step={STEP} scriptId="all_testers_v1"
        onClose={vi.fn()} onSubmitted={vi.fn()} />,
    )
    const submit = screen.getByRole('button', { name: /Submit Feedback/i })
    expect(submit).toBeDisabled()
  })

  it('a free-form feedback panel notes it is logged independently', () => {
    render(
      <TestSubmissionPanel
        mode="feedback" step={null} scriptId={null} sourceRoute="/analytics"
        onClose={vi.fn()} onSubmitted={vi.fn()} />,
    )
    expect(screen.getByText(/logged independently of your current test step/i))
      .toBeInTheDocument()
  })
})
