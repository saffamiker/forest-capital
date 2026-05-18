export interface AgentMessage {
  agent: string
  role: string
  model: string
  content: string
  is_final: boolean
}

export interface CouncilResponse {
  query: string
  messages: AgentMessage[]
  final_recommendation: string
  consensus_reached: boolean
  /** "live" = a real council run; "fallback" = demo/mock data returned
   *  because the pipeline failed or the request ran in the test env. */
  mode?: 'live' | 'fallback'
  /** Viewer council allocation after this query — present only for a
   *  limited (non-unlimited) user. */
  council_queries_used?: number
  council_queries_limit?: number | null
}

export type Verdict = 'PASS' | 'WARN' | 'FAIL'

export interface QACheck {
  check_id: string
  check: string
  description: string
  status: Verdict
  category: string
  evidence: string
  fix?: string | null
}

export interface QAAuditResult {
  sprint?: string
  verdict: Verdict
  checks_passed: number
  checks_warned: number
  checks_failed: number
  checks_total: number
  summary?: string
  items: QACheck[]
  limitations?: string[]
  data_caveats?: string[]
  model_assumptions?: string[]
  // The full LLM analysis text. LLM-assessed checks carry a placeholder
  // evidence line pointing here — the panel renders this under each such
  // warning/fail item so the reasoning is visible, not just referenced.
  raw_analysis?: string
}
