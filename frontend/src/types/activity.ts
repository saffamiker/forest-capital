/**
 * Types for the Team Activity feature — mirror the shapes returned by
 * GET /api/v1/activity/team and /summary (backend tools/activity_log.py).
 */

/** The kinds of row the unified timeline interleaves. */
export type ActivityKind =
  | 'commit' | 'council' | 'academic_review' | 'qa'
  | 'document_upload' | 'page_view'
  | 'test_pass' | 'test_failure' | 'test_failure_resolved' | 'test_feedback'

/** One row of the unified timeline. Kind-specific fields are optional —
 *  which ones are populated depends on `kind`. */
export interface ActivityEvent {
  kind: ActivityKind
  timestamp: string | null
  user: string
  user_name: string
  session_type: 'analytical' | 'testing' | null
  // commit
  sha?: string
  message?: string
  files_changed?: number | null
  insertions?: number | null
  deletions?: number | null
  github_url?: string | null
  branch?: string
  // agent interaction
  question_text?: string | null
  agents_involved?: string[] | null
  response_summary?: string | null
  metadata?: Record<string, unknown> | null
  // page view
  page?: string
  duration_seconds?: number | null
}

export interface TeamActivityResponse {
  events: ActivityEvent[]
  total_returned: number
  limit: number
  offset: number
}

export interface MemberSummary {
  user: string
  user_name: string
  council_interactions: number
  academic_review_sessions: number
  document_uploads: number
  qa_audits: number
  page_views: number
  last_active: string | null
  most_used_features: string[]
}

export interface AgentCount {
  agent: string
  count: number
}

export interface LastAcademicReview {
  user: string
  user_name: string
  timestamp: string | null
  overall_rating: string | null
}

export interface ActivitySummary {
  per_member: MemberSummary[]
  commits: { total: number; this_week: number; by_author: Record<string, number> }
  most_active_agents: AgentCount[]
  last_academic_review: LastAcademicReview | null
  total_interactions: number
  analytical_sessions_only: boolean
  test_coverage?: { steps_attested: number; testers: number }
}

/** One row of the AI cost summary — a member or an interaction type. */
export interface CostRow {
  user?: string
  user_name?: string
  interaction_type?: string
  cost_usd: number
  input_tokens: number
  output_tokens: number
  interactions: number
}

/** GET /api/v1/activity/cost-summary — AI token spend. */
export interface CostSummary {
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
  total_interactions: number
  by_member: CostRow[]
  by_type: CostRow[]
  analytical_sessions_only: boolean
}
