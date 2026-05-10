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
  id: number
  label: string
  verdict: Verdict
  category: string
  detail: string
}

export interface QAAuditResult {
  overall_verdict: Verdict
  passed: number
  warned: number
  failed: number
  total_checks?: number
  checks: QACheck[]
}
