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
  // Set of check_ids the team has acknowledged via the intentional-
  // overrides endpoint (mark-intentional / disclosure_required confirm).
  // The badge derivation skips warnings whose check_id is in this set,
  // so a fully-acknowledged result reads PASS instead of WARN. Polled
  // alongside the audit status; refreshed by pollOverrides().
  acknowledgedChecks: Set<string>
  loading: boolean
  error: string | null
  loaded: boolean

  load: () => Promise<void>    // no-op if already loaded — used on tab mount
  // Force re-run — used by the "Re-run audit" button. force defaults to
  // true: a manual click means "give me fresh checks now", bypassing
  // both the backend hash gate and the min-interval cache. load() calls
  // reload(false) so the first tab visit benefits from a cached audit.
  reload: (force?: boolean) => Promise<void>
  pollStatus: () => Promise<void>  // refreshes tieredStatus from /api/v1/qa/status
  pollOverrides: () => Promise<void>  // refreshes acknowledgedChecks from /api/v1/qa/intentional-overrides
  triggerTier1: () => Promise<void> // POST /api/v1/qa/run — sync Tier 1 + async Tier 2
  triggerFullReview: () => Promise<void> // POST /api/v1/qa/full-review — Opus Tier 3
  setResult: (r: QAAuditResult) => void
  setLoading: (v: boolean) => void
  setError: (e: string | null) => void
  clear: () => void
}

// Check IDs that are EXCLUDED from the badge's warning calculation
// regardless of their status. These are attestation checks whose
// disclosed/acknowledged state is the meaningful signal, not the
// raw WARN. Leaving them in the count would create false alarm
// fatigue — every audit would read WARN despite no actionable
// issues. May 26 2026 spec from the user.
//
// IN02 = Academic Review attestation. The check WARNs by design
// when the latest Academic Review hasn't parsed five rated
// sections, but that is a state the team manages by RE-RUNNING
// the review — not an audit finding to be acknowledged
// per-check. It doesn't belong in the actionable warning count.
const _BADGE_EXCLUDED_CHECK_IDS: ReadonlySet<string> = new Set([
  'IN02',
])

// Statuses on a QACheck that count as a warning for the badge.
// 'incomplete' is included because an unfinished section is
// actionable (re-run the audit). 'pass' / 'unknown' are not.
const _BADGE_WARN_STATUSES: ReadonlySet<string> = new Set([
  'warn', 'warning', 'incomplete',
])

// Statuses that count as a failure for the badge.
const _BADGE_FAIL_STATUSES: ReadonlySet<string> = new Set([
  'fail', 'failure',
])

/**
 * Per-check badge derivation. Walks the items array, EXCLUDES
 * IN02 (and any other badge-excluded attestation check) from
 * the warning count, and SKIPS warnings whose check_id is in
 * the team's acknowledged-overrides set. Returns
 *   { effective_failed, effective_warned }
 * — the counts the badge cares about.
 *
 * A check that doesn't appear in acknowledgedChecks but carries
 * status='warn' is "actionable" and contributes to the badge.
 * A check that does appear is treated as resolved (the team
 * has recorded a disclosure / intentional-design override) and
 * the badge can clear to PASS.
 */
function _effectiveCounts(
  r: QAAuditResult, acknowledged: ReadonlySet<string>,
): { failed: number, warned: number } {
  const items = Array.isArray(r.items) ? r.items : []
  if (items.length === 0) {
    // Fallback for stale / placeholder results that lack the
    // items array — use the summary counts. The user's spec
    // ("badge should reflect actionable state only") applies
    // only when we have per-check data to inspect; without it
    // the conservative answer is "use what we have".
    return {
      failed: r.checks_failed || 0,
      warned: r.checks_warned || 0,
    }
  }
  let failed = 0
  let warned = 0
  for (const item of items) {
    const cid = String(item.check_id || '')
    const st = String(item.status || '').toLowerCase()
    if (_BADGE_FAIL_STATUSES.has(st)) {
      failed += 1
      continue
    }
    if (!_BADGE_WARN_STATUSES.has(st)) continue
    // Warning-class status — apply the exclusion + ack filters.
    if (_BADGE_EXCLUDED_CHECK_IDS.has(cid)) continue
    if (acknowledged.has(cid)) continue
    warned += 1
  }
  return { failed, warned }
}

/**
 * Badge derivation from the full audit result. Per user spec
 * (May 26 2026):
 *   FAIL — any check carrying a failure status.
 *   WARN — at least one unacknowledged actionable warning,
 *          AFTER excluding badge-excluded attestation checks
 *          (IN02) and warnings the team has confirmed via the
 *          intentional-overrides endpoint.
 *   PASS — everything else: no failures and no unacknowledged
 *          actionable warnings.
 */
function deriveStatus(
  r: QAAuditResult, acknowledged: ReadonlySet<string>,
): QAStatus {
  const { failed, warned } = _effectiveCounts(r, acknowledged)
  if (failed > 0) return 'fail'
  if (warned > 0) return 'warn'
  return 'pass'
}

/**
 * Badge derivation from the tiered status payload. The payload
 * carries only the summary verdict — there's no items list to
 * inspect per-check — so an acknowledgement cannot override a
 * server-side WARN at this layer. If we have a cached audit
 * result with items, we PREFER deriveStatus(result, ...) so
 * acknowledgements clear the badge. Without a cached result we
 * trust the server verdict.
 *
 * The caller passes both pieces of state so this function can
 * decide which signal to use:
 *   - cached audit present  → derive from items + acknowledged
 *   - tieredStatus only     → trust the server verdict
 */
