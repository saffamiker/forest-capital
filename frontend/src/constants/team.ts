/**
 * team.ts — the project team allowlist, the frontend mirror of the
 * backend config.PROJECT_TEAM_EMAILS.
 *
 * The platform has two access tiers: any authenticated user may explore
 * the analytics, charts and AI council; the project team additionally
 * has the action features (document upload, report generation, the test
 * runner, Academic Review). `isTeamMember` is the single frontend
 * predicate behind both — keep this list in lockstep with the backend.
 */

export const PROJECT_TEAM_EMAILS: readonly string[] = [
  'ruurdsm@queens.edu',   // Michael Ruurds
  'murdockm@queens.edu',  // Molly Murdock
  'thaob@queens.edu',     // Bob Thao
]

/** True when the email belongs to a project team member. */
export function isTeamMember(email: string | null | undefined): boolean {
  if (!email) return false
  return PROJECT_TEAM_EMAILS.includes(email.trim().toLowerCase())
}
