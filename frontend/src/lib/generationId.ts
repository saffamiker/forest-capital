/**
 * Defensive integer coercion for report-generation IDs.
 *
 * Background — May 24 2026
 * ------------------------
 * The backend `/api/v1/reports/generations/{id}` family expects a plain
 * positive integer. The frontend was observed sending IDs in the
 * colon-separated form "3:1" / "1:1" — almost certainly the product of a
 * stringified composite (generation_id:paper_revision) accidentally
 * threaded into a URL builder. Those requests reach the backend as
 * 422/500 noise, and the user sees a broken adjudicate / delete /
 * version-history action.
 *
 * `safeGenerationId` is the single defensive seam. Every URL that
 * embeds a generation ID MUST run the candidate through this helper
 * first; if it returns null, skip the request entirely rather than
 * firing a doomed call.
 *
 *   safeGenerationId(3)         === 3
 *   safeGenerationId("3")       === 3
 *   safeGenerationId(3.7)       === 3   (truncated)
 *   safeGenerationId("3:1")     === null  (NaN after Number)
 *   safeGenerationId(null)      === null
 *   safeGenerationId(undefined) === null
 *   safeGenerationId("")        === null
 *   safeGenerationId(-1)        === null
 *   safeGenerationId(0)         === null   (no row id is zero in our schema)
 *
 * Co-located with the URL builder helpers so the canonical path is one
 * import away. Never inline `Math.trunc(Number(id))` at a call site;
 * always go through this helper so the contract stays one place to
 * audit when the bug reproduces.
 */
export function safeGenerationId(id: unknown): number | null {
  if (id === null || id === undefined) return null
  const n = Math.trunc(Number(id))
  if (!Number.isFinite(n) || n <= 0) return null
  return n
}

/**
 * Convenience — build a `/api/v1/reports/generations/{id}` URL with
 * the ID coerced through `safeGenerationId`. Returns null when the
 * input is invalid; callers should treat null as "skip the request".
 *
 *   generationUrl(3, "/versions")  === "/api/v1/reports/generations/3/versions"
 *   generationUrl("3:1")           === null
 */
export function generationUrl(
  id: unknown,
  suffix: string = '',
): string | null {
  const safe = safeGenerationId(id)
  if (safe === null) return null
  const path = suffix.startsWith('/') || suffix === ''
    ? suffix
    : `/${suffix}`
  return `/api/v1/reports/generations/${safe}${path}`
}
