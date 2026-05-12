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
}
