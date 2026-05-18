/**
 * Types for the in-platform document editor — mirror the shapes the
 * /api/v1/documents/drafts endpoints return (backend tools/editor_drafts.py).
 */
import type { JSONContent } from '@tiptap/core'

export type EditorDocumentType =
  | 'midpoint_paper' | 'executive_brief' | 'presentation_deck'

/** A TipTap document JSON object (paper/brief). */
export type TipTapDoc = JSONContent

/** One slide in a presentation_deck draft's content_json. */
export interface DeckSlide {
  id: number
  title: string
  content: string
  data_points: string[]
  speaker_notes: string
  verified: boolean
  notes_written: boolean
}

/** A presentation_deck draft stores {slides:[...]} in content_json. */
export interface DeckContent {
  slides: DeckSlide[]
}

export interface EditorDraft {
  id: number
  document_type: EditorDocumentType
  owner_email: string
  title: string
  content_json: TipTapDoc | DeckContent | null
  content_text: string | null
  word_count: number
  version: number
  is_current: boolean
  is_deleted: boolean
  created_from: 'generated' | 'uploaded' | 'manual'
  created_at: string | null
  updated_at: string | null
}

export interface EditorDraftVersion {
  id: number
  draft_id: number
  version: number
  content_json: TipTapDoc | DeckContent | null
  content_text: string | null
  word_count: number
  version_label: string | null
  saved_at: string | null
  saved_by: string | null
}

/** Save state for the auto-save indicator. */
export type SaveState = 'idle' | 'saving' | 'saved' | 'error'
