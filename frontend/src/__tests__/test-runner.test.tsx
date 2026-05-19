/**
 * test-runner.test.tsx — guided UAT test runner.
 *
 * Covers the code-versioned test scripts (testScripts.ts) and the
 * failure/feedback submission panel's required-field gating. The
 * server-backed flows (endpoint calls, quality gate, resume) are
 * exercised by the backend suite (tests/test_test_runner.py).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import axios from 'axios'
import {
  TEST_SCRIPTS, TEST_SCRIPT_VERSION, getTestScript, scriptForEmail,
} from '../constants/testScripts'
import TestSubmissionPanel from '../components/TestSubmissionPanel'
import TestRunner from '../components/TestRunner'
import type { TestStep } from '../constants/testScripts'
import { AuthContext } from '../App'
import { SessionProvider, useSession } from '../context/SessionContext'
import { startTestRun } from '../lib/testRunnerBus'

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

// ── TestRunner — Testing Mode is enforced on every start/close ────────────────

/** Surfaces the live session_type so a test can assert Testing Mode. */
function SessionProbe() {
  const { sessionType } = useSession()
  return <div data-testid="session-type">{sessionType}</div>
}

function renderRunner() {
  const authValue = {
    session: {
      token: 't', email: 'thaob@queens.edu', permissions: ['team_member'],
    },
    isVerifying: false, login: vi.fn(), logout: vi.fn(),
  }
  return render(
    <AuthContext.Provider value={authValue}>
      <SessionProvider>
        <MemoryRouter>
          <SessionProbe />
          <TestRunner />
        </MemoryRouter>
      </SessionProvider>
    </AuthContext.Provider>,
  )
}

describe('TestRunner — Testing Mode enforcement', () => {
  beforeEach(() => {
    localStorage.clear()
    // loadExisting() GETs the prior results — no prior run.
    vi.spyOn(axios, 'get').mockResolvedValue({ data: { results: {} } })
  })
  afterEach(() => { vi.restoreAllMocks() })

  it('starting via the notification path enables Testing Mode', async () => {
    renderRunner()
    expect(screen.getByTestId('session-type')).toHaveTextContent('analytical')
    act(() => { startTestRun({ scriptId: 'all_testers_v1' }) })
    await waitFor(() =>
      expect(screen.getByTestId('session-type')).toHaveTextContent('testing'))
  })

  it('starting via a Test Results re-test link enables Testing Mode', async () => {
    renderRunner()
    const firstStep = TEST_SCRIPTS[0].steps[0].id
    act(() => {
      startTestRun({ scriptId: 'all_testers_v1', stepId: firstStep })
    })
    await waitFor(() =>
      expect(screen.getByTestId('session-type')).toHaveTextContent('testing'))
  })

  it('closing the runner disables Testing Mode', async () => {
    renderRunner()
    act(() => { startTestRun() })   // no scriptId → the script selector
    await waitFor(() =>
      expect(screen.getByTestId('session-type')).toHaveTextContent('testing'))
    fireEvent.click(screen.getByText('Cancel'))
    await waitFor(() =>
      expect(screen.getByTestId('session-type')).toHaveTextContent('analytical'))
  })

  it('shows a toast when Testing Mode is auto-enabled', async () => {
    renderRunner()
    act(() => { startTestRun({ scriptId: 'all_testers_v1' }) })
    expect(await screen.findByText(/Testing Mode enabled automatically/))
      .toBeInTheDocument()
  })

  it('shows a toast when Testing Mode is auto-disabled on close', async () => {
    renderRunner()
    act(() => { startTestRun() })
    await screen.findByText('Cancel')
    fireEvent.click(screen.getByText('Cancel'))
    expect(await screen.findByText(/Testing Mode off/)).toBeInTheDocument()
  })
})
