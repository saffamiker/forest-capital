/**
 * frontend/src/stores/storyboardStore.ts
 *
 * Holds Molly's working storyboard, version history, and the auto-save
 * coordination state. All editor mutations go through this store —
 * drag-reorder, slide updates, additions, deletions. The store owns
 * the debounced auto-save timer so navigating away mid-edit doesn't
 * lose work or fire a duplicate save.
 *
 * Auto-save cadence: 30 seconds after the last edit (debounced). A
 * manual "Save Version" goes through saveNamedVersion() instead.
 */
import { create } from 'zustand'
import axios from 'axios'
import type {
  Slide, Storyboard, StoryboardDraftResponse, DocumentVersion,
} from '../types/storyboard'

const AUTOSAVE_DEBOUNCE_MS = 30_000

interface StoryboardState {
  documentId:    string | null
  storyboard:    Storyboard | null
  versions:      DocumentVersion[]
  loading:       boolean
  saving:        boolean
  lastSavedAt:   Date | null
  error:         string | null

  // Track which slide is currently selected in the editor — the right
  // panel reads from here to render the expanded editor.
  selectedSlideId: string | null

  // Lifecycle / data fetching
  createDraft:     () => Promise<void>
  loadDocument:    (documentId: string) => Promise<void>
  loadVersions:    () => Promise<void>

  // Slide mutations (each one triggers debounced auto-save)
  setSelectedSlide: (slideId: string | null) => void
  updateSlide:     (slideId: string, patch: Partial<Slide>) => void
  reorderSlides:   (orderedIds: string[]) => void
  addSlide:        (afterOrder: number) => void
  removeSlide:     (slideId: string) => void

  // Manual persistence
  saveNamedVersion: (name: string, summary?: string) => Promise<void>
  restoreVersion:   (versionId: string) => Promise<void>

  clear: () => void
}

let autosaveTimer: ReturnType<typeof setTimeout> | null = null


function recomputeTiming(slides: Slide[]): number {
  return Math.round(slides.reduce((s, sl) => s + Number(sl.timing_mins || 0), 0) * 10) / 10
}


function normaliseOrders(slides: Slide[]): Slide[] {
  // Reassign `order` to be sequential 1..N regardless of input. Keeps
  // the drag-reorder and add/remove operations from drifting away from
  // 1-indexed contiguous order numbers — important for the backend
  // pptx + script writers that sort by order.
  return slides.map((s, i) => ({ ...s, order: i + 1 }))
}


