/**
 * currentDraftPresence -- June 27 2026.
 *
 * Tiny shared store that tracks WHICH document_types have a current
 * (is_current=true) draft. Two consumers:
 *
 *   DocumentGenerationPanel -- already maintains a richer
 *     CurrentDraftSummary map. It pushes into this store after each
 *     successful /api/v1/documents/drafts fetch so other components
 *     can read the same "does a draft exist" signal without
 *     duplicating the GET.
 *
 *   GenerationToast -- reads this store when a job fails. When a
 *     current draft exists for the failed job's doc_type, the toast
 *     swaps the red "Generation failed" chrome for a neutral
 *     "Previous generation attempt unavailable -- your current
 *     draft is still available" message + Open in Editor.
 *
 * Why a separate store: GenerationToast is mounted in MainLayout
 * (every page); DocumentGenerationPanel is the Reports page only.
 * Without a shared store the toast would either (a) be unaware of
 * draft state and always show red, or (b) fire its own /drafts GET
 * on every navigation. A module-level Map keeps the read O(1) and
 * the write happens once per drafts fetch on the Reports page.
 *
 * Refresh strategy: the toast also calls refreshFromDraftsList()
 * lazily on first mount so a user who lands on a non-Reports page
 * (and never visits Reports) still gets the suppression. Cached for
 * the session; explicit calls (e.g. after a generation completes)
 * overwrite.
 */
import { useSyncExternalStore } from 'react'
import axios from 'axios'

interface DraftPresence {
  id:          number
  document_type: string
  /** ISO timestamp; null when the row pre-dates the columns. */
  updated_at:  string | null
  /** True when the draft row has BOTH data_hash set AND was not
   *  explicitly flagged as content-empty by the backend's NULL-
   *  guard (PR #445 prevents NULL content_json drafts from being
   *  promoted to is_current=true, so this should always be true for
   *  any row that survives the is_current=true filter). */
  content_present: boolean
}

const byDocType = new Map<string, DraftPresence>()
const listeners = new Set<() => void>()
let snapshot: Map<string, DraftPresence> = new Map()
let lastRefreshAt = 0
const REFRESH_TTL_MS = 30_000

function emit(): void {
  snapshot = new Map(byDocType)
  listeners.forEach((l) => l())
}

/** DocumentGenerationPanel pushes here after each /drafts fetch. */
export function setCurrentDraftPresence(
  drafts: Array<{
    id: number
    document_type: string
    is_current?: boolean
    updated_at?: string | null
    data_hash?:  string | null
  }>,
): void {
  byDocType.clear()
  for (const d of drafts) {
    if (d.is_current === false) continue
    // PR #445 enforces NULL content_json drafts can't be promoted to
    // is_current=true; treat any is_current=true draft as content-
    // present. We surface `data_hash != null` here as a defensive
    // secondary signal -- a draft with no data_hash means the
    // generation almost certainly didn't reach the persistence
    // path. The toast suppression keys on content_present.
    byDocType.set(d.document_type, {
      id: d.id,
      document_type: d.document_type,
      updated_at: d.updated_at ?? null,
      content_present: (d.data_hash ?? null) !== null,
    })
  }
  lastRefreshAt = Date.now()
  emit()
}

/** Lazy refresh -- the toast calls this on mount when the cache is
 *  cold so we still get the suppression on pages that never load the
 *  Reports panel. Best-effort; failures are swallowed. */
export async function refreshFromDraftsList(): Promise<void> {
  if (Date.now() - lastRefreshAt < REFRESH_TTL_MS) return
  try {
    const res = await axios.get<{
      drafts: Array<{
        id: number; document_type: string
        is_current?: boolean
        updated_at?: string | null
        data_hash?: string | null
      }>
    }>('/api/v1/documents/drafts')
    setCurrentDraftPresence(res.data?.drafts ?? [])
  } catch {
    /* swallow -- a failed refresh just means no suppression */
  }
}

/** Synchronous read: does a usable current draft exist for this
 *  document_type? Used by GenerationToast to decide whether to
 *  suppress the red error banner on a stale-job failure. */
export function hasCurrentDraft(documentType: string): boolean {
  const d = snapshot.get(documentType)
  return !!d && d.content_present
}

/** Synchronous read: the current draft id for a doc_type, or null.
 *  Used by GenerationToast to render a working Open in Editor link. */
export function currentDraftId(documentType: string): number | null {
  return snapshot.get(documentType)?.id ?? null
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** React hook -- subscribe to the current presence map. */
export function useCurrentDraftPresence(): Map<string, DraftPresence> {
  return useSyncExternalStore(
    subscribe, () => snapshot, () => snapshot)
}

/** Test-only -- reset the cache between specs. */
export function __resetCurrentDraftPresence(): void {
  byDocType.clear()
  lastRefreshAt = 0
  emit()
}
