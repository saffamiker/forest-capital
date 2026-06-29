/**
 * RegimeFreshnessBadge.tsx -- June 28 2026.
 *
 * Visual freshness indicator for the CIO Live Recommendation
 * card. Displays a relative "Updated 4 minutes ago" badge below
 * the regime classification + confidence line, color-coded by
 * staleness:
 *   - green  if < 5 minutes old
 *   - amber  if 5-15 minutes old (approaching the 15-min TTL)
 *   - red    if > 15 minutes old (TTL has expired)
 *
 * Hover shows the absolute UTC timestamp for precision.
 *
 * Updates live every 15 seconds via setInterval so the relative
 * label + color stay current without requiring a page refresh.
 * The interval is cheap (one Date.now() comparison + a re-render
 * of a single short text node).
 *
 * Props:
 *   `computed_at`   ISO 8601 string of when the regime was
 *                   computed. Source: cio_recommendation.
 *                   computed_at OR the HMM event timestamp,
 *                   whichever the caller passes in. Null/missing
 *                   renders nothing (graceful degrade).
 */
import { useEffect, useState } from 'react'


export interface RegimeFreshnessBadgeProps {
  computed_at?: string | null
}


export function RegimeFreshnessBadge(
  { computed_at }: RegimeFreshnessBadgeProps,
): React.ReactElement | null {
  // Tick state forces a re-render every 15 seconds so the
  // relative label updates without a page refresh.
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!computed_at) return
    const id = window.setInterval(
      () => setTick((n) => n + 1), 15_000)
    return () => window.clearInterval(id)
  }, [computed_at])

  if (!computed_at) return null
  const parsed = _parseUTC(computed_at)
  if (parsed === null) return null

  const ageMs = Date.now() - parsed
  // Defensive: future timestamps (clock skew) display as "just now".
  const ageMinutes = Math.max(0, Math.floor(ageMs / 60_000))

  const { relativeLabel, tone } = _formatAge(ageMinutes)
  const absoluteUTC = _formatAbsoluteUTC(parsed)

  return (
    <div
      data-testid="regime-freshness-badge"
      data-age-minutes={ageMinutes}
      data-tone={tone}
      className={
        'mt-1 inline-flex items-center gap-1 text-2xs '
        + 'font-mono cursor-default '
        + (tone === 'green'
          ? 'text-success'
          : tone === 'amber'
            ? 'text-warning'
            : 'text-danger')}
      title={absoluteUTC}>
      <span
        aria-hidden="true"
        className={
          'inline-block w-1.5 h-1.5 rounded-full '
          + (tone === 'green'
            ? 'bg-success'
            : tone === 'amber'
              ? 'bg-warning'
              : 'bg-danger')} />
      <span>{relativeLabel}</span>
    </div>
  )
}


// ── Helpers ────────────────────────────────────────────────


/**
 * Parse an ISO-ish timestamp into a millisecond epoch. Backend
 * timestamps are UTC; when the string carries no timezone
 * marker, treat it as UTC ('Z') so the browser converts to local
 * properly instead of misreading the UTC clock as local.
 */
function _parseUTC(ts: string): number | null {
  let s = String(ts).trim().replace(' ', 'T')
  if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)) s += 'Z'
  const d = new Date(s)
  const ms = d.getTime()
  return isNaN(ms) ? null : ms
}


/**
 * Convert minute count -> human-readable relative label + the
 * tone (green / amber / red) per the 5 / 15 minute thresholds.
 */
function _formatAge(
  ageMinutes: number,
): { relativeLabel: string; tone: 'green' | 'amber' | 'red' } {
  const tone: 'green' | 'amber' | 'red' =
    ageMinutes < 5 ? 'green'
      : ageMinutes < 15 ? 'amber'
        : 'red'
  let relativeLabel: string
  if (ageMinutes === 0) {
    relativeLabel = 'Updated just now'
  } else if (ageMinutes === 1) {
    relativeLabel = 'Updated 1 minute ago'
  } else if (ageMinutes < 60) {
    relativeLabel = `Updated ${ageMinutes} minutes ago`
  } else {
    const hours = Math.floor(ageMinutes / 60)
    const mins  = ageMinutes % 60
    if (hours === 1 && mins === 0) {
      relativeLabel = 'Updated 1 hour ago'
    } else if (mins === 0) {
      relativeLabel = `Updated ${hours} hours ago`
    } else if (hours === 1) {
      relativeLabel = `Updated 1 hour ${mins}m ago`
    } else {
      relativeLabel = `Updated ${hours} hours ${mins}m ago`
    }
  }
  return { relativeLabel, tone }
}


/**
 * Absolute UTC string for the hover tooltip, e.g.
 *   "2026-06-28 23:14:07 UTC"
 * ISO-shape but space-separated + " UTC" suffix so a non-
 * technical reader can parse it at a glance.
 */
function _formatAbsoluteUTC(epochMs: number): string {
  const d = new Date(epochMs)
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-`
    + `${pad(d.getUTCDate())} `
    + `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:`
    + `${pad(d.getUTCSeconds())} UTC`
  )
}


export default RegimeFreshnessBadge
