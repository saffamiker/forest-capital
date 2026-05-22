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

// Per-check verdict. INCOMPLETE was added May 22 2026 as a fourth
// first-class status — it signals "the audit did not finish this
// check", NOT "this check has a concern". INCOMPLETE never drives the
// overall report verdict (which stays a three-tier PASS / WARN /
// FAIL). See backend/agents/qa_agent.py for the full contract.
export type Verdict = 'PASS' | 'WARN' | 'FAIL' | 'INCOMPLETE'

// The four action paths a WARN or FAIL finding can take. Locked here
// so a stray action_type from the backend (a model hallucination, a
// future contract drift) does not render a button variant the UI does
// not understand. See backend/agents/qa_agent._ACTION_TYPES.
export type QAActionType =
  | 'code_fix'              // The platform has a defect to fix in code.
  | 'methodology_decision'  // Ambiguous — intentional design vs error.
                            // Renders BOTH Mark as Intentional AND Flag
                            // for Fix buttons; the team decides.
  | 'disclosure_required'   // Acceptable but must be disclosed in the
                            // academic report. Renders Copy Disclosure
                            // Text — the pre-drafted sentence is the
                            // disclosure_text field below.
  | 'rerun_required'        // Agent could not complete the check.
                            // Renders Re-run Audit; INCOMPLETE checks
                            // default to this action_type.

export interface QACheck {
  check_id: string
  check: string
  description: string
  status: Verdict
  category: string
  evidence: string
  fix?: string | null

  // Structured WARN / FAIL fields (May 22 2026 contract). PASS sections
  // typically have all five Nones — the brief evidence above is the
  // substance there. WARN / FAIL sections carry every field except
  // disclosure_text (present only when action_type=disclosure_required).
  // INCOMPLETE sections carry action_type=rerun_required + a remediation
  // pointing the user at the Re-run Audit button.
  finding?: string | null
  implication?: string | null
  remediation?: string | null
  action_type?: QAActionType | null
  disclosure_text?: string | null
}

// Overall audit verdict. Strictly THREE-tier — INCOMPLETE is a per-
// check status that signals "the audit did not finish this check"; it
// never drives the overall verdict. The backend's _build_report
// enforces this (verdict = FAIL > WARN > PASS, INCOMPLETE excluded);
// narrowing the type here so call sites (worstVerdict, badge colour
// lookups) can rely on the 3-tier set without coercion.
export type OverallVerdict = Exclude<Verdict, 'INCOMPLETE'>

export interface QAAuditResult {
  sprint?: string
  verdict: OverallVerdict
  checks_passed: number
  checks_warned: number
  checks_failed: number
  // INCOMPLETE checks count separately so a baseless WARN does not
  // inflate the warned total. Optional for back-compat with cached
  // audit rows persisted before the May 22 2026 schema landed —
  // those rows simply have no incompletes and the panel reads 0.
  checks_incomplete?: number
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
