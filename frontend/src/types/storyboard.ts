/**
 * frontend/src/types/storyboard.ts
 *
 * TypeScript mirrors of the storyboard JSON shape stored in
 * document_drafts.content. Schema matches CLAUDE.md Section 14 — every
 * field on the backend Slide dict has a TS counterpart here.
 */

export type SlideOwner = 'Molly' | 'Michael' | 'Bob' | 'All'

export interface Slide {
  id:           string
  order:        number
  owner:        SlideOwner
  timing_mins:  number
  headline:     string
  key_point:    string
  chart_ref:    string | null
  speaker_note: string
  live_demo:    boolean
  transition:   string
  ai_draft:     boolean
}

export interface Storyboard {
  slides:            Slide[]
  total_timing_mins: number
  generated_at?:     string
  ai_draft?:         boolean
}

export interface StoryboardDraftResponse {
  document_id:  string | null
  storyboard:   Storyboard
  persistence:  'saved' | 'unavailable'
  message?:     string
}

export interface DocumentVersion {
  id:              string
  version_number:  number
  version_name:    string | null
  change_summary:  string | null
  created_at:      string | null
  created_by:      string
  is_auto_save:    boolean
  restored_from:   string | null
}

export interface AssistantDiff {
  removed: string[]
  added:   string[]
}

export interface AssistantResponse {
  suggestion:   string
  diff:         AssistantDiff
  explanation:  string
  confidence:   number
  out_of_scope?: boolean
  mock?:         boolean
}
