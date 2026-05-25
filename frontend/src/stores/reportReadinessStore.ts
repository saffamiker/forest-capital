/**
 * frontend/src/stores/reportReadinessStore.ts
 *
 * Single source of truth for GET /api/v1/report/readiness — the
 * combined verdict on whether either audit surface has unreviewed
 * blocking items that should prevent report generation.
 *
 * The Reports page reads `readiness` to render a status indicator,
 * and the generation buttons consult `readiness.is_ready` to decide
 * whether clicking them should open the blocking modal (with the
 * outstanding items) or fire the generation POST. The backend gate
 * (_require_report_ready in main.py) does the same check server-
 * side so a stale frontend cannot bypass it — that path returns 422
 * with a structured detail that the modal can also render.
 *
 * 60-second TTL: readiness flips state on every Acknowledge / Mark
 * Intentional / Revoke. A short TTL means the Reports page reflects
 * those state changes within a minute of the team's action, without
 * stacking redundant fetches on every mount. The store also exposes
 * reload() so the audit panels can force a refresh after a
 * mutation if a more reactive surface is wired up later.
 */
import { create } from 'zustand'
import axios from 'axios'

export interface StatisticalBlocker {
  finding_id: number | null
  layer: number | null
  check_name: string | null
  metric: string | null
  strategy: string | null
  status: string | null
  discrepancy: string | null
}

export interface MethodologyBlocker {
  check_id: string | null
  check: string | null
  description: string | null
  category: string | null
  status: string | null
}

export interface ReportReadiness {
  is_ready: boolean
  blocking_count: number
  statistical: {
    unreviewed_warnings: StatisticalBlocker[]
    unreviewed_failures: StatisticalBlocker[]
  }
  methodology: {
    unresolved_warnings: MethodologyBlocker[]
    unresolved_failures: MethodologyBlocker[]
  }
  checked_at: string
}

const TTL_MS = 60 * 1000

interface ReadinessState {
  readiness: ReportReadiness | null
  loading: boolean
  fetchedAt: Date | null

  load: () => Promise<void>    // respects TTL — no-op if fresh
  reload: () => Promise<void>  // force refresh regardless of TTL
}

function isStale(fetchedAt: Date | null): boolean {
  if (!fetchedAt) return true
  return Date.now() - fetchedAt.getTime() > TTL_MS
}

export const useReportReadinessStore = create<ReadinessState>((set, get) => ({
  readiness: null,
  loading: false,
  fetchedAt: null,

  load: async () => {
    if (!isStale(get().fetchedAt) && get().readiness != null) return
    if (get().loading) return
    await get().reload()
  },

  reload: async () => {
    set({ loading: true })
    try {
      const res = await axios.get<ReportReadiness>('/api/v1/report/readiness')
      set({ readiness: res.data, loading: false, fetchedAt: new Date() })
    } catch {
      // Fail-open: a fetch failure leaves the prior verdict intact
      // (or null on first load). The page renders a muted "readiness
      // unknown" state in either case rather than blocking the user
      // from interacting with anything else.
      set({ loading: false })
    }
  },
}))


/**
 * Returns a flat list of blocker labels suitable for rendering in the
 * blocking modal. Mirrors the backend's summarise_blockers() ordering:
 * statistical failures, statistical warnings, methodology failures,
 * methodology warnings.
 *
 * Defensive against a malformed payload — a generic axios.get mock
 * that returns `{}` (a common test stub for unknown URLs) leaves the
 * `statistical` / `methodology` keys undefined; defaulting to empty
 * arrays keeps the function side-effect-free.
 */
export function readinessBlockerLabels(
  readiness: ReportReadiness | null,
): string[] {
  if (!readiness) return []
  const out: string[] = []
  const stat = readiness.statistical ?? {
    unreviewed_warnings: [], unreviewed_failures: [],
  }
  const meth = readiness.methodology ?? {
    unresolved_warnings: [], unresolved_failures: [],
  }
  for (const f of stat.unreviewed_failures ?? []) {
    const label = f.check_name ?? f.metric ?? '(unnamed)'
    out.push(`Statistical FAIL — L${f.layer ?? '?'} · ${label}`)
  }
  for (const f of stat.unreviewed_warnings ?? []) {
    const label = f.check_name ?? f.metric ?? '(unnamed)'
    out.push(`Statistical WARN unreviewed — L${f.layer ?? '?'} · ${label}`)
  }
  for (const it of meth.unresolved_failures ?? []) {
    const label = it.check ?? it.description ?? '(unnamed)'
    out.push(`Methodology FAIL — ${it.check_id ?? '?'} · ${label}`)
  }
  for (const it of meth.unresolved_warnings ?? []) {
    const label = it.check ?? it.description ?? '(unnamed)'
    out.push(`Methodology WARN unreviewed — ${it.check_id ?? '?'} · ${label}`)
  }
  return out
}
