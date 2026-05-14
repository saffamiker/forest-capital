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
import axios from 'axios'
import type { QAAuditResult as QAAuditResponse } from '../types/agents'

export type QAStatus = 'unknown' | 'pass' | 'warn' | 'fail'

// Re-exported alias preserves backward compatibility with components/code
// that imports the QA audit response shape from this store rather than types/agents.
export type QAAuditResult = QAAuditResponse

interface QAState {
  result: QAAuditResult | null
  status: QAStatus
  loading: boolean
  error: string | null
  loaded: boolean

  load: () => Promise<void>    // no-op if already loaded — used on tab mount
  reload: () => Promise<void>  // force re-run — used by the "Re-run audit" button
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

export const useQAStore = create<QAState>((set, get) => ({
  result: null,
  status: 'unknown',
  loading: false,
  error: null,
  loaded: false,

  load: async () => {
    // Skip if already loaded or in flight — the invariant that makes the QA tab
    // instant when revisited after the first run.
    if (get().loaded || get().loading) return
    await get().reload()
  },

  reload: async () => {
    set({ loading: true, error: null })
    try {
      const res = await axios.post<QAAuditResult>('/api/qa/audit')
      set({
        result: res.data,
        status: deriveStatus(res.data),
        loaded: true,
        loading: false,
        error: null,
      })
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to run QA audit'
      set({ loading: false, error: String(msg) })
    }
  },

  setResult: (r) =>
    set({ result: r, status: deriveStatus(r), loaded: true, error: null }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e, loading: false }),
  clear: () =>
    set({ result: null, status: 'unknown', loaded: false, error: null }),
}))
