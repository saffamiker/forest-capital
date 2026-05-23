/**
 * frontend/src/stores/reportWriterStore.ts
 *
 * Module-level pipeline state for the report writer. Surfaces:
 *
 *   - The current per-step status map (used by the nav-bar badge
 *     to show running / complete / failed)
 *   - The active audit_id so reloads of /reports/writer can resume
 *     the in-flight pipeline without duplicating audit rows
 *   - The pipeline-started-at timestamp for the summary card
 *
 * The store is intentionally minimal — the bulk of pipeline state
 * still lives in ReportWriter.tsx component state. This store
 * carries ONLY the cross-screen signals (nav badge + restore key).
 */
import { create } from 'zustand'

export type BadgeState =
  | 'idle'
  | 'running'
  | 'complete'
  | 'failed'

interface ReportWriterStore {
  // Nav badge — the single signal MainLayout renders next to the
  // Reports nav item when a pipeline run is active.
  badge: BadgeState
  badgeDetail: string | null

  // The audit_id the backend returned on the first upsert. Frontend
  // round-trips it on every subsequent step completion so the
  // backend updates the same row instead of creating duplicates.
  auditId: number | null

  // The pipeline-started-at timestamp for the summary card's total
  // wall-clock display.
  pipelineStartedAt: number | null

  // Setters
  setBadge: (state: BadgeState, detail?: string | null) => void
  setAuditId: (id: number | null) => void
  setPipelineStartedAt: (t: number | null) => void
  reset: () => void
}


export const useReportWriterStore = create<ReportWriterStore>((set) => ({
  badge: 'idle',
  badgeDetail: null,
  auditId: null,
  pipelineStartedAt: null,

  setBadge: (state, detail) =>
    set({ badge: state, badgeDetail: detail ?? null }),
  setAuditId: (id) => set({ auditId: id }),
  setPipelineStartedAt: (t) => set({ pipelineStartedAt: t }),
  reset: () => set({
    badge: 'idle',
    badgeDetail: null,
    auditId: null,
    pipelineStartedAt: null,
  }),
}))
