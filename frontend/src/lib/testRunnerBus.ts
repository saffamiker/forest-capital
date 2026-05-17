/**
 * testRunnerBus — a tiny module-level bridge so any component can start
 * the guided UAT test runner without prop-drilling or a context.
 *
 * TestRunner registers its start function on mount; the Settings
 * "Start Test Pass" button and the login notifications ("Run Tests Now",
 * "Re-test Now") call startTestRun(). This keeps TestRunner the single
 * owner of its Joyride run state while letting unrelated components
 * trigger it.
 *
 * startTestRun optionally takes a scriptId (skip the selector and run
 * that script) and a stepId (jump straight to that step — used by the
 * "Re-test Now" notification).
 */
export interface StartTestRunOptions {
  scriptId?: string
  stepId?: string
}

type Starter = (opts?: StartTestRunOptions) => void

let _starter: Starter | null = null

/** TestRunner calls this on mount (and with null on unmount). */
export function registerTestRunner(fn: Starter | null): void {
  _starter = fn
}

/** Opens the test runner — the script selector, or a specific script/step. */
export function startTestRun(opts?: StartTestRunOptions): void {
  _starter?.(opts)
}

/** True once TestRunner has registered — lets a caller enable its trigger. */
export function isTestRunnerReady(): boolean {
  return _starter !== null
}
