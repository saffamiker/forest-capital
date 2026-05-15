/**
 * frontend/src/types/documents.ts
 *
 * Type contracts for Bob's section-structured documents (Sprint 6 Phase 10).
 * Mirrors the backend section-doc content schema in main.py
 * (_build_section_doc_content): every section carries both the immutable
 * AI draft and Bob's editable content so View AI Draft and Revert work
 * per-section without losing edits elsewhere.
 *
 * DocumentVersion is re-exported from storyboard.ts so the shared
 * VersionHistory pattern uses one type across both Bob and Molly editors.
 */
import type { DocumentVersion } from './storyboard'

export type SectionDocType =
  | 'midpoint_paper'
  | 'executive_brief'
  | 'analytical_appendix'

export interface DocumentSection {
  id:           string
  title:        string
  // Immutable on creation — the original Academic Writer prose. Bob's
  // View AI Draft side panel reads this; Revert copies it back into
  // `content`. Regenerate AI replaces this with a fresh run.
  ai_draft:     string
  // Bob's current text. Starts equal to ai_draft on document creation.
  content:      string
  last_edited:  string
}

export interface SectionDocument {
  doc_type:  SectionDocType
  title:     string
  subtitle:  string
  sections:  DocumentSection[]
}

export interface SectionDocDraftResponse {
  document_id:  string | null
  content:      SectionDocument
  persistence:  'saved' | 'unavailable'
  message?:     string
}

export interface DocumentDraftResponse {
  document_id:       string
  content:           SectionDocument
  last_saved_at:     string | null
  based_on_version:  string | null
}

export interface RegenerateSectionResponse {
  ai_draft:   string
  section_id: string
}

export type { DocumentVersion }
