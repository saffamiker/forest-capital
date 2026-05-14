/**
 * frontend/src/types/advisor.ts
 *
 * Type contracts for the Academic Advisor (Agent 10) endpoints.
 *
 * Every type mirrors the Pydantic schema in backend/models/schemas.py
 * and the dict shape returned by agents/academic_advisor.py. Keep them
 * in sync — the citation integrity invariant assumes both ends agree
 * on what counts as "verified".
 */

export type DeliverableType = 'midpoint' | 'appendix' | 'brief' | 'presentation'

export type AdvisorVerdict = 'plausible' | 'implausible' | 'uncertain'

export interface VerifiedCitation {
  title:      string
  url:        string
  // Optional fields populated by the analyse/citations endpoints. The
  // verify endpoint uses VerifiedEvidence below instead, which carries
  // a one-line summary in place of authors/year.
  authors?:   string
  year?:      number
  relevance?: string
  // 2-3 sentence passage drawn from web_fetch of `url`. The backend
  // emits the field on every citation. A non-empty string means the
  // page was fetched and the model extracted a corroborating passage;
  // null means web_fetch failed or didn't run for this URL — the UI
  // shows "Excerpt unavailable — click to verify directly" on hover.
  excerpt:    string | null
  // Always true after the backend filter — the post-filter list never
  // contains a citation that wasn't returned by web_search. We keep the
  // field so the frontend can show a "verified" badge regardless.
  verified:   true
}

export interface VerifiedEvidence {
  title:   string
  url:     string
  summary: string
}

export interface VerifiedSource {
  title:    string
  url:      string
  verified: true
}

// Response from POST /api/advisor/analyse
export interface AdvisorAnalysis {
  key_findings:     string[]
  guidance:         string[]
  citations:        VerifiedCitation[]
  potential_issues: string[]
  verified_sources?: VerifiedSource[]
  deliverable_type?: string
  error?:           string
}

// Response from POST /api/advisor/verify-finding
export interface AdvisorVerification {
  supporting_evidence:    VerifiedEvidence[]
  contradicting_evidence: VerifiedEvidence[]
  verdict:                AdvisorVerdict
  reasoning:              string
  verified_sources?:      VerifiedSource[]
}

// Response from POST /api/advisor/citations
export interface AdvisorCitationsResponse {
  citations:         VerifiedCitation[]
  verified_sources?: VerifiedSource[]
}