function statusFromVerdict(
  p: QAStatusPayload | null,
  cachedResult: QAAuditResult | null,
  acknowledged: ReadonlySet<string>,
): QAStatus {
  if (!p) return 'unknown'
  if (p.running) return 'running'
  // Prefer the per-check derivation when we have items — it
  // respects IN02 exclusion + acknowledgements. A server WARN
  // verdict that is entirely composed of acknowledged warnings
  // becomes PASS via this path.
  if (cachedResult && Array.isArray(cachedResult.items)
        && cachedResult.items.length > 0) {
    return deriveStatus(cachedResult, acknowledged)
  }
  if (p.verdict === 'PASS') return 'pass'
  if (p.verdict === 'WARN') return 'warn'
  if (p.verdict === 'FAIL') return 'fail'
  return 'unknown'
}

/**
 * Format the /api/qa/audit error payload as a human-readable
 * string. The backend's hotfix (May 23 2026) returns a structured
 * detail object — {error, error_type, message, hint} — so the user
 * sees the real underlying error instead of the previous mock-
 * audit silent fallback. Falls back to .message or a generic
 * string for any non-structured error shape.
 */
function _formatAuditError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') {
      const d = detail as {
        error?: string; error_type?: string;
        message?: string; hint?: string
      }
      const parts: string[] = []
      if (d.message) parts.push(d.message)
      else if (d.error) parts.push(d.error)
      if (d.error_type && d.error_type !== d.error) {
        parts.push(`(${d.error_type})`)
      }
      if (d.hint) parts.push(`— ${d.hint}`)
      if (parts.length > 0) return parts.join(' ')
    }
    return err.message || 'QA audit request failed.'
  }
  return (err as Error)?.message || 'Failed to run QA audit.'
}


export const useQAStore = create<QAState>((set, get) => ({
  result: null,
  status: 'unknown',
  tieredStatus: null,
  acknowledgedChecks: new Set<string>(),
  loading: false,
  error: null,
  loaded: false,

  load: async () => {
    // Skip if already loaded or in flight — the invariant that makes the QA tab
    // instant when revisited after the first run. Cached-friendly: passes
    // force=false so the backend serves the hash-matched audit if one exists.
    if (get().loaded || get().loading) return
    await get().reload(false)
  },

  reload: async (force = true) => {
    // force defaults to true — a direct reload() call is a manual
    // re-run. The backend's hash gate is keyed on strategy_hash, so
    // an IN02 (Academic Review) change is invisible to a cached
    // audit without this bypass. load() is the only caller that
    // passes force=false (first tab visit, cache-friendly).
    set({ loading: true, error: null })
    try {
      const res = await axios.post<QAAuditResult>(
        '/api/qa/audit', { force })
      // Refresh acknowledgements alongside the audit so the badge
      // reflects the most current ack state — a Mark Intentional /
      // Confirm Disclosure click in another tab is picked up on
      // the next reload. Sequential await keeps the order
      // deterministic; pollOverrides() never raises.
      await get().pollOverrides()
      set({
        result: res.data,
        status: deriveStatus(res.data, get().acknowledgedChecks),
        loaded: true,
        loading: false,
        error: null,
      })
    } catch (err) {
      // The backend's /api/qa/audit error now returns a structured
      // detail object: {error, error_type, message, hint}. Format
      // it as a human-readable string for the qaStore.error field
      // so the user sees the real failure instead of the previous
      // silent fallback to mock data (the hotfix prompt — May 23
      // 2026). A plain-string detail still works (backwards
      // compat with other handlers that return detail: "msg").
      const msg = _formatAuditError(err)
      set({ loading: false, error: msg })
    }
  },

  // Lightweight status poll for the nav badge. Returns silently on error
  // — a transient backend hiccup must not flicker the badge to UNKNOWN
  // when the user is in the middle of preparing a presentation.
  //
  // May 26 2026 — also refreshes the acknowledgedChecks set so a
  // recently-recorded disclosure / Mark Intentional clears the
  // badge from WARN to PASS within the 30-second poll cycle.
  pollStatus: async () => {
    try {
      // Fetch both in parallel — they're independent endpoints
      // and the badge needs both to derive correctly.
      const [statusRes] = await Promise.all([
        axios.get<QAStatusPayload>('/api/v1/qa/status'),
        get().pollOverrides(),
      ])
      set({
        tieredStatus: statusRes.data,
        status: statusFromVerdict(
          statusRes.data, get().result, get().acknowledgedChecks),
      })
    } catch {
      // Keep the previous tieredStatus so the badge doesn't flicker.
    }
  },

  // Refreshes the acknowledgedChecks set from the intentional-
  // overrides endpoint. The endpoint returns the full overrides
  // map keyed by check_id; we project to a set of check_ids the
  // badge derivation walks. Fail-silent — a transient outage
  // keeps the previous set rather than flickering the badge.
  pollOverrides: async () => {
    try {
      const res = await axios.get<{
        overrides: Record<string, unknown>
      }>('/api/v1/qa/intentional-overrides')
      const map = res.data?.overrides || {}
      const next = new Set<string>(Object.keys(map))
      set({
        acknowledgedChecks: next,
        // Re-derive status against the fresh ack set when we
        // already have an audit result cached.
        ...(get().result
          ? { status: deriveStatus(get().result as QAAuditResult, next) }
          : {}),
      })
    } catch {
      // Keep the previous set.
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
    set({
      result: r,
      status: deriveStatus(r, get().acknowledgedChecks),
      loaded: true,
      error: null,
    }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e, loading: false }),
  clear: () =>
    set({
      result: null,
      status: 'unknown',
      tieredStatus: null,
      acknowledgedChecks: new Set<string>(),
      loaded: false,
      error: null,
    }),
}))