export const useStoryboardStore = create<StoryboardState>((set, get) => ({
  documentId: null,
  storyboard: null,
  versions: [],
  loading: false,
  saving: false,
  lastSavedAt: null,
  error: null,
  selectedSlideId: null,

  createDraft: async () => {
    set({ loading: true, error: null })
    try {
      const res = await axios.post<StoryboardDraftResponse>(
        '/api/documents/storyboard/draft',
      )
      const firstSlideId = res.data.storyboard.slides[0]?.id ?? null
      set({
        documentId: res.data.document_id,
        storyboard: res.data.storyboard,
        selectedSlideId: firstSlideId,
        loading: false,
        // If persistence is "unavailable" (no DATABASE_URL) the version
        // list will be empty — that's fine; the user can still edit but
        // Save Version will fail until the operator runs alembic.
        error: res.data.persistence === 'unavailable' ? res.data.message ?? null : null,
      })
      // Stash the active document_id so the Reports screen's deck / Q&A
      // generators can target this storyboard without forcing the user
      // to re-open the editor. Cleared on logout via the auth flow.
      if (res.data.document_id) {
        try {
          localStorage.setItem('fc_active_storyboard_id', res.data.document_id)
        } catch { /* localStorage disabled — silently no-op */ }
        await get().loadVersions()
      }
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to create storyboard draft'
      set({ loading: false, error: String(msg) })
    }
  },

  loadDocument: async (documentId) => {
    set({ loading: true, error: null })
    try {
      const res = await axios.get<{ content: Storyboard }>(`/api/documents/${documentId}`)
      const sb = res.data.content
      set({
        documentId,
        storyboard: sb,
        selectedSlideId: sb.slides[0]?.id ?? null,
        loading: false,
      })
      await get().loadVersions()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load storyboard'
      set({ loading: false, error: String(msg) })
    }
  },

  loadVersions: async () => {
    const { documentId } = get()
    if (!documentId) return
    try {
      const res = await axios.get<{ versions: DocumentVersion[] }>(
        `/api/documents/${documentId}/versions`,
      )
      set({ versions: res.data.versions })
    } catch {
      // Versions list is decorative — silent on failure
    }
  },

  setSelectedSlide: (slideId) => set({ selectedSlideId: slideId }),

  updateSlide: (slideId, patch) => {
    const { storyboard } = get()
    if (!storyboard) return
    const slides = storyboard.slides.map((s) =>
      s.id === slideId ? { ...s, ...patch } : s,
    )
    set({
      storyboard: {
        ...storyboard,
        slides,
        total_timing_mins: recomputeTiming(slides),
      },
    })
    scheduleAutosave(get)
  },

  reorderSlides: (orderedIds) => {
    const { storyboard } = get()
    if (!storyboard) return
    const byId = new Map(storyboard.slides.map((s) => [s.id, s]))
    const reordered = orderedIds
      .map((id) => byId.get(id))
      .filter((s): s is Slide => s !== undefined)
    const renumbered = normaliseOrders(reordered)
    set({
      storyboard: {
        ...storyboard,
        slides: renumbered,
        total_timing_mins: recomputeTiming(renumbered),
      },
    })
    scheduleAutosave(get)
  },

  addSlide: (afterOrder) => {
    const { storyboard } = get()
    if (!storyboard) return
    const newSlide: Slide = {
      id:           crypto.randomUUID(),
      order:        afterOrder + 1,
      owner:        'Molly',
      timing_mins:  1.0,
      headline:     'New slide',
      key_point:    '',
      chart_ref:    null,
      speaker_note: '',
      live_demo:    false,
      transition:   '',
      ai_draft:     false,
    }
    const inserted: Slide[] = []
    for (const s of storyboard.slides) {
      inserted.push(s)
      if (s.order === afterOrder) inserted.push(newSlide)
    }
    if (afterOrder === 0) inserted.unshift(newSlide)
    if (!inserted.includes(newSlide)) inserted.push(newSlide)
    const renumbered = normaliseOrders(inserted)
    set({
      storyboard: { ...storyboard, slides: renumbered, total_timing_mins: recomputeTiming(renumbered) },
      selectedSlideId: newSlide.id,
    })
    scheduleAutosave(get)
  },

  removeSlide: (slideId) => {
    const { storyboard, selectedSlideId } = get()
    if (!storyboard) return
    const filtered = storyboard.slides.filter((s) => s.id !== slideId)
    const renumbered = normaliseOrders(filtered)
    set({
      storyboard: { ...storyboard, slides: renumbered, total_timing_mins: recomputeTiming(renumbered) },
      // If the deleted slide was selected, select the first remaining slide
      selectedSlideId: selectedSlideId === slideId
        ? (renumbered[0]?.id ?? null)
        : selectedSlideId,
    })
    scheduleAutosave(get)
  },

  saveNamedVersion: async (name, summary) => {
    const { documentId, storyboard } = get()
    if (!documentId || !storyboard) return
    set({ saving: true })
    try {
      await axios.post(`/api/documents/${documentId}/versions`, {
        content: storyboard, version_name: name, change_summary: summary,
      })
      set({ saving: false, lastSavedAt: new Date() })
      await get().loadVersions()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Save version failed'
      set({ saving: false, error: String(msg) })
    }
  },

  restoreVersion: async (versionId) => {
    const { documentId } = get()
    if (!documentId) return
    set({ saving: true })
    try {
      await axios.post(`/api/documents/${documentId}/restore/${versionId}`)
      // Reload the draft after the restore so the editor shows the
      // newly-rolled-back content
      await get().loadDocument(documentId)
      set({ saving: false, lastSavedAt: new Date() })
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Restore failed'
      set({ saving: false, error: String(msg) })
    }
  },

  clear: () => {
    if (autosaveTimer) clearTimeout(autosaveTimer)
    autosaveTimer = null
    set({
      documentId: null, storyboard: null, versions: [],
      loading: false, saving: false, lastSavedAt: null,
      error: null, selectedSlideId: null,
    })
  },
}))


// Auto-save is debounced so rapid edits (typing a headline character-by-character)
// don't fire a save per keystroke. CLAUDE.md spec is 30 seconds — the user gets
// a clean Last-saved indicator and we don't hammer the PATCH endpoint.
function scheduleAutosave(get: () => StoryboardState): void {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(() => {
    void autoSave(get)
  }, AUTOSAVE_DEBOUNCE_MS)
}

async function autoSave(get: () => StoryboardState): Promise<void> {
  const { documentId, storyboard } = get()
  if (!documentId || !storyboard) return
  useStoryboardStore.setState({ saving: true })
  try {
    await axios.patch(`/api/documents/${documentId}/draft`, { content: storyboard })
    useStoryboardStore.setState({ saving: false, lastSavedAt: new Date() })
  } catch {
    useStoryboardStore.setState({ saving: false })
  }
}
