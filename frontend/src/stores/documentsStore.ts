/**
 * frontend/src/stores/documentsStore.ts
 *
 * Bob's section-document editor state — mirrors storyboardStore's
 * pattern for a different content shape. Each section carries an
 * immutable AI draft alongside Bob's editable content, so the UI can
 * surface View AI Draft / Regenerate / Revert per section without
 * losing edits to other sections.
 *
 * Auto-save fires 30 seconds after the last edit (debounced). Named
 * versions go through saveNamedVersion. Word export is a synchronous
 * download — handled in the SectionEditor page directly.
 */
import { create } from 'zustand'
import axios from 'axios'
import type {
  DocumentSection,
  SectionDocument,
  SectionDocType,
  SectionDocDraftResponse,
  DocumentDraftResponse,
  DocumentVersion,
  RegenerateSectionResponse,
} from '../types/documents'

const AUTOSAVE_DEBOUNCE_MS = 30_000

interface DocumentsState {
  documentId:        string | null
  document:          SectionDocument | null
  versions:          DocumentVersion[]
  loading:           boolean
  saving:            boolean
  lastSavedAt:       Date | null
  error:             string | null
  // Which section is currently in focus in the editor. The editor
  // surface uses this to scroll-into-view and outline the active
  // section card.
  selectedSectionId: string | null

  // Lifecycle
  createDraft:    (docType: SectionDocType) => Promise<string | null>
  loadDocument:   (documentId: string) => Promise<void>
  loadVersions:   () => Promise<void>
  clear:          () => void

  // Section mutations (each triggers debounced auto-save)
  setSelectedSection: (sectionId: string | null) => void
  updateSection:      (sectionId: string, patch: Partial<DocumentSection>) => void
  // Regenerate AI for one section — calls backend and updates ai_draft
  // only. The frontend chooses whether to also overwrite `content`.
  regenerateSection:  (sectionId: string) => Promise<string | null>
  // Revert: copies the immutable ai_draft back into `content` for one
  // section. Persisted via the same debounced auto-save path.
  revertSection:      (sectionId: string) => void

  // Persistence
  saveNamedVersion:   (name: string, summary?: string) => Promise<void>
  restoreVersion:     (versionId: string) => Promise<void>
}

let autosaveTimer: ReturnType<typeof setTimeout> | null = null


function clearAutosave() {
  if (autosaveTimer) {
    clearTimeout(autosaveTimer)
    autosaveTimer = null
  }
}


/**
 * Schedule a debounced PATCH of the current document. Reads documentId
 * and document fresh from the store inside the timer so a rapid edit
 * → reload sequence doesn't accidentally save stale content.
 */
function scheduleAutosave(get: () => DocumentsState, set: (patch: Partial<DocumentsState>) => void) {
  clearAutosave()
  autosaveTimer = setTimeout(async () => {
    const { documentId, document } = get()
    if (!documentId || !document) return
    set({ saving: true })
    try {
      await axios.patch(`/api/documents/${documentId}/draft`, { content: document })
      set({ saving: false, lastSavedAt: new Date() })
    } catch {
      // Autosave failures are surfaced as a banner in the editor but
      // never thrown — we don't want a transient network blip to break
      // Bob's typing flow.
      set({ saving: false, error: 'Auto-save failed — your edits are still local.' })
    }
  }, AUTOSAVE_DEBOUNCE_MS)
}


