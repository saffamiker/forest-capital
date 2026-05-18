/**
 * frontend/src/stores/qaStore.ts
 *
 * Persists the last QA audit result for the session so the QA tab
 * shows the previous audit when re-visited without re-running the
 * methodology checklist (which calls the QA agent and takes ~10s).
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

export type QAStatus = 'unknown' | 'pass' | 'warn' | 'fail' | 'running'

// Re-exported alias preserves backward compatibility with components/code
// that imports the QA audit response shape from this store rather than types/agents.
export type QAAuditResult = QAAuditResponse

// Lightweight status payload from /api/v1/qa/status — polled by the nav
// badge and read by the Present-mode gate. Decoupled from the heavy
// QAAuditResult (which is what the full QA tab renders) so the badge
// can refresh frequently without re-downloading the full checklist.
export interface QAStatusPayload {
  verdict:              'PASS' | 'WARN' | 'FAIL' | 'UNKNOWN'
  tier:                 1 | 2 | 3 | null
  run_at:               string | null
  age_hours:            number | null
  strategy_hash:        string
  present_mode_allowed: boolean
  running:              boolean
}

interface QAState {
  result: QAAuditResult | null
  status: QAStatus
  // Tiered-QA badge state. Source of truth for the Present-mode gate;
  // refreshed by pollStatus() every 30 seconds.
  tieredStatus: QAStatusPayload | null
  loading: boolean
  error: string | null
  loaded: boolean

  load: () => Promise<void>    // no-op if already loaded — used on tab mount
  reload: () => Promise<void>  // force re-run — used by the "Re-run audit" button
  pollStatus: () => Promise<void>  // refreshes tieredStatus from /api/v1/qa/status
  triggerTier1: () => Promise<void> // POST /api/v1/qa/run — sync Tier 1 + async Tier 2
  triggerFullReview: () => Promise<void> // POST /api/v1/qa/full-review — Opus Tier 3
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

function statusFromVerdict(p: QAStatusPayload | null): QAStatus {
  if (!p) return 'unknown'
  if (p.running) return 'running'
  if (p.verdict === 'PASS') return 'pass'
  if (p.verdict === 'WARN') return 'warn'
  if (p.verdict === 'FAIL') return 'fail'
  return 'unknown'
}

export const useQAStore = create<QAState>((set, get) => ({
  result: null,
  status: 'unknown',
  tieredStatus: null,
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

  // Lightweight status poll for the nav badge. Returns silently on error
  // — a transient backend hiccup must not flicker the badge to UNKNOWN
  // when the user is in the middle of preparing a presentation.
  pollStatus: async () => {
    try {
      const res = await axios.get<QAStatusPayload>('/api/v1/qa/status')
      set({ tieredStatus: res.data, status: statusFromVerdict(res.data) })
    } catch {
      // Keep the previous tieredStatus so the badge doesn't flicker.
    }
  },

  triggerTier1: async () => {
    set({ status: 'running' })
    try {
      await axios.post('/api/v1/qa/run')
      // Refresh the badge once Tier 1 returned; Tier 2 lands later.
      await get().pollStatus()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to trigger QA run'
      set({ error: String(msg), status: 'unknown' })
    }
  },

  triggerFullReview: async () => {
    set({ status: 'running' })
    try {
      await axios.post('/api/v1/qa/full-review')
      await get().pollStatus()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to trigger full review'
      set({ error: String(msg), status: 'unknown' })
    }
  },

  setResult: (r) =>
    set({ result: r, status: deriveStatus(r), loaded: true, error: null }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e, loading: false }),
  clear: () =>
    set({ result: null, status: 'unknown', tieredStatus: null, loaded: false, error: null }),
}))
