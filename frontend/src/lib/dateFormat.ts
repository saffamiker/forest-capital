/**
 * dateFormat.ts — centralised date display helpers.
 *
 * May 24 2026 (UAT bug ID 278). Platform-wide convention is
 * MM-DD-YYYY for dates shown to the user. Charts and exports may
 * still use ISO YYYY-MM-DD where needed (CSV columns, axis ticks
 * where the canonical ordering matters), but every interactive
 * surface — tables, banners, tooltips, popovers, status pills —
 * renders dates as MM-DD-YYYY through these helpers.
 *
 * The functions are deliberately defensive: a null / empty / bad
 * input returns "—" rather than "Invalid Date" so a missing field
 * never reads as a broken UI.
 */


/**
 * Renders an ISO date (YYYY-MM-DD or full ISO timestamp) as
 * MM-DD-YYYY. Examples:
 *   "2026-05-24"              → "05-24-2026"
 *   "2026-05-24T12:34:56Z"    → "05-24-2026"
 *   null / undefined / ""     → "—"
 *   "not a date"              → "not a date" (passthrough)
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  // Pad two-digit month and day — the leading zero is the only way
  // MM-DD-YYYY stays correctly aligned across rows.
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(d.getUTCDate()).padStart(2, '0')
  const yyyy = String(d.getUTCFullYear())
  return `${mm}-${dd}-${yyyy}`
}


/**
 * Renders an ISO datetime as "MM-DD-YYYY HH:MM" in 24h format.
 * Use for timestamps that need the time component (last-saved at,
 * generated-at, etc.).
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const yyyy = String(d.getFullYear())
  const hh = String(d.getHours()).padStart(2, '0')
  const mn = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd}-${yyyy} ${hh}:${mn}`
}


/**
 * Renders an age in seconds as a human-readable relative time:
 *   "30s ago", "5 min ago", "2h ago", "3 days ago".
 *
 * Use for short-lived freshness indicators (cache age, "computed N
 * min ago", "last login"). The output never includes "in N" — every
 * caller is reporting an elapsed time from a known past moment.
 */
export function formatRelativeTime(
  seconds: number | null | undefined,
): string {
  if (seconds === null || seconds === undefined
      || !Number.isFinite(seconds)) return '—'
  const abs = Math.max(0, seconds)
  if (abs < 60) return `${Math.round(abs)}s ago`
  if (abs < 3600) return `${Math.round(abs / 60)} min ago`
  if (abs < 86400) return `${Math.round(abs / 3600)}h ago`
  return `${Math.round(abs / 86400)} days ago`
}
