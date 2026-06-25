/**
 * Types for the in-platform document editor — mirror the shapes the
 * /api/v1/documents/drafts endpoints return (backend tools/editor_drafts.py).
 */
import type { JSONContent } from '@tiptap/core'

export type EditorDocumentType =
  | 'midpoint_paper' | 'executive_brief' | 'presentation_deck'
  | 'presentation_script' | 'analytical_appendix'

/** A TipTap document JSON object (paper/brief). */
export type TipTapDoc = JSONContent

// ── Canvas presentation-deck schema ──────────────────────────────────────
// A presentation_deck draft stores a free-form 960x540 (16:9) canvas per
// slide — see migration 022. Each element carries absolute coordinates;
// the centre panel renders them on a Konva Stage.

/** Fields shared by every positioned canvas element. */
export interface CanvasElementBase {
  id: string
  x: number
  y: number
  width: number
  height: number
  locked: boolean
}

/** A text element — Konva.Text, inline-editable on double-click. */
export interface CanvasTextElement extends CanvasElementBase {
  type: 'text'
  content: string
  fontSize: number
  fontWeight: 'normal' | 'bold'
  /** Optional — migration 022 emits no italic; absent reads as 'normal'. */
  fontStyle?: 'normal' | 'italic'
  color: string
}

/** A chart element — a platform chart rendered server-side as a PNG. */
export interface CanvasChartElement extends CanvasElementBase {
  type: 'chart'
  /** A key from GET /api/v1/charts/available. */
  chartKey: string
  /** The presenter confirms the chart reflects current platform data. */
  verified: boolean
}

export type CanvasElement = CanvasTextElement | CanvasChartElement

/** One slide of a presentation_deck draft's content_json. */
export interface CanvasSlide {
  id: number
  title: string
  background: string
  speaker_notes: string
  elements: CanvasElement[]
  /** The presenter assigned to this slide — null/absent until assigned. */
  speaker?: string | null
}

/** A presentation_deck draft stores {slides:[...]} in content_json. */
export interface CanvasDeck {
  slides: CanvasSlide[]
}

/** Per-check flag detail from the post-generation audit
 *  (tools.document_audit). Numeric and consistency flags carry
 *  strategy/metric/value triples; direction flags carry the
 *  superlative + sentence; citation flags carry the unfound author.
 *  The exact field set varies per check — the banner renders
 *  whatever fields are present without strict typing. */
export type AuditFlag = Record<string, unknown>

export interface AuditWarnings {
  flags_by_check: {
    numeric:     AuditFlag[]
    direction:   AuditFlag[]
    consistency: AuditFlag[]
    citation:    AuditFlag[]
  }
  flag_counts: {
    numeric:     number
    direction:   number
    consistency: number
    citation:    number
    total:       number
  }
  skipped?: Record<string, string>
}

export interface EditorDraft {
  id: number
  document_type: EditorDocumentType
  owner_email: string
  title: string
  content_json: TipTapDoc | CanvasDeck | null
  content_text: string | null
  word_count: number
  version: number
  is_current: boolean
  is_deleted: boolean
  created_from: 'generated' | 'uploaded' | 'manual'
  created_at: string | null
  updated_at: string | null
  /** Per-check post-generation audit flag list. NULL on a clean
   *  run. The frontend renders AuditWarningsBanner when present. */
  audit_warnings?: AuditWarnings | null
  /** June 25 2026 -- data_hash the draft was generated against.
   *  Used by DataHashChip + the export-warning modal pre-flight.
   *  Optional on the type because pre-migration-057 deploys never
   *  populated it; null is treated as "unknown" and the chip
   *  hides entirely. */
  data_hash?: string | null
}

export interface EditorDraftVersion {
  id: number
  draft_id: number
  version: number
  content_json: TipTapDoc | CanvasDeck | null
  content_text: string | null
  word_count: number
  version_label: string | null
  saved_at: string | null
  saved_by: string | null
}

/** Save state for the auto-save indicator. */
export type SaveState = 'idle' | 'saving' | 'saved' | 'error'
