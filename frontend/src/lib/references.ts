/**
 * frontend/src/lib/references.ts
 *
 * Static-asset fetch + lookup for academic references. The Level 3
 * "Learn More" panel in ExplainableText cites these. Loaded once per
 * session (memoised module-level promise) so the side panel opens
 * instantly the second time it's used.
 *
 * The file is the same references.json the backend Academic Writer
 * Agent draws from — checked in at backend/data/references.json and
 * copied to frontend/public/references.json. If the two ever drift,
 * the Academic Writer Agent test (test_academic_writer) catches it.
 */

export interface Reference {
  author:    string
  year:      number
  title:     string
  source:    string
  apa:       string
  use_for:   string[]
}

export type ReferencesByKey = Record<string, Reference>

let cachedPromise: Promise<ReferencesByKey> | null = null

export function loadReferences(): Promise<ReferencesByKey> {
  if (cachedPromise) return cachedPromise
  cachedPromise = fetch('/references.json')
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({} as ReferencesByKey))
  return cachedPromise
}

/**
 * Find the best-matching reference for a term. Falls back to fuzzy
 * matching against the `use_for` tags so callers can pass natural
 * phrases like "Sharpe ratio" or "FDR correction" without knowing
 * the canonical reference keys.
 */
export function findReferenceFor(
  refs: ReferencesByKey,
  term: string,
): Reference | null {
  const needle = term.toLowerCase()
  for (const ref of Object.values(refs)) {
    if (ref.use_for.some((tag) => tag.toLowerCase().includes(needle))) {
      return ref
    }
  }
  // Secondary fallback: match against the title
  for (const ref of Object.values(refs)) {
    if (ref.title.toLowerCase().includes(needle)) {
      return ref
    }
  }
  return null
}
