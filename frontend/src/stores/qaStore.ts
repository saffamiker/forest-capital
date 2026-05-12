/**
 * frontend/src/stores/qaStore.ts
 *
 * Persists the last QA audit result for the session so the QA tab
 * shows the previous audit when re-visited without re-running the
 * 30-point checklist (which calls the QA agent and takes ~10s).
 *
 * Also tracks the QA status gate for Present mode:
 *   'unknown'  — audit not yet run this session
 *   'pass'     — all checks passed
 *   'warn'     — some warnings, no failures
 *   'fail'     — one or more hard failures
 * Present mode is locked until status is 'warn' or 'pass'.
 */

import { create } from 'zustand'

export type QAStatus = 'unknown' | 'pass' | 'warn' | 'fail'

export interface QACheckItem {
  check_id: string
  category: string
  check: string
  description: string
  status: 'PASS' | 'WARN' | 'FAIL'
  evidence?: string
  fix?: string | null
}

export interface QAAuditResult {
  checks_passed: number
  checks_warned: number
  checks_failed: number
  summary: string
  items: QACheckItem[]
  run_at: string
}

interface QAState {
  result: QAAuditResult | null
  status: QAStatus
  loading: boolean
  error: string | null
  loaded: boolean

  setResult: (r: QAAuditResult) => void
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  clear: () => void
}

function deriveStatus(r: QAAuditResult): QAStatus {
  if (r.checks_failed > 0) return 'fail'
  if (r.checks_warned > 0) return 'warn'
  return 'pass'
}

export const useQAStore = create<QAState>((set) => ({
  result: null,
  status: 'unknown',
  loading: false,
  error: null,
  loaded: false,

  setResult: (r) =>
    set({ result: r, status: deriveStatus(r), loaded: true, error: null }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e, loading: false }),
  clear: () =>
    set({ result: null, status: 'unknown', loaded: false, error: null }),
}))
