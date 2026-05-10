export interface GlossaryTerm {
  hover: string
  what: string
  why: string
  in_context?: string
  verdict?: string
}

export interface ParameterExplanation {
  hover: string
  what: string
  why: string
  effect_now?: string
  what_if?: string
}

export interface PersonaExplanation {
  plain_english: string
  design_decisions: string
  this_session: string
}

export interface QAItemExplanation {
  what: string
  why: string
  failure_meaning: string
  how_tested: string
}

export interface ChartExplanation {
  chart_id: string
  hover_summary: string
  purpose: string
  how_to_read: string
  key_callouts: string[]
  narrative: string
  what_to_watch: string
}

export interface GlossaryStore {
  terms: Record<string, GlossaryTerm>
  parameters: Record<string, ParameterExplanation>
  personas: Record<string, PersonaExplanation>
  qa: Record<string, QAItemExplanation>
  charts: Record<string, ChartExplanation>
}
