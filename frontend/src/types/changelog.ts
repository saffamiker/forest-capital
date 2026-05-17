/**
 * Types for the changelog feature — mirror the /api/v1/changelog
 * endpoint payloads (backend tools/changelog.py).
 */

export interface ChangelogEntry {
  id: number
  version: number
  released_at: string
  title: string
  description: string
  /** Why the feature helps the team earn higher marks. */
  academic_rationale: string
  tour_step_id: string | null
}

/** GET /api/v1/changelog/unseen */
export interface UnseenChangelogResponse {
  entries: ChangelogEntry[]
  has_tour_update: boolean
  tour_version: number
}

/** GET /api/v1/changelog */
export interface AllChangelogResponse {
  entries: ChangelogEntry[]
}
