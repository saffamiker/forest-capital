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

/** Chart visual / data configuration, prepopulated at deck
 *  generation time and editable in the slide editor. All fields
 *  are OPTIONAL -- when absent the renderer uses its hardcoded
 *  defaults (so legacy drafts created before this schema rev
 *  render unchanged). The fields cover presentation only; the
 *  underlying data source is still the verified analytics cache. */
export interface ChartConfig {
  /** Logical chart kind. The renderer dispatches off chartKey
   *  for the data wiring; chart_type is an editor hint + a guard
   *  against silently swapping a line-chart's renderer for a
   *  bar-chart's data shape. */
  chart_type?: 'line' | 'bar' | 'scatter' | 'waterfall' | 'table'
  /** Override the default chart title (defaults to the renderer's
   *  hardcoded string). */
  title?: string
  /** Caption rendered below the chart on the slide. */
  caption?: string
  /** Mirror of the chart element's chartKey -- carried in config
   *  so the editor can show / re-assign a renderer without
   *  reaching into the parent element. */
  renderer_key?: string
  color_scheme?: {
    primary?:   string
    secondary?: string
    benchmark?: string
    accent?:    string
  }
  axis?: {
    x_label?: string
    y_label?: string
    x_min?: number | null
    x_max?: number | null
    y_min?: number | null
    y_max?: number | null
  }
  /** Per-series controls -- visibility, label, color override.
   *  Prepopulated with one entry per strategy in the analytics
   *  cache at generation time (all visible by default). */
  series?: Array<{
    key:     string
    label:   string
    visible: boolean
    color?:  string
  }>
  date_range?: {
    start?:  string | null
    end?:    string | null
    preset?: 'full' | 'post_2022' | 'oos_only' | 'custom'
  }
  highlight_regime_breaks?: boolean
  show_benchmark?: boolean
}

/** Table visual / data configuration, prepopulated at deck
 *  generation time and editable in the slide editor. */
export interface TableConfig {
  /** Logical table kind -- drives the default columns + the
   *  cache the rows are pulled from. */
  table_type?:
    | 'performance' | 'correlation' | 'factor_loadings' | 'drawdown'
  title?:   string
  caption?: string
  /** Strategy IDs to include as rows (when the table's cell
   *  data is looked up at render time from the analytics cache)
   *  OR a list-of-lists where each row already carries its cell
   *  data verbatim (the deck-generation prepopulated shape).
   *  The renderer accepts both; the Configure panel writes
   *  whichever shape the user is editing. */
  rows?: string[] | string[][]
  /** Metric column ids to include (e.g. 'sharpe', 'max_dd').
   *  Empty / absent => the table_type's default column set. */
  columns?: string[]
  highlight_best?:  boolean
  highlight_worst?: boolean
  decimal_places?:  number
}

/** A chart element — a platform chart rendered server-side as a PNG. */
export interface CanvasChartElement extends CanvasElementBase {
  type: 'chart'
  /** A key from GET /api/v1/charts/available. */
  chartKey: string
  /** The presenter confirms the chart reflects current platform data. */
  verified: boolean
  /** Optional appearance + filtering overrides for the renderer.
   *  Absence preserves the renderer's hardcoded defaults. */
  chart_config?: ChartConfig
}

/** A native table element -- promoted to a first-class canvas
 *  element type June 26 2026 so Molly can edit table appearance +
 *  row/column selection in the slide editor without falling back
 *  to markdown-pipe text inside a body element. Renders as a real
 *  PPTX <a:tbl> on export. */
export interface CanvasTableElement extends CanvasElementBase {
  type: 'table'
  /** Optional appearance + selection overrides. */
  table_config?: TableConfig
}

export type CanvasElement =
  | CanvasTextElement | CanvasChartElement | CanvasTableElement

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
    // June 26 2026 -- the backend has been emitting these five
    // additional check categories (document_audit.py flag_counts
    // sums all of them into `total`) but the banner never
    // rendered FlagGroup blocks for them. Result: a draft with
    // e.g. 3 numeric + 11 story_plan flags showed "Audit flagged
    // 14 items" in the header but only 3 in the expanded view.
    // Adding here so the banner can iterate all categories.
    story_plan?:              AuditFlag[]
    required_citations?:      AuditFlag[]
    section_word_count?:      AuditFlag[]
    unresolved_placeholders?: AuditFlag[]
    raw_numeric?:             AuditFlag[]
  }
  flag_counts: {
    numeric:     number
    direction:   number
    consistency: number
    citation:    number
    story_plan?:              number
    required_citations?:      number
    section_word_count?:      number
    unresolved_placeholders?: number
    raw_numeric?:             number
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
