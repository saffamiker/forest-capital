/**
 * frontend/src/components/SubmissionFreezeBanner.tsx
 *
 * Layer 4 -- visible signal that the submission freeze is active.
 *
 * Two variants:
 *
 *   default  -- full-width info card for the Reports page. Surfaces
 *               freeze date, truncated hash, the safety note that
 *               the live platform continues to update, and a pointer
 *               to admin settings for deactivation.
 *
 *   compact  -- one-line note for the Investment Outlook page, so
 *               the live-signal audience knows that document
 *               generation is locked to a historical snapshot even
 *               while the regime / CIO card / forward projection
 *               update normally.
 *
 * The banner only renders when the freeze is ACTIVE. The endpoint
 * is read once on mount and memoised for 60s; failures fail-open
 * (no banner) so a transient backend hiccup does not flash a
 * confusing "freeze unavailable" state.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import { Snowflake } from 'lucide-react'


export interface SubmissionStatus {
  freeze_active: boolean
  freeze_hash: string | null
  freeze_date: string | null
  current_live_hash: string
  hash_drift: boolean
  frozen_documents: Record<string, {
    generated: boolean
    exported: boolean
    export_verified: boolean | null
    editor_draft_id: string | null
  }>
  submission_ready: boolean
  submission_recommendation: string
}


// In-module memo so a Reports + Investment Outlook mount in the
// same minute does not double-fetch. 60s TTL keeps the panel
// responsive after an admin flip without spamming the endpoint.
const TTL_MS = 60_000
let _cached: { at: number; value: SubmissionStatus | null } | null = null

async function fetchSubmissionStatus(): Promise<SubmissionStatus | null> {
  const now = Date.now()
  if (_cached && (now - _cached.at) < TTL_MS) return _cached.value
  try {
    const res = await axios.get<SubmissionStatus>(
      '/api/v1/admin/submission-status')
    _cached = { at: now, value: res.data }
    return res.data
  } catch {
    // Fail-open: a 401/403/500 means we render nothing rather than
    // a confusing error banner. The freeze either IS active and we
    // show the banner, or we say nothing.
    _cached = { at: now, value: null }
    return null
  }
}


function formatFreezeDate(iso: string | null): string {
  if (!iso) return 'an unknown date'
  // freeze_date is ISO date (YYYY-MM-DD) -- format as "June 30 2026"
  try {
    const d = new Date(iso + 'T00:00:00Z')
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleDateString('en-US', {
      year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
    })
  } catch {
    return iso
  }
}


export interface SubmissionFreezeBannerProps {
  variant?: 'default' | 'compact'
}


export default function SubmissionFreezeBanner(
  { variant = 'default' }: SubmissionFreezeBannerProps,
) {
  const [status, setStatus] = useState<SubmissionStatus | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const s = await fetchSubmissionStatus()
      if (!cancelled) {
        setStatus(s)
        setLoaded(true)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // Render nothing until loaded -- avoids a flash of "no freeze"
  // followed by the banner appearing. Also nothing when the freeze
  // is OFF, which is the common steady state.
  if (!loaded || !status || !status.freeze_active) return null

  const hashFragment = (status.freeze_hash || '').slice(0, 8) || '00000000'
  const freezeDateLabel = formatFreezeDate(status.freeze_date)

  if (variant === 'compact') {
    return (
      <div
        data-testid="submission-freeze-banner-compact"
        className="mx-4 md:mx-6 mt-3 rounded border border-electric/30
                   bg-electric/5 px-3 py-2 flex items-start gap-2
                   text-xs text-electric">
        <Snowflake className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Note: Document generation is frozen to May 2026 data. Live
          signals shown here are current.
        </span>
      </div>
    )
  }

  return (
    <div
      data-testid="submission-freeze-banner"
      className="rounded border border-electric/40 bg-electric/10 px-4 py-3
                 flex items-start gap-3">
      <Snowflake className="w-4 h-4 text-electric shrink-0 mt-0.5" />
      <div className="min-w-0">
        <p className="text-sm font-semibold text-electric">
          Submission freeze active since {freezeDateLabel}
        </p>
        <p className="text-2xs text-electric/80 mt-1">
          Documents locked to data through May 2026 (hash {hashFragment}).
          The live platform continues to update normally. Deactivate
          freeze via admin settings.
        </p>
      </div>
    </div>
  )
}