export const useDocumentsStore = create<DocumentsState>((set, get) => ({
  documentId: null,
  document: null,
  versions: [],
  loading: false,
  saving: false,
  lastSavedAt: null,
  error: null,
  selectedSectionId: null,

  createDraft: async (docType) => {
    set({ loading: true, error: null })
    try {
      const res = await axios.post<SectionDocDraftResponse>(
        '/api/documents/section-doc/draft',
        { doc_type: docType },
      )
      const firstSectionId = res.data.content.sections[0]?.id ?? null
      set({
        documentId:        res.data.document_id,
        document:          res.data.content,
        selectedSectionId: firstSectionId,
        loading:           false,
        error: res.data.persistence === 'unavailable'
          ? res.data.message ?? 'Document drafted but not persisted.'
          : null,
      })
      return res.data.document_id
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to create document draft'
      set({ loading: false, error: String(msg) })
      return null
    }
  },

  loadDocument: async (documentId) => {
    set({ loading: true, error: null })
    try {
      const res = await axios.get<DocumentDraftResponse>(
        `/api/documents/${documentId}`,
      )
      const firstSectionId = res.data.content.sections[0]?.id ?? null
      set({
        documentId,
        document:          res.data.content,
        selectedSectionId: firstSectionId,
        loading:           false,
        lastSavedAt:       res.data.last_saved_at ? new Date(res.data.last_saved_at) : null,
      })
      await get().loadVersions()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Failed to load document'
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
      set({ versions: res.data.versions ?? [] })
    } catch {
      // Versions list is non-critical for editing — silent failure.
    }
  },

  setSelectedSection: (sectionId) => set({ selectedSectionId: sectionId }),

  updateSection: (sectionId, patch) => {
    const { document } = get()
    if (!document) return
    const updated: SectionDocument = {
      ...document,
      sections: document.sections.map((s) =>
        s.id === sectionId
          ? { ...s, ...patch, last_edited: new Date().toISOString() }
          : s,
      ),
    }
    set({ document: updated })
    scheduleAutosave(get, set)
  },

  regenerateSection: async (sectionId) => {
    const { documentId, document } = get()
    if (!documentId || !document) return null
    set({ saving: true, error: null })
    try {
      const res = await axios.post<RegenerateSectionResponse>(
        `/api/documents/${documentId}/sections/${sectionId}/regenerate`,
      )
      // Update ONLY ai_draft — Bob's `content` stays intact. The UI
      // surfaces a "Replace your text with new AI draft?" affordance
      // if Bob wants to commit the change.
      const updated: SectionDocument = {
        ...document,
        sections: document.sections.map((s) =>
          s.id === sectionId
            ? { ...s, ai_draft: res.data.ai_draft }
            : s,
        ),
      }
      set({ document: updated, saving: false })
      scheduleAutosave(get, set)
      return res.data.ai_draft
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Regenerate failed'
      set({ saving: false, error: String(msg) })
      return null
    }
  },

  revertSection: (sectionId) => {
    const { document } = get()
    if (!document) return
    const section = document.sections.find((s) => s.id === sectionId)
    if (!section) return
    // Copy ai_draft into content. ai_draft itself never changes here —
    // it's the immutable side. Bob's edits are gone after revert; the
    // UI shows a confirmation dialog before calling this.
    const updated: SectionDocument = {
      ...document,
      sections: document.sections.map((s) =>
        s.id === sectionId
          ? { ...s, content: s.ai_draft, last_edited: new Date().toISOString() }
          : s,
      ),
    }
    set({ document: updated })
    scheduleAutosave(get, set)
  },

  saveNamedVersion: async (name, summary) => {
    const { documentId, document } = get()
    if (!documentId || !document) return
    // Flush any pending auto-save first so the named version captures
    // the current draft, not a stale one. We do this by clearing the
    // debounce timer and patching synchronously before snapshotting.
    clearAutosave()
    try {
      await axios.patch(`/api/documents/${documentId}/draft`, { content: document })
      await axios.post(
        `/api/documents/${documentId}/versions`,
        {
          content: document,
          version_name: name,
          change_summary: summary,
        },
      )
      set({ lastSavedAt: new Date() })
      await get().loadVersions()
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Save version failed'
      set({ error: String(msg) })
    }
  },

  restoreVersion: async (versionId) => {
    const { documentId } = get()
    if (!documentId) return
    try {
      await axios.post(`/api/documents/${documentId}/restore/${versionId}`)
      // Re-fetch the draft so the editor reflects the restored content.
      await get().loadDocument(documentId)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Restore failed'
      set({ error: String(msg) })
    }
  },

  clear: () => {
    clearAutosave()
    set({
      documentId: null, document: null, versions: [],
      loading: false, saving: false, lastSavedAt: null, error: null,
      selectedSectionId: null,
    })
  },
}))
